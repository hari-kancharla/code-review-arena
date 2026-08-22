"""Aggregate a benchmark pack's runs into a shareable Markdown and JSON report."""

from __future__ import annotations

import json
import warnings
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arena.core.config import REPORT_SCHEMA_VERSION
from arena.core.models import RunResult
from arena.reports.json_report import read_json_report
from arena.reports.leaderboard import leaderboard_eligible
from arena.reports.report_schema import AuditReport

AUDIT_BENCHMARK_SET = "audit_v1"
GAP_THRESHOLD = 0.15
FAILURE_REASON_LABELS = (
    "patch_required_but_missing",
    "patch_apply_failed",
    "structural_validation_failed",
    "tests_failed",
    "execution_inconclusive",
    "no_execution_evidence",
    "localization_failed",
    "detection_failed",
    "incomplete_bug_detection",
    "test_integrity_violation",
    "false_positive",
)


def _pack_label(benchmark_set: str) -> str:
    """Human label for a pack id, e.g. audit_v1 -> 'Audit Pack v1'."""
    if benchmark_set.startswith("audit_v"):
        return f"Audit Pack v{benchmark_set.removeprefix('audit_v')}"
    return benchmark_set


def load_audit_runs(runs_dir: Path, benchmark_set: str = AUDIT_BENCHMARK_SET) -> list[RunResult]:
    runs: list[RunResult] = []
    for path in sorted(runs_dir.glob("*/run.json")):
        try:
            run = read_json_report(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            warnings.warn(f"Skipping malformed run file {path}: {exc}", stacklevel=2)
            continue
        if run.benchmark_set == benchmark_set:
            runs.append(run)
    return runs


def is_publishable(run: RunResult) -> bool:
    """Whether a run may appear in a published report.

    Routed through the same eligibility policy as the file leaderboard and the
    database repository (with ``include_unverified=True``, because a report is an
    inspection surface and trusted-local runs belong in it) so the three consumers
    can never drift. In practice this admits only a ``complete`` schema-v2 run:
    anything the harness itself declared ``invalid``/``partial``/``failed``, and
    any pre-v2 run whose validity simply cannot be known, is not a trustworthy
    measurement and must never supersede one that is.
    """
    return leaderboard_eligible(run, include_unverified=True)


def _format_rate(value: float | None) -> str:
    return f"{value * 100:.1f}%" if value is not None else "n/a"


def _reviewer_label(run: RunResult) -> str:
    return f"{run.reviewer}:{run.model}" if run.model else run.reviewer


def _pack_case_count(benchmark_set: str) -> int:
    """Best-effort case count for a pack, for the empty-state summary; 0 if unknown."""
    from arena.benchmark.case_loader import load_manifest
    from arena.core.config import resolve_benchmark_set

    pack_dir = resolve_benchmark_set(benchmark_set)
    if pack_dir is None:
        return 0
    try:
        return len(load_manifest(pack_dir).cases)
    except Exception:  # noqa: BLE001 - a missing/invalid manifest just means unknown.
        return 0


def build_audit_report_data(
    runs: list[RunResult],
    *,
    benchmark_set: str = AUDIT_BENCHMARK_SET,
    pack_label: str | None = None,
) -> dict[str, Any]:
    pack_label = pack_label or _pack_label(benchmark_set)
    title = f"Detection Is Not Validation: {pack_label} Results"
    # Filter at the point of publication, not only at load, so a caller that
    # assembles runs itself cannot publish a run the harness rejected. Excluded
    # runs are counted into the summary rather than dropped silently.
    submitted_run_count = len(runs)
    runs = [run for run in runs if is_publishable(run)]
    excluded_run_count = submitted_run_count - len(runs)
    if not runs:
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "title": title,
            "generated_at": datetime.now(UTC).isoformat(),
            "empty": True,
            "summary": {
                "benchmark_pack": benchmark_set,
                "run_count": 0,
                "excluded_run_count": excluded_run_count,
                "case_count": _pack_case_count(benchmark_set),
                "reviewers_tested": [],
                "biggest_detection_validation_gap": None,
            },
            "reviewers": [],
            "gaps": [],
            "failure_modes": {},
            "case_studies": [],
            "reproducibility_commands": _reproducibility_commands(benchmark_set),
            "limitations": _limitations(benchmark_set),
        }

    latest: dict[tuple[str, str | None, str], RunResult] = {}
    for run in runs:
        key = (run.reviewer, run.model, run.mode)
        previous = latest.get(key)
        if previous is None or run.completed_at > previous.completed_at:
            latest[key] = run

    reviewer_rows: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    failure_counter: Counter[str] = Counter()
    biggest_gap = 0.0
    biggest_gap_label: str | None = None

    for run in latest.values():
        metrics = run.deterministic_metrics
        detection = metrics.detection_f_beta if metrics else None
        validated = metrics.validated_f_beta if metrics else None
        gap = (detection or 0.0) - (validated or 0.0)
        run_failure_counter: Counter[str] = Counter()
        if gap > biggest_gap:
            biggest_gap = gap
            biggest_gap_label = _reviewer_label(run)
        if detection is not None and validated is not None and gap >= GAP_THRESHOLD:
            gaps.append(
                {
                    "reviewer": run.reviewer,
                    "model": run.model or "",
                    "mode": run.mode,
                    "detection_f_beta": detection,
                    "validated_f_beta": validated,
                    "gap": round(gap, 6),
                    "run_id": run.run_id,
                }
            )
        for case in run.case_results:
            for reason in case.failure_reasons:
                if reason in FAILURE_REASON_LABELS:
                    failure_counter[reason] += 1
                    run_failure_counter[reason] += 1
        reviewer_rows.append(
            {
                "reviewer": run.reviewer,
                "model": run.model or "",
                "mode": run.mode,
                "detection_precision": metrics.detection_precision if metrics else None,
                "detection_recall": metrics.detection_recall if metrics else None,
                "detection_f_beta": detection,
                "validated_precision": metrics.validated_precision if metrics else None,
                "validated_recall": metrics.validated_recall if metrics else None,
                "validated_f_beta": validated,
                "validated_case_rate": metrics.validated_case_rate if metrics else None,
                "complete_repair_rate": metrics.complete_repair_rate if metrics else None,
                "bug_completeness_rate": metrics.bug_completeness_rate if metrics else None,
                "supported_claim_rate": metrics.supported_claim_rate if metrics else None,
                "deterministic_pass_rate": metrics.deterministic_pass_rate if metrics else None,
                "validated_eligible_case_count": (
                    metrics.validated_eligible_case_count if metrics else None
                ),
                "patch_apply_rate": metrics.patch_apply_rate if metrics else None,
                "test_pass_rate": metrics.test_pass_rate if metrics else None,
                "structural_pass_rate": metrics.structural_pass_rate if metrics else None,
                "false_positives_per_case": metrics.false_positives_per_case if metrics else None,
                "cost_per_validated_fix": metrics.cost_per_validated_fix if metrics else None,
                "latency_per_case_ms": metrics.latency_per_case_ms if metrics else None,
                "run_id": run.run_id,
                "primary_failure_mode": (
                    run_failure_counter.most_common(1)[0][0] if run_failure_counter else None
                ),
            }
        )

    def _validated_sort_key(row: dict[str, Any]) -> float:
        value = row.get("validated_case_rate")
        return float(value) if isinstance(value, (int, float)) else -1.0

    reviewer_rows.sort(key=_validated_sort_key, reverse=True)
    gaps.sort(key=lambda row: row["gap"], reverse=True)
    case_studies = _select_case_studies(list(latest.values()))

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "title": title,
        "generated_at": datetime.now(UTC).isoformat(),
        "empty": False,
        "summary": {
            "benchmark_pack": benchmark_set,
            "run_count": len(runs),
            "excluded_run_count": excluded_run_count,
            "case_count": max((run.case_count for run in latest.values()), default=10),
            "reviewers_tested": sorted({_reviewer_label(run) for run in latest.values()}),
            "biggest_detection_validation_gap": (
                {"reviewer": biggest_gap_label, "gap": round(biggest_gap, 6)}
                if biggest_gap_label
                else None
            ),
        },
        "reviewers": reviewer_rows,
        "gaps": gaps,
        "failure_modes": dict(failure_counter),
        "case_studies": case_studies,
        "reproducibility_commands": _reproducibility_commands(benchmark_set),
        "limitations": _limitations(benchmark_set),
    }


