import json
from datetime import UTC, datetime
from pathlib import Path

from arena.core.models import DeterministicMetrics, RunMetadata, RunResult
from arena.reports.audit_report import (
    build_audit_report_data,
    render_audit_report_markdown,
    write_audit_report,
)
from arena.reports.json_report import write_json_report


def _sample_run(
    run_id: str,
    validated: float,
    detection: float,
    *,
    schema_version: int = 2,
    run_status: str = "complete",
    completed_at: datetime | None = None,
    pack_checksum_verified: bool | None = None,
) -> RunResult:
    """A run shaped like one the current harness writes: schema v2, complete.

    ``schema_version``/``run_status`` are overridable so the validity-filter tests
    can build the untrustworthy runs a report must refuse to publish, and
    ``pack_checksum_verified`` so the provenance carried onto a published row can
    be asserted.
    """
    metrics = DeterministicMetrics(
        detection_f_beta=detection,
        validated_f_beta=validated,
        beta=1.0,
        deterministic_pass_rate=validated,
        patch_apply_rate=1.0,
        test_pass_rate=1.0,
        structural_pass_rate=1.0,
        false_positives_per_case=0.0,
        cost_per_validated_fix=0.0,
        latency_per_case_ms=10.0,
    )
    return RunResult(
        run_id=run_id,
        benchmark_set="audit_v1",
        reviewer="mock",
        model="perfect_patch",
        schema_version=schema_version,
        run_status=run_status,
        started_at=datetime.now(UTC),
        completed_at=completed_at or datetime.now(UTC),
        metadata=RunMetadata(
            prompt_version="v1",
            benchmark_version="audit_v1",
            pack_checksum_verified=pack_checksum_verified,
        ),
        case_results=[],
        total_score=100.0,
        mode="full",
        beta=1.0,
        deterministic_metrics=metrics,
        bugs_found=5,
        correct_files=5,
        correct_lines=5,
        false_positives=0,
        total_cost=0.0,
        total_latency_ms=50,
    )


def test_audit_report_markdown_renders_populated_sections():
    from arena.reports.audit_report import build_audit_report_data, render_audit_report_markdown

    # A reviewer that detects everything but validates nothing is the canonical
    # detection-versus-validation gap; render its populated report.
    detector = _sample_run("detect-only", validated=0.0, detection=1.0)
    markdown = render_audit_report_markdown(build_audit_report_data([detector]))
    assert "Reviewer Comparison" in markdown
    assert "Validated Case Rate" in markdown  # the table leads with the primary metric
    assert "Detection vs Validation Gap" in markdown
    assert "`validated_case_rate` is the primary" in markdown


def test_audit_report_empty_state(tmp_path: Path):
    data = build_audit_report_data([])
    assert data["empty"] is True
    assert data["summary"]["benchmark_pack"] == "audit_v1"
    markdown = write_audit_report(tmp_path, tmp_path / "report.md")
    assert markdown["empty"] is True


def test_audit_report_is_pack_agnostic():
    data = build_audit_report_data([], benchmark_set="audit_v2")
    assert data["summary"]["benchmark_pack"] == "audit_v2"
    assert "Audit Pack v2" in data["title"]
    assert any("audit_v2" in command for command in data["reproducibility_commands"])


def test_audit_report_uses_real_run_json_only(tmp_path: Path):
    run = _sample_run("audit-run-1", validated=0.5, detection=1.0)
    run_dir = tmp_path / "audit-run-1"
    run_dir.mkdir()
    write_json_report(run, run_dir / "run.json")
    (tmp_path / "other-set").mkdir()
    other = _sample_run("other", validated=1.0, detection=1.0)
    other = other.model_copy(update={"benchmark_set": "v1"})
    write_json_report(other, tmp_path / "other-set" / "run.json")

    from arena.reports.audit_report import load_audit_runs

    runs = load_audit_runs(tmp_path)
    data = build_audit_report_data(runs)
    assert data["empty"] is False
    assert len(data["reviewers"]) == 1
    assert data["reviewers"][0]["validated_f_beta"] == 0.5
    assert data["gaps"][0]["gap"] == 0.5

    output = tmp_path / "audit.md"
    json_path = tmp_path / "audit.json"
    payload = write_audit_report(tmp_path, output, json_path)
    assert output.exists()
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved["title"].startswith("Detection Is Not Validation")
    assert "Limitations" in output.read_text(encoding="utf-8")
    assert payload["summary"]["run_count"] == 1


def test_audit_report_json_matches_schema_and_markdown(tmp_path: Path):
    from arena.core.config import REPORT_SCHEMA_VERSION
    from arena.reports.report_schema import AuditReport

    run = _sample_run("audit-run-1", validated=0.5, detection=1.0)
    run_dir = tmp_path / "audit-run-1"
    run_dir.mkdir()
    write_json_report(run, run_dir / "run.json")

    output = tmp_path / "audit.md"
    json_path = tmp_path / "audit.json"
    data = write_audit_report(tmp_path, output, json_path)

    # The written JSON validates against the versioned contract.
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    report = AuditReport.model_validate(saved)
    assert report.schema_version == REPORT_SCHEMA_VERSION

    # Markdown and JSON are rendered from one source, so the JSON's headline figure
    # appears verbatim in the Markdown table.
    markdown = output.read_text(encoding="utf-8")
    assert f"{data['reviewers'][0]['validated_f_beta']:.3f}" in markdown
    assert report.summary.reviewers_tested == data["summary"]["reviewers_tested"]


