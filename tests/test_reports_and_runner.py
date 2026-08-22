from pathlib import Path

import pytest

from arena.benchmark.benchmark_runner import _reserve_run_dir, run_benchmark
from arena.reports.audit_report import build_audit_report_data
from arena.reports.html_report import render_html
from arena.reports.json_report import read_json_report
from arena.reports.leaderboard import leaderboard_rows
from arena.reports.markdown_report import render_markdown
from arena.reviewers.base import BaseReviewer
from arena.reviewers.controls import ControlReviewer
from tests.conftest import pack_case_count


@pytest.fixture
def audit_benchmark_dir() -> Path:
    return Path("benchmark_sets/audit_v1")


class _RaisesOnOneCase(BaseReviewer):
    """Otherwise-perfect reviewer that raises on a single case id."""

    name = "mock"
    model = "raises_one"

    def __init__(self, bad_case_id: str) -> None:
        self._inner = ControlReviewer("perfect_patch")
        self._bad_case_id = bad_case_id

    def review(self, context):  # type: ignore[no-untyped-def]
        if context.case.id == self._bad_case_id:
            raise RuntimeError("boom while reviewing")
        return self._inner.review(context)


def test_run_directory_reservation_never_reuses_an_existing_path(tmp_path):
    first_id, first_dir = _reserve_run_dir(tmp_path)
    second_id, second_dir = _reserve_run_dir(tmp_path)
    assert first_dir.exists()
    assert second_dir.exists()
    assert first_id != second_id


def test_runner_generates_reports_and_storage(benchmark_dir, tmp_path):
    run = run_benchmark(
        benchmark_dir,
        ControlReviewer("perfect"),
        output_dir=tmp_path / "runs",
        db_path=tmp_path / "arena.db",
    )
    run_dir = tmp_path / "runs" / run.run_id
    assert run.total_score == 100
    assert (run_dir / "run.json").exists()
    assert (run_dir / "report.md").exists()
    assert (run_dir / "report.html").exists()
    expected = pack_case_count()
    assert read_json_report(run_dir / "run.json").bugs_found == expected
    assert f"Bugs Found | {expected}/{expected}" in render_markdown(run)
    assert "## False Positive Summary" in render_markdown(run)
    assert "## Missed Bug Summary" in render_markdown(run)
    assert "Case Traces" in render_html(run)
    assert "Scoring breakdown" in render_html(run)
    assert run.deterministic_metrics is None
    assert not [item for item in run.case_results if item.test_output]


def test_full_patch_run_generates_deterministic_reports(benchmark_dir, tmp_path):
    run = run_benchmark(
        benchmark_dir,
        ControlReviewer("perfect_patch"),
        output_dir=tmp_path / "runs",
        db_path=tmp_path / "arena.db",
        mode="full",
        allow_local_execution=True,
    )
    assert run.deterministic_metrics is not None
    assert run.deterministic_metrics.detection_f_beta == 1
    assert run.deterministic_metrics.validated_f_beta == 1
    assert run.deterministic_metrics.deterministic_pass_rate == 1
    assert run.deterministic_metrics.patch_apply_rate == 1
    required = {
        "fastapi_auth_bypass_001",
        "kafka_idempotency_bug_001",
        "redis_cache_key_collision_001",
        "sql_permission_leak_001",
        "rag_fake_citation_001",
    }
    assert all(
        result.deterministic_pass for result in run.case_results if result.case_id in required
    )
    markdown = render_markdown(run)
    assert "## Deterministic Validation Summary" in markdown
    assert "Suggested Patch:" in markdown
    assert "Structural Pass Rate" in markdown


def _run_patch_mode(benchmark_dir, tmp_path, mode: str):
    return run_benchmark(
        benchmark_dir,
        ControlReviewer(mode),
        output_dir=tmp_path / "runs",
        db_path=tmp_path / "arena.db",
        mode="full",
        allow_local_execution=True,
    )