def _select_case_studies(runs: list[RunResult]) -> list[dict[str, Any]]:
    candidates: list[tuple[int, dict[str, Any]]] = []
    priority = {
        "structural_validation_failed": 4,
        "tests_failed": 3,
        "patch_apply_failed": 2,
        "patch_required_but_missing": 2,
        "localization_failed": 1,
        "detection_failed": 1,
        "false_positive": 1,
    }
    for run in runs:
        for case in run.case_results:
            if case.deterministic_pass is True:
                continue
            reasons = case.failure_reasons
            if not reasons:
                continue
            top_reason = max(reasons, key=lambda reason: priority.get(reason, 0))
            # Cases that passed validation were already skipped above, so any case
            # here is unvalidated; for a detected-but-unrepaired case, surface its
            # first reported reason.
            detection_only = case.bug_found and case.correct_file and case.correct_line
            if detection_only:
                top_reason = reasons[0]
            finding_summary = ""
            if case.scored_findings:
                finding_summary = case.scored_findings[0].finding.summary
            candidates.append(
                (
                    priority.get(top_reason, 0),
                    {
                        "case_id": case.case_id,
                        "reviewer": run.reviewer,
                        "model": run.model or "",
                        "finding_summary": finding_summary,
                        "failure_reasons": reasons,
                        "validator_evidence": [
                            {
                                "name": item.get("name"),
                                "passed": item.get("passed"),
                                "message": item.get("message"),
                            }
                            for item in case.validator_results[:2]
                        ],
                        "test_stderr_tail": case.test_stderr_tail[-400:]
                        if case.test_stderr_tail
                        else "",
                    },
                )
            )
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in candidates[:3]]