def test_tampered_pack_run_is_never_published(tmp_path: Path):
    """A run the harness stamped invalid cannot reach a published report.

    This is the exact shape of the leak: editing a pack makes the harness record
    run_status=invalid and pack_checksum_verified=False, and the report builder
    then promoted that run to a headline reviewer row anyway.
    """
    tampered = _sample_run(
        "run-2026-08-14-must-not-publish",
        validated=1.0,
        detection=1.0,
        run_status="invalid",
        pack_checksum_verified=False,
    )
    data = build_audit_report_data([tampered])

    assert data["empty"] is True
    assert data["reviewers"] == []
    assert data["summary"]["run_count"] == 0
    # Excluded, not silently dropped.
    assert data["summary"]["excluded_run_count"] == 1

    markdown = render_audit_report_markdown(data)
    assert "excluded" in markdown
    assert "run-2026-08-14-must-not-publish" not in markdown


def test_invalid_runs_excluded_but_valid_ones_still_reported():
    """One bad run does not suppress the good ones; the exclusion is still counted."""
    good = _sample_run("good", validated=1.0, detection=1.0)
    bad = _sample_run("bad", validated=0.0, detection=1.0, run_status="invalid")
    failed = _sample_run("failed", validated=0.0, detection=0.0, run_status="failed")
    legacy = _sample_run("legacy", validated=1.0, detection=1.0, schema_version=1)

    data = build_audit_report_data([good, bad, failed, legacy])

    assert data["empty"] is False
    assert [row["run_id"] for row in data["reviewers"]] == ["good"]
    assert data["summary"]["excluded_run_count"] == 3
    assert "Runs excluded as untrustworthy: 3" in render_audit_report_markdown(data)


def test_report_rows_carry_integrity_provenance():
    """A published figure can be traced to the trust state that produced it."""
    run = _sample_run("clean", validated=1.0, detection=1.0, pack_checksum_verified=True)
    row = build_audit_report_data([run])["reviewers"][0]
    assert row["run_status"] == "complete"
    assert row["pack_checksum_verified"] is True


def test_report_and_leaderboard_share_one_integrity_floor():
    """The two publishing paths must not drift apart on what counts as trustworthy."""
    from arena.reports.leaderboard import leaderboard_eligible

    invalid = _sample_run("x", validated=1.0, detection=1.0, run_status="invalid")
    # The leaderboard refuses it even when asked to include unverified runs...
    assert leaderboard_eligible(invalid, include_unverified=True) is False
    # ...so the report must refuse it too.
    assert build_audit_report_data([invalid])["reviewers"] == []


def test_audit_report_schema_rejects_drift():
    import pytest
    from pydantic import ValidationError

    from arena.reports.report_schema import AuditReport

    with pytest.raises(ValidationError):
        AuditReport.model_validate({"schema_version": "1.0", "unexpected": True})


def test_invalid_run_never_supersedes_a_valid_one():
    """A later invalid run must not overwrite a real measurement.

    The newest-per-(reviewer, model, mode) reduction used to run before any
    validity check, so a run that never executed -- e.g. Docker was unavailable,
    or the pack checksum did not match -- published its all-zero metrics over a
    complete run and flipped the reviewer's headline rate to 0.
    """
    from datetime import timedelta

    now = datetime.now(UTC)
    good = _sample_run("good", validated=1.0, detection=1.0, completed_at=now)
    # Same reviewer/model/mode, seven seconds newer, but the harness rejected it.
    bad = _sample_run(
        "bad",
        validated=0.0,
        detection=0.0,
        run_status="invalid",
        completed_at=now + timedelta(seconds=7),
    )

    data = build_audit_report_data([good, bad])

    assert data["empty"] is False
    assert [row["run_id"] for row in data["reviewers"]] == ["good"]
    assert data["reviewers"][0]["validated_f_beta"] == 1.0
    assert data["summary"]["run_count"] == 1
    assert data["summary"]["excluded_run_count"] == 1


def test_report_is_empty_when_every_run_is_untrustworthy():
    """Nothing publishable must yield the empty state, not a zeroed report."""
    invalid = _sample_run("invalid", validated=0.0, detection=0.0, run_status="invalid")
    partial = _sample_run("partial", validated=0.4, detection=0.9, run_status="partial")

    data = build_audit_report_data([invalid, partial])

    assert data["empty"] is True
    assert data["summary"]["run_count"] == 0
    assert data["summary"]["excluded_run_count"] == 2


def test_pre_v2_runs_are_not_publishable():
    """A pre-v2 run carries no validity fields, so its trustworthiness is unknown.

    Unknown is not success: it is excluded rather than published, matching the
    leaderboard and repository policy.
    """
    legacy = _sample_run("legacy", validated=1.0, detection=1.0, schema_version=1)

    data = build_audit_report_data([legacy])

    assert data["empty"] is True
    assert data["summary"]["excluded_run_count"] == 1