def test_validated_case_rate_is_the_coherent_default_metric(benchmark_dir, tmp_path):
    perfect = _run_patch_mode(benchmark_dir, tmp_path, "perfect_patch")
    missing = _run_patch_mode(benchmark_dir, tmp_path, "detects_no_patch")
    assert perfect.deterministic_metrics is not None
    assert missing.deterministic_metrics is not None

    # Unit-coherent case-level metric, equal to the legacy pass rate by construction.
    assert perfect.deterministic_metrics.validated_case_rate == 1.0
    assert missing.deterministic_metrics.validated_case_rate == 0.0
    assert (
        perfect.deterministic_metrics.validated_case_rate
        == perfect.deterministic_metrics.deterministic_pass_rate
    )

    # leaderboard_rows now defaults to validated_case_rate.
    ranked = leaderboard_rows(tmp_path / "runs", include_unverified=True)
    by_model = {row["model"]: row["metric_value"] for row in ranked}
    assert ranked[0]["model"] == "perfect_patch"
    assert by_model["perfect_patch"] == 1.0
    assert by_model["detects_no_patch"] == 0.0


def test_validated_metrics_distinguish_detection_from_a_valid_fix(benchmark_dir, tmp_path):
    perfect = _run_patch_mode(benchmark_dir, tmp_path, "perfect_patch")
    bad = _run_patch_mode(benchmark_dir, tmp_path, "bad_patch")
    missing = _run_patch_mode(benchmark_dir, tmp_path, "detects_no_patch")
    malformed = _run_patch_mode(benchmark_dir, tmp_path, "malformed_patch")

    assert perfect.deterministic_metrics is not None
    assert bad.deterministic_metrics is not None
    assert missing.deterministic_metrics is not None
    assert malformed.deterministic_metrics is not None
    assert perfect.deterministic_metrics.validated_f_beta == 1
    assert bad.deterministic_metrics.detection_f_beta == 1
    assert bad.deterministic_metrics.validated_f_beta < 1
    assert bad.deterministic_metrics.deterministic_pass_rate == 0.0
    assert missing.deterministic_metrics.detection_f_beta == 1
    assert missing.deterministic_metrics.validated_f_beta == 0
    assert malformed.deterministic_metrics.detection_f_beta == 1
    assert malformed.deterministic_metrics.validated_f_beta == 0
    assert malformed.deterministic_metrics.patch_apply_rate == 0


def test_leaderboard_supports_the_validated_f_beta_metric(benchmark_dir, tmp_path):
    for mode in ("bad_patch", "malformed_patch", "detects_no_patch", "perfect_patch"):
        _run_patch_mode(benchmark_dir, tmp_path, mode)

    validated = leaderboard_rows(
        tmp_path / "runs", metric="validated_f_beta", beta=1.0, include_unverified=True
    )
    metrics_by_model = {row["model"]: row["metric_value"] for row in validated}
    assert validated[0]["model"] == "perfect_patch"
    assert metrics_by_model["perfect_patch"] == 1
    assert metrics_by_model["bad_patch"] < metrics_by_model["perfect_patch"]
    assert metrics_by_model["detects_no_patch"] == 0
    assert metrics_by_model["malformed_patch"] == 0

    detection = leaderboard_rows(
        tmp_path / "runs", metric="detection_f_beta", beta=1.0, include_unverified=True
    )
    detection_by_model = {row["model"]: row["metric_value"] for row in detection}
    assert detection_by_model["perfect_patch"] == 1
    assert detection_by_model["bad_patch"] == 1
    assert detection_by_model["detects_no_patch"] == 1
    assert detection_by_model["malformed_patch"] == 1

    legacy_alias = leaderboard_rows(
        tmp_path / "runs", metric="f_beta", beta=1.0, include_unverified=True
    )
    alias_by_model = {row["model"]: row["metric_value"] for row in legacy_alias}
    assert alias_by_model == detection_by_model

    review_only = run_benchmark(
        benchmark_dir,
        ControlReviewer("perfect"),
        output_dir=tmp_path / "runs",
        db_path=tmp_path / "arena.db",
    )
    assert review_only.deterministic_metrics is None
    cost_ranked = leaderboard_rows(
        tmp_path / "runs", metric="cost_per_validated_fix", include_unverified=True
    )
    assert cost_ranked[-1]["model"] == "perfect"
    assert cost_ranked[-1]["metric_value"] is None