def _reproducibility_commands(benchmark_set: str = AUDIT_BENCHMARK_SET) -> list[str]:
    pack = f"benchmark_sets/{benchmark_set}"
    # audit_v1 carries the keyword_gamer control; other packs use the generic
    # shallow-patch adversarial baseline, which needs no per-case configuration.
    adversarial = (
        f"arena run {pack} --reviewer control:keyword_gamer --mode full --allow-local-execution"
        if benchmark_set == AUDIT_BENCHMARK_SET
        else f"arena run {pack} --reviewer shallow-patch --mode full --allow-local-execution"
    )
    return [
        f"arena validate {pack}",
        f"arena run {pack} --reviewer reference-patch --mode full --allow-local-execution",
        adversarial,
        "arena leaderboard runs/ --metric validated_case_rate --beta 1.0 --include-unverified",
        f"arena audit-report runs/ --output docs/reports/{benchmark_set}-results.md",
    ]


def _limitations(benchmark_set: str = AUDIT_BENCHMARK_SET) -> list[str]:
    return [
        f"{benchmark_set} is curated and small; it measures a narrow set of patch-backed failures.",
        "Structural validators are hand-authored and may reject alternate but valid repairs.",
        "Regression tests cannot prove full production correctness.",
        "Some credible fixes may fail when validators are intentionally narrow.",
        "This pack is not designed to compete with large public leaderboard scale benchmarks.",
    ]


