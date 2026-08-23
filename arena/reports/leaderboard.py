"""Run report aggregation."""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict

from arena.core.models import RunResult
from arena.reports.json_report import read_json_report
from arena.scoring.deterministic_scorer import aggregate_deterministic_metrics


class LeaderboardRow(TypedDict):
    reviewer: str
    model: str
    score: float
    bugs_found: str
    false_positives: int
    cost: float
    latency_ms: int
    run_id: str
    mode: str
    completed_at: str
    metric_value: float | None
    # Wilson 95% CI, present only for validated_case_rate / deterministic_pass_rate.
    metric_ci: tuple[float, float] | None
    pack: str


def load_runs(runs_dir: Path) -> list[RunResult]:
    paths = sorted(runs_dir.glob("*/run.json"))
    return [read_json_report(path) for path in paths]


def run_integrity_admissible(*, schema_version: int, run_status: str) -> bool:
    """The trust floor shared by every consumer that publishes run results.

    A run below this floor is not a measurement at all: either it predates the v2
    schema, or the harness itself stamped it non-complete (a tampered pack, a run
    that produced nothing, or one that needed execution it never got). Unlike the
    comparability rules in ``eligibility_from_fields``, this floor is absolute --
    ``--include-unverified`` relaxes comparability, never integrity. Report
    builders share it so a run the leaderboard refuses to rank can never be
    promoted into a published artifact instead.
    """
    return schema_version >= 2 and run_status == "complete"


def eligibility_from_fields(
    *,
    schema_version: int,
    run_status: str,
    execution_backend: str,
    coverage_rate: float,
    pack_digest_externally_verified: bool,
    non_exact_output_used: bool | None = None,
    reviewer_oracle_reachable: bool | None = None,
    include_unverified: bool = False,
) -> bool:
    """The single leaderboard-eligibility policy, in terms of plain fields.

    Shared by the file/report leaderboard (RunResult objects) and the database
    repository / API (stored run JSON), so they never drift. A verified run is
    one whose result can be trusted: complete v2, run in Docker (not on the host),
    full coverage, a pack whose content matched a digest supplied out of band
    (--expected-pack-sha256), AND exact reviewer output (Arena did not reinterpret
    any case). The pack's own pack.sha256 is NOT sufficient -- it lives inside the
    pack, so an edited pack with a regenerated hash still passes self-consistency.
    ``non_exact_output_used`` is False only when every case was exact or invalid;
    True (salvage used) and None (old run, exactness unknown) are not default
    comparable. Anything short is inspectable only with include_unverified.
    """
    if not run_integrity_admissible(schema_version=schema_version, run_status=run_status):
        return False
    if include_unverified:
        return True
    return (
        execution_backend == "docker"
        and coverage_rate == 1.0
        and pack_digest_externally_verified is True
        and non_exact_output_used is False
        # A reviewer that could read the pack's answer key was not measured
        # blind: a wrapper that echoes reference.patch scores a perfect 1.000 on
        # every metric. Unknown (None, an older run) is treated as unsafe.
        and reviewer_oracle_reachable is False
    )


def leaderboard_eligible(run: RunResult, *, include_unverified: bool = False) -> bool:
    """Whether a RunResult is comparable on the default leaderboard."""
    return eligibility_from_fields(
        schema_version=run.schema_version,
        run_status=run.run_status,
        execution_backend=run.execution_backend,
        coverage_rate=run.coverage_rate,
        pack_digest_externally_verified=run.metadata.pack_digest_externally_verified,
        non_exact_output_used=run.metadata.non_exact_output_used,
        reviewer_oracle_reachable=run.metadata.reviewer_oracle_reachable,
        include_unverified=include_unverified,
    )