def _run_audit_patch_mode(audit_benchmark_dir: Path, tmp_path, mode: str):
    return run_benchmark(
        audit_benchmark_dir,
        ControlReviewer(mode),
        output_dir=tmp_path / "audit_runs",
        db_path=tmp_path / "audit.db",
        mode="full",
        allow_local_execution=True,
    )


def test_keyword_gamer_scores_detection_without_validation(audit_benchmark_dir, tmp_path):
    run = _run_audit_patch_mode(audit_benchmark_dir, tmp_path, "keyword_gamer")
    metrics = run.deterministic_metrics
    assert metrics is not None
    assert metrics.detection_f_beta == 1
    assert metrics.validated_f_beta == 0
    assert metrics.deterministic_pass_rate == 0
    assert all(not result.deterministic_pass for result in run.case_results)
    failure_reasons = {reason for result in run.case_results for reason in result.failure_reasons}
    assert failure_reasons & {
        "structural_validation_failed",
        "tests_failed",
        "patch_apply_failed",
    }


def test_leaderboard_ranks_perfect_patch_above_keyword_gamer(audit_benchmark_dir, tmp_path):
    _run_audit_patch_mode(audit_benchmark_dir, tmp_path, "keyword_gamer")
    _run_audit_patch_mode(audit_benchmark_dir, tmp_path, "perfect_patch")

    validated = leaderboard_rows(
        tmp_path / "audit_runs", metric="validated_f_beta", beta=1.0, include_unverified=True
    )
    metrics_by_model = {row["model"]: row["metric_value"] for row in validated}
    assert validated[0]["model"] == "perfect_patch"
    assert metrics_by_model["perfect_patch"] == 1
    assert metrics_by_model["keyword_gamer"] == 0

    detection = leaderboard_rows(
        tmp_path / "audit_runs", metric="detection_f_beta", beta=1.0, include_unverified=True
    )
    detection_by_model = {row["model"]: row["metric_value"] for row in detection}
    assert detection_by_model["keyword_gamer"] == 1


def test_single_case_failure_does_not_abort_the_batch(audit_benchmark_dir, tmp_path):
    bad_case_id = "security_jwt_audience_validation_001"
    run = run_benchmark(
        audit_benchmark_dir,
        _RaisesOnOneCase(bad_case_id),
        output_dir=tmp_path / "runs",
        db_path=tmp_path / "arena.db",
        mode="full",
        allow_local_execution=True,
    )
    # The batch completes with every case represented, including the one that raised.
    assert run.case_count == 10
    assert (tmp_path / "runs" / run.run_id / "run.json").exists()

    failed = next(item for item in run.case_results if item.case_id == bad_case_id)
    assert failed.deterministic_pass is False
    assert any(reason.startswith("case_execution_error") for reason in failed.failure_reasons)

    # The other nine cases still scored normally.
    healthy = [item for item in run.case_results if item.case_id != bad_case_id]
    assert len(healthy) == 9
    assert all(item.deterministic_pass for item in healthy)

    # ...but the run must not advertise itself as a complete, fully covered
    # measurement: coverage is completed/eligible, and `complete` requires zero
    # failed cases. Otherwise a crashed case is laundered into a clean run and
    # the leaderboard's coverage_rate == 1.0 gate cannot reject it.
    assert run.failed_case_count == 1
    assert run.completed_case_count == 9
    assert run.eligible_case_count == 10
    assert run.coverage_rate == 0.9
    assert run.run_status == "partial"

    from arena.reports.leaderboard import leaderboard_eligible

    assert leaderboard_eligible(run, include_unverified=True) is False

    metrics = run.deterministic_metrics
    assert metrics is not None
    assert metrics.deterministic_pass_rate == 0.9


def test_audit_report_lists_keyword_gamer_as_detection_validation_gap(
    audit_benchmark_dir, tmp_path
):
    run = _run_audit_patch_mode(audit_benchmark_dir, tmp_path, "keyword_gamer")
    data = build_audit_report_data([run])
    gap_reviewers = {f"{gap['reviewer']}:{gap['model']}" for gap in data["gaps"]}
    assert "control:keyword_gamer" in gap_reviewers
    keyword_gap = next(gap for gap in data["gaps"] if gap["model"] == "keyword_gamer")
    assert keyword_gap["detection_f_beta"] == 1
    assert keyword_gap["validated_f_beta"] == 0