def render_audit_report_markdown(data: dict[str, Any]) -> str:
    lines = [f"# {data['title']}", ""]
    if data.get("empty"):
        pack = data["summary"]["benchmark_pack"]
        lines.extend(
            [
                f"_No {pack} runs were found under the requested runs directory._",
                "",
                "## Reproducibility",
                "",
            ]
        )
        lines.extend(f"- `{command}`" for command in data["reproducibility_commands"])
        lines.extend(["", "## Limitations", ""])
        lines.extend(f"- {item}" for item in data["limitations"])
        return "\n".join(lines) + "\n"

    summary = data["summary"]
    lines.extend(
        [
            "## 1. Summary",
            "",
            f"- Benchmark pack: `{summary['benchmark_pack']}`",
            f"- Runs discovered: {summary['run_count']}",
            f"- Cases per run: {summary['case_count']}",
            f"- Reviewers tested: {', '.join(summary['reviewers_tested']) or 'none'}",
        ]
    )
    gap = summary.get("biggest_detection_validation_gap")
    if gap:
        lines.append(
            f"- Biggest detection-validation gap: `{gap['reviewer']}` "
            f"(detection_f_beta - validated_f_beta = {gap['gap']:.3f})"
        )
    lines.extend(["", "## 2. Methodology", ""])
    lines.extend(
        [
            "- Ground truth stays hidden from reviewer prompts and custom-command JSON.",
            "- Reviewers receive the PR diff, relevant files, and optional test/static output.",
            "- Full mode applies `suggested_patch`, runs regression tests, and structural validators.",
            "- `validated_case_rate` is the primary full/patch metric: validated cases over "
            "eligible cases.",
            "- `detection_f_beta` scores localization only; the gap below contrasts it with "
            "the deprecated `validated_f_beta`.",
            "",
            "## 3. Reviewer Comparison",
            "",
            "| Reviewer | Model | Mode | Detection F-beta | Validated Case Rate | "
            "Deterministic Pass Rate | Patch Apply Rate | Test Pass Rate | "
            "Structural Pass Rate | False Positives / Case | Cost / Validated Fix | Latency / Case |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in data["reviewers"]:
        lines.append(
            f"| {row['reviewer']} | {row['model']} | {row['mode']} | "
            f"{_metric_cell(row.get('detection_f_beta'))} | {_format_rate(row.get('validated_case_rate'))} | "
            f"{_format_rate(row.get('deterministic_pass_rate'))} | "
            f"{_format_rate(row.get('patch_apply_rate'))} | "
            f"{_format_rate(row.get('test_pass_rate'))} | "
            f"{_format_rate(row.get('structural_pass_rate'))} | "
            f"{row.get('false_positives_per_case', 'n/a')} | "
            f"{row.get('cost_per_validated_fix', 'n/a')} | "
            f"{row.get('latency_per_case_ms', 'n/a')} |"
        )
    lines.extend(["", "## 4. Detection vs Validation Gap", ""])
    if data["gaps"]:
        for gap in data["gaps"]:
            lines.append(
                f"- `{gap['reviewer']}:{gap['model']}` ({gap['mode']}): detection={gap['detection_f_beta']:.3f}, "
                f"validated={gap['validated_f_beta']:.3f}, gap={gap['gap']:.3f} "
                f"(run `{gap['run_id']}`)"
            )
    else:
        lines.append("- No runs exceeded the configured detection-validation gap threshold.")
    lines.extend(["", "## 5. Failure Mode Breakdown", ""])
    if data["failure_modes"]:
        for reason, count in sorted(data["failure_modes"].items(), key=lambda item: -item[1]):
            lines.append(f"- `{reason}`: {count}")
    else:
        lines.append("- No recorded failure reasons.")
    lines.extend(["", "## 6. Case Studies", ""])
    if data["case_studies"]:
        for study in data["case_studies"]:
            lines.append(f"### {study['case_id']} ({study['reviewer']}:{study['model']})")
            if study.get("finding_summary"):
                lines.append(f"- Finding: {study['finding_summary']}")
            lines.append(f"- Failure reasons: {', '.join(study['failure_reasons'])}")
            if study.get("validator_evidence"):
                lines.append("- Validator evidence:")
                for item in study["validator_evidence"]:
                    lines.append(f"  - `{item['name']}`: {item['message']}")
            if study.get("test_stderr_tail"):
                lines.append(f"- Test output tail: `{study['test_stderr_tail']}`")
            lines.append("")
    else:
        lines.append("- No failing case studies were available.")
    lines.extend(["", "## 7. Reproducibility", ""])
    lines.extend(f"- `{command}`" for command in data["reproducibility_commands"])
    lines.extend(["", "## 8. Limitations", ""])
    lines.extend(f"- {item}" for item in data["limitations"])
    return "\n".join(lines) + "\n"


def _metric_cell(value: float | None) -> str:
    return f"{value:.3f}" if value is not None else "n/a"


def write_audit_report(
    runs_dir: Path,
    output_path: Path,
    json_output_path: Path | None = None,
    *,
    benchmark_set: str = AUDIT_BENCHMARK_SET,
) -> dict[str, Any]:
    data = build_audit_report_data(
        load_audit_runs(runs_dir, benchmark_set), benchmark_set=benchmark_set
    )
    # Validate the contract before writing so a producer-side drift fails loudly here
    # rather than silently in the dashboard.
    AuditReport.model_validate(data)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_audit_report_markdown(data), encoding="utf-8")
    if json_output_path is not None:
        json_output_path.parent.mkdir(parents=True, exist_ok=True)
        json_output_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data