def leaderboard_rows(
    runs_dir: Path,
    metric: str = "validated_case_rate",
    beta: float = 1.0,
    include_unverified: bool = False,
) -> list[LeaderboardRow]:
    latest: dict[tuple[str, str, str | None, str], RunResult] = {}
    for run in load_runs(runs_dir):
        if not leaderboard_eligible(run, include_unverified=include_unverified):
            continue
        # The pack is part of the identity: the same reviewer/model/mode measured
        # on different packs are different results and must not overwrite each
        # other (audit_v1 vs audit_v2 are not comparable).
        key = (run.benchmark_set, run.reviewer, run.model, run.mode)
        previous = latest.get(key)
        if previous is None or run.completed_at > previous.completed_at:
            latest[key] = run
    rows: list[LeaderboardRow] = [
        {
            "reviewer": run.reviewer,
            "model": run.model or "",
            "score": run.total_score,
            "bugs_found": f"{run.bugs_found}/{run.case_count}",
            "false_positives": run.false_positives,
            "cost": run.total_cost,
            "latency_ms": run.total_latency_ms,
            "run_id": run.run_id,
            "mode": run.mode,
            "completed_at": run.completed_at.isoformat(),
            "metric_value": _metric(run, metric, beta),
            "metric_ci": _metric_ci(run, metric),
            "pack": _pack_label(run),
        }
        for run in latest.values()
    ]
    descending = metric not in {
        "cost_per_validated_fix",
        "false_positives_per_case",
        "latency_per_case_ms",
    }
    return sorted(
        rows,
        key=lambda item: _sort_key(item["metric_value"], descending),
    )


def _metric_ci(run: RunResult, metric: str) -> tuple[float, float] | None:
    """The validated_case_rate confidence interval, for the case-rate metrics only."""
    metrics = run.deterministic_metrics
    if metrics is None or metric not in {"validated_case_rate", "deterministic_pass_rate"}:
        return None
    low, high = metrics.validated_case_rate_ci_low, metrics.validated_case_rate_ci_high
    return (low, high) if low is not None and high is not None else None


def _pack_label(run: RunResult) -> str:
    checksum = run.metadata.pack_checksum
    if not checksum:
        return run.benchmark_set
    suffix = " (tampered!)" if run.metadata.pack_checksum_verified is False else ""
    return f"{run.benchmark_set}@{checksum[:10]}{suffix}"


def _sort_key(value: float | None, descending: bool) -> tuple[bool, float]:
    if value is None:
        return (True, 0.0)
    return (False, -value if descending else value)


def _metric(run: RunResult, metric: str, beta: float) -> float | None:
    if metric in {"score", "review_quality_score"}:
        return run.total_score
    if run.deterministic_metrics is None:
        return None
    metric = "detection_f_beta" if metric == "f_beta" else metric
    metrics = (
        aggregate_deterministic_metrics(
            run.case_results, beta, run.total_cost, run.total_latency_ms
        )
        if metric in {"detection_f_beta", "validated_f_beta"}
        else run.deterministic_metrics
    )
    valid = {
        "detection_f_beta",
        "detection_f1",
        "detection_precision",
        "detection_recall",
        "validated_f_beta",
        "validated_f1",
        "validated_precision",
        "validated_recall",
        "validated_case_rate",
        "complete_repair_rate",
        "bug_completeness_rate",
        "supported_claim_rate",
        "deterministic_pass_rate",
        "patch_apply_rate",
        "test_pass_rate",
        "structural_pass_rate",
        "false_positives_per_case",
        "cost_per_validated_fix",
        "latency_per_case_ms",
    }
    if metric not in valid:
        raise ValueError(f"Unsupported leaderboard metric: {metric}")
    values = {
        "detection_f_beta": metrics.detection_f_beta,
        "detection_f1": metrics.detection_f1,
        "detection_precision": metrics.detection_precision,
        "detection_recall": metrics.detection_recall,
        "validated_f_beta": metrics.validated_f_beta,
        "validated_f1": metrics.validated_f1,
        "validated_precision": metrics.validated_precision,
        "validated_recall": metrics.validated_recall,
        "validated_case_rate": metrics.validated_case_rate,
        "complete_repair_rate": metrics.complete_repair_rate,
        "bug_completeness_rate": metrics.bug_completeness_rate,
        "supported_claim_rate": metrics.supported_claim_rate,
        "deterministic_pass_rate": metrics.deterministic_pass_rate,
        "patch_apply_rate": metrics.patch_apply_rate,
        "test_pass_rate": metrics.test_pass_rate,
        "structural_pass_rate": metrics.structural_pass_rate,
        "false_positives_per_case": metrics.false_positives_per_case,
        "cost_per_validated_fix": metrics.cost_per_validated_fix,
        "latency_per_case_ms": metrics.latency_per_case_ms,
    }
    return values[metric]
