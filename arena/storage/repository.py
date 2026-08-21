"""Persistence operations for run results and API reads."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from arena.core.models import RunResult
from arena.reports.leaderboard import eligibility_from_fields
from arena.storage.db import connect


class RunRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def save(self, run: RunResult) -> None:
        metrics = run.deterministic_metrics
        with connect(self.db_path) as db:
            db.execute(
                """INSERT OR REPLACE INTO runs
                   (id, reviewer, model, benchmark_set, started_at, completed_at,
                    total_score, total_cost, total_latency_ms, beta, deterministic_precision,
                    deterministic_recall, deterministic_f1, deterministic_f_beta,
                    detection_precision, detection_recall, detection_f1, detection_f_beta,
                    validated_precision, validated_recall, validated_f1, validated_f_beta,
                    deterministic_pass_rate,
                    patch_apply_rate, test_pass_rate, structural_pass_rate,
                    false_positives_per_case, cost_per_true_positive, cost_per_validated_fix,
                    latency_per_case_ms,
                    schema_version, run_status, execution_backend, eligible_case_count,
                    completed_case_count, failed_case_count, skipped_case_count, coverage_rate,
                    validated_case_rate,
                    run_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           ?)""",
                (
                    run.run_id,
                    run.reviewer,
                    run.model,
                    run.benchmark_set,
                    run.started_at.isoformat(),
                    run.completed_at.isoformat(),
                    run.total_score,
                    run.total_cost,
                    run.total_latency_ms,
                    run.beta,
                    metrics.detection_precision if metrics else None,
                    metrics.detection_recall if metrics else None,
                    metrics.detection_f1 if metrics else None,
                    metrics.detection_f_beta if metrics else None,
                    metrics.detection_precision if metrics else None,
                    metrics.detection_recall if metrics else None,
                    metrics.detection_f1 if metrics else None,
                    metrics.detection_f_beta if metrics else None,
                    metrics.validated_precision if metrics else None,
                    metrics.validated_recall if metrics else None,
                    metrics.validated_f1 if metrics else None,
                    metrics.validated_f_beta if metrics else None,
                    metrics.deterministic_pass_rate if metrics else None,
                    metrics.patch_apply_rate if metrics else None,
                    metrics.test_pass_rate if metrics else None,
                    metrics.structural_pass_rate if metrics else None,
                    metrics.false_positives_per_case if metrics else None,
                    metrics.cost_per_validated_fix if metrics else None,
                    metrics.cost_per_validated_fix if metrics else None,
                    metrics.latency_per_case_ms if metrics else None,
                    run.schema_version,
                    run.run_status,
                    run.execution_backend,
                    run.eligible_case_count,
                    run.completed_case_count,
                    run.failed_case_count,
                    run.skipped_case_count,
                    run.coverage_rate,
                    metrics.validated_case_rate if metrics else None,
                    json.dumps(run.model_dump(mode="json")),
                ),
            )
            db.execute("DELETE FROM case_results WHERE run_id = ?", (run.run_id,))
            for case in run.case_results:
                case_result_id = str(uuid.uuid4())
                parsed = (
                    json.dumps(case.response.parsed_response.model_dump())
                    if case.response.parsed_response
                    else None
                )
                db.execute(
                    """INSERT INTO case_results
                   (id, run_id, case_id, score, bug_found, correct_file, correct_line,
                        false_positive_count, deterministic_pass, patch_provided, patch_applied,
                        tests_ran, tests_passed, structural_validation_ran,
                        structural_validation_passed, failure_reasons_json, patch_error,
                        raw_response, parsed_response)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        case_result_id,
                        run.run_id,
                        case.case_id,
                        case.score,
                        case.bug_found,
                        case.correct_file,
                        case.correct_line,
                        case.false_positive_count,
                        case.deterministic_pass,
                        case.patch_provided,
                        case.patch_applied,
                        case.tests_ran,
                        case.tests_passed,
                        bool(case.validators_run),
                        case.validators_passed,
                        json.dumps(case.failure_reasons),
                        case.patch_error,
                        case.response.raw_response,
                        parsed,
                    ),
                )
                for item in case.scored_findings:
                    finding = item.finding
                    db.execute(
                        """INSERT INTO findings
                           (id, case_result_id, title, category, severity, file, line_start,
                            line_end, summary, suggested_fix, confidence, is_true_positive)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            str(uuid.uuid4()),
                            case_result_id,
                            finding.title,
                            finding.category,
                            finding.severity,
                            finding.file,
                            finding.line_start,
                            finding.line_end,
                            finding.summary,
                            finding.suggested_fix or "",
                            finding.confidence,
                            item.is_true_positive,
                        ),
                    )
                for validator in case.validator_results:
                    db.execute(
                        """INSERT INTO validator_results
                           (id, case_result_id, validator_name, passed, confidence, message,
                            evidence_json, error)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            str(uuid.uuid4()),
                            case_result_id,
                            validator["name"],
                            validator["passed"],
                            validator["confidence"],
                            validator["message"],
                            json.dumps(validator["evidence"]),
                            validator.get("error"),
                        ),
                    )

    def list_runs(self) -> list[dict[str, object]]:
        with connect(self.db_path) as db:
            rows = db.execute(
                "SELECT id, reviewer, model, benchmark_set, total_score, total_cost, "
                "total_latency_ms, completed_at, beta, deterministic_precision, "
                "deterministic_recall, deterministic_f1, deterministic_f_beta, patch_apply_rate, "
                "test_pass_rate, structural_pass_rate, false_positives_per_case, "
                "cost_per_true_positive, detection_precision, detection_recall, detection_f1, "
                "detection_f_beta, validated_precision, validated_recall, validated_f1, "
                "validated_f_beta, validated_case_rate, deterministic_pass_rate, "
                "cost_per_validated_fix, "
                "latency_per_case_ms FROM runs ORDER BY completed_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, run_id: str) -> RunResult | None:
        with connect(self.db_path) as db:
            row = db.execute("SELECT run_json FROM runs WHERE id = ?", (run_id,)).fetchone()
        return RunResult.model_validate_json(row["run_json"]) if row else None

    def leaderboard(self, *, include_unverified: bool = False) -> list[dict[str, object]]:
        # Reads the stored run JSON directly rather than rebuilding the full nested
        # RunResult/CaseResult object graph: a leaderboard row is a summary, so this
        # stays responsive with hundreds of runs.
        with connect(self.db_path) as db:
            rows = db.execute("SELECT run_json FROM runs ORDER BY completed_at DESC").fetchall()
        latest: dict[tuple[str, str, str | None, str], dict[str, Any]] = {}
        history: dict[tuple[str, str, str | None, str], int] = {}
        for row in rows:
            data: dict[str, Any] = json.loads(row["run_json"])
            # Shared eligibility policy (same as leaderboard_rows / leaderboard_eligible):
            # complete v2, Docker-backed, full coverage, externally verified pack. Runs
            # that fall short are preserved in storage but never ranked by default.
            metadata = data.get("metadata") or {}
            if not eligibility_from_fields(
                schema_version=data.get("schema_version", 1),
                run_status=data.get("run_status", "complete"),
                execution_backend=data.get("execution_backend", "none"),
                coverage_rate=data.get("coverage_rate", 1.0),
                pack_digest_externally_verified=bool(
                    metadata.get("pack_digest_externally_verified", False)
                ),
                non_exact_output_used=metadata.get("non_exact_output_used"),
                include_unverified=include_unverified,
            ):
                continue
            # Include the pack: the same reviewer/model/mode on different packs are
            # distinct results and must not overwrite each other.
            key = (
                data["benchmark_set"],
                data["reviewer"],
                data.get("model"),
                data.get("mode", "review"),
            )
            history[key] = history.get(key, 0) + 1
            latest.setdefault(key, data)

        summaries: list[dict[str, object]] = []
        for key, data in latest.items():
            case_results = data.get("case_results") or []
            metrics = data.get("deterministic_metrics")
            summaries.append(
                {
                    "reviewer": data["reviewer"],
                    "model": data.get("model") or "",
                    "mode": data.get("mode", "review"),
                    "benchmark_set": data["benchmark_set"],
                    "score": data["total_score"],
                    "bugs_found": data["bugs_found"],
                    "case_count": len(case_results),
                    "false_positives": data["false_positives"],
                    "cost": data["total_cost"],
                    "latency_ms": data["total_latency_ms"],
                    "run_id": data["run_id"],
                    "history_count": history[key],
                    "completed_at": data["completed_at"],
                    "deterministic_passes": sum(
                        case.get("deterministic_pass") is True for case in case_results
                    ),
                    "deterministic_metrics": metrics,
                    # Whether this row would survive the DEFAULT (comparable) policy.
                    # With include_unverified the result set mixes both, so the row has
                    # to carry its own status or a consumer cannot label it honestly.
                    "verified": eligibility_from_fields(
                        schema_version=data.get("schema_version", 1),
                        run_status=data.get("run_status", "complete"),
                        execution_backend=data.get("execution_backend", "none"),
                        coverage_rate=data.get("coverage_rate", 1.0),
                        pack_digest_externally_verified=bool(
                            (data.get("metadata") or {}).get(
                                "pack_digest_externally_verified", False
                            )
                        ),
                        non_exact_output_used=(data.get("metadata") or {}).get(
                            "non_exact_output_used"
                        ),
                        include_unverified=False,
                    ),
                }
            )

        def _validated(summary: dict[str, object]) -> float:
            metrics = summary["deterministic_metrics"]
            value = metrics.get("validated_case_rate") if isinstance(metrics, dict) else None
            return value if isinstance(value, (int, float)) else -1.0

        summaries.sort(key=_validated, reverse=True)
        return summaries
