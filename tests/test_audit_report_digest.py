"""Audit-report rows surface the reviewer image digest recorded on runs."""

from datetime import datetime

from arena.core.models import RunMetadata, RunResult
from arena.reports.audit_report import build_audit_report_data


def _digest_run(run_id: str, reviewer: str, digest: str | None) -> RunResult:
    return RunResult(
        run_id=run_id,
        benchmark_set="audit_v1",
        reviewer=reviewer,
        model=None,
        started_at=datetime.now(),
        completed_at=datetime.now(),
        metadata=RunMetadata(
            prompt_version="v2",
            benchmark_version="1",
            reviewer_oracle_reachable=False,
            reviewer_image_digest=digest,
        ),
        case_results=[],
        total_score=0.0,
        bugs_found=0,
        correct_files=0,
        correct_lines=0,
        false_positives=0,
        total_cost=0.0,
        total_latency_ms=0,
        schema_version=2,
        run_status="complete",
        mode="full",
    )


def test_audit_report_row_records_reviewer_image_digest():
    run = _digest_run("r-digest", "docker-command", "img@sha256:" + "a" * 64)
    data = build_audit_report_data([run], benchmark_set="audit_v1")
    (row,) = [row for row in data["reviewers"] if row["run_id"] == "r-digest"]
    assert row["reviewer_image_digest"] == "img@sha256:" + "a" * 64


def test_audit_report_row_leaves_digest_none_on_legacy_runs():
    run = _digest_run("r-legacy", "reference-patch", None)
    data = build_audit_report_data([run], benchmark_set="audit_v1")
    (row,) = [row for row in data["reviewers"] if row["run_id"] == "r-legacy"]
    assert row["reviewer_image_digest"] is None
