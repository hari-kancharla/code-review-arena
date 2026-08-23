"""Run trust level: status, coverage, and leaderboard eligibility."""

from datetime import datetime
from pathlib import Path
from time import monotonic
from types import SimpleNamespace

import pytest

from arena.benchmark.benchmark_runner import _effective_timeout, _run_status, run_benchmark
from arena.benchmark.pack_hash import pack_checksum
from arena.core.errors import ValidationError
from arena.core.models import RUN_SCHEMA_VERSION, RunMetadata, RunResult
from arena.reports.leaderboard import leaderboard_eligible, leaderboard_rows
from arena.reviewers.controls import ControlReviewer

V1 = Path("benchmark_sets/v1")


def _minimal_run(**overrides) -> RunResult:
    base = dict(
        run_id="r",
        benchmark_set="v1",
        reviewer="control",
        model="perfect",
        started_at=datetime.now(),
        completed_at=datetime.now(),
        metadata=RunMetadata(
            prompt_version="v1", benchmark_version="v1", reviewer_oracle_reachable=False
        ),
        case_results=[],
        total_score=0.0,
        bugs_found=0,
        correct_files=0,
        correct_lines=0,
        false_positives=0,
        total_cost=0.0,
        total_latency_ms=0,
    )
    base.update(overrides)
    return RunResult(**base)


def test_effective_timeout_clamps_to_the_run_deadline():
    # No deadline: the case timeout is used as-is.
    assert _effective_timeout(30, None) == 30
    # A near deadline shortens the per-case timeout.
    soon = _effective_timeout(30, monotonic() + 5)
    assert 1 <= soon <= 5
    # Past the deadline, it floors at 1s rather than going negative.
    assert _effective_timeout(30, monotonic() - 10) == 1


def _status(**overrides) -> str:
    base = dict(
        results=10,
        failed=0,
        skipped=False,
        checksum_verified=None,
        execution_required=False,
        executed=0,
        unavailable=0,
    )
    base.update(overrides)
    return _run_status(**base)


def test_run_status_classifies_each_trust_level():
    assert _status() == "complete"
    assert _status(checksum_verified=True) == "complete"
    assert _status(results=3, skipped=True) == "partial"
    # A budget that trips before any case runs is still a truncation, not a crash.
    assert _status(results=0, skipped=True) == "partial"
    assert _status(results=0) == "failed"
    # A tampered pack invalidates the whole run regardless of coverage.
    assert _status(skipped=True, checksum_verified=False) == "invalid"


def test_run_status_is_not_complete_when_a_case_crashed():
    """`complete` requires zero failed cases (trusted-evaluation-architecture.md).

    A case that raised inside the harness leaves a placeholder result, so the run
    is a partial measurement -- and a run in which nothing survived has no usable
    result at all.
    """
    assert _status(results=10, failed=1) == "partial"
    assert _status(results=10, failed=9) == "partial"
    # Every case crashed: no usable result, so this is a failure, not a partial.
    assert _status(results=10, failed=10) == "failed"


def test_run_status_invalid_when_reviewer_never_produced_usable_output():
    """Every case unparseable is a broken reviewer, not a score of zero.

    A wrapper that crashes on every case previously completed as a normal run
    reporting 0.0, indistinguishable on the leaderboard from a reviewer that ran
    and found nothing.
    """
    assert _status(all_output_invalid=True) == "invalid"
    # A partial failure is still a scoreable reviewer-contract failure.
    assert _status(all_output_invalid=False) == "complete"
    # A tampered pack still takes precedence, and no results is still "failed".
    assert _status(all_output_invalid=True, checksum_verified=False) == "invalid"
    assert _status(results=0, all_output_invalid=False) == "failed"


def test_run_status_invalid_when_execution_required_but_backend_unavailable():
    # Execution was required, nothing ran, and cases tried: scores are not real.
    assert _status(execution_required=True, executed=0, unavailable=5, results=5) == "invalid"
    # A review-mode run never needs execution, so an unavailable backend is moot.
    assert _status(execution_required=False, executed=0, unavailable=5) == "complete"
    # Some cases ran and some could not: partial, not invalid.
    assert _status(execution_required=True, executed=3, unavailable=2) == "partial"
    # Execution required but every case ran: complete.
    assert _status(execution_required=True, executed=10, unavailable=0) == "complete"


def test_crashing_reviewer_is_marked_invalid_not_scored_zero(tmp_path):
    """End-to-end: a reviewer that raises on every case must not read as a real 0.0."""
    from arena.core.models import CaseContext, ReviewerResponse
    from arena.reviewers.base import BaseReviewer

    class CrashingReviewer(BaseReviewer):
        name = "crashing"
        model = None

        def review(self, context: CaseContext) -> ReviewerResponse:
            raise RuntimeError("wrapper died")

    run = run_benchmark(V1, CrashingReviewer(), mode="review")

    # Every case failed to parse, so the run itself is not a measurement.
    assert run.metadata.reviewer_parse_status_counts == {"invalid": run.eligible_case_count}
    assert run.run_status == "invalid"
    # And it can never reach the leaderboard, even with unverified runs included.
    assert leaderboard_eligible(run, include_unverified=True) is False


def test_execution_backend_reflects_actual_execution(tmp_path):
    full = run_benchmark(
        V1,
        ControlReviewer("perfect_patch"),
        output_dir=tmp_path / "runs",
        persist=False,
        mode="full",
        allow_local_execution=True,
    )
    assert full.execution_backend == "trusted-local"
    assert any(case.execution_backend == "trusted-local" for case in full.case_results)

    review = run_benchmark(
        V1, ControlReviewer("perfect"), output_dir=tmp_path / "review-runs", persist=False
    )
    assert review.execution_backend == "none"


def test_full_run_is_invalid_when_no_execution_backend_is_available(tmp_path):
    # Full mode needs test execution, but local execution is off and the V1 cases
    # ship no docker image: nothing can run, so no repair can be judged and the
    # scores are not a fair measurement of the reviewer.
    run = run_benchmark(
        V1,
        ControlReviewer("perfect_patch"),
        output_dir=tmp_path / "runs",
        persist=False,
        mode="full",
        allow_local_execution=False,
    )
    assert run.run_status == "invalid"
    assert run.execution_backend == "none"
    # Every case that applied a patch tried to run and hit the disabled backend.
    assert any(case.execution_unavailable for case in run.case_results)
    assert leaderboard_eligible(run) is False


def test_local_execution_requires_a_trusted_pack_hash(tmp_path, monkeypatch):
    import warnings

    from arena.benchmark.pack_hash import pack_checksum

    # An allowlist that does not include this pack's checksum blocks host execution
    # even though --allow-local-execution was passed: nothing runs, so the run is
    # invalid rather than silently trusting an unlisted pack.
    monkeypatch.setenv("ARENA_TRUSTED_PACK_HASHES", "0000deadbeef")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        blocked = run_benchmark(
            V1,
            ControlReviewer("perfect_patch"),
            output_dir=tmp_path / "blocked",
            persist=False,
            mode="full",
            allow_local_execution=True,
        )
    assert blocked.execution_backend == "none"
    assert blocked.run_status == "invalid"

    # The pack's real checksum on the allowlist permits local execution.
    monkeypatch.setenv("ARENA_TRUSTED_PACK_HASHES", pack_checksum(V1))
    allowed = run_benchmark(
        V1,
        ControlReviewer("perfect_patch"),
        output_dir=tmp_path / "allowed",
        persist=False,
        mode="full",
        allow_local_execution=True,
    )
    assert allowed.execution_backend == "trusted-local"
    assert allowed.run_status == "complete"


def test_complete_run_records_full_coverage(tmp_path):
    run = run_benchmark(V1, ControlReviewer("perfect"), output_dir=tmp_path / "runs", persist=False)
    assert run.schema_version == RUN_SCHEMA_VERSION
    assert run.run_status == "complete"
    assert run.execution_backend == "none"  # review mode never executes
    assert run.skipped_case_count == 0
    assert run.failed_case_count == 0
    assert run.eligible_case_count == run.completed_case_count == run.case_count
    assert run.coverage_rate == 1.0


def test_leaderboard_includes_complete_and_excludes_partial(tmp_path):
    runs_dir = tmp_path / "runs"
    complete = run_benchmark(V1, ControlReviewer("perfect"), output_dir=runs_dir, persist=False)
    partial = run_benchmark(
        V1, ControlReviewer("perfect"), output_dir=runs_dir, persist=False, max_wall_seconds=0.0
    )
    assert complete.run_status == "complete"
    assert partial.run_status == "partial"
    # These are unverified review runs (no Docker), so query with include_unverified;
    # the point here is that a complete run is listed and a partial one is not.
    run_ids = {row["run_id"] for row in leaderboard_rows(runs_dir, include_unverified=True)}
    assert complete.run_id in run_ids
    assert partial.run_id not in run_ids


def _verified_run(**overrides) -> RunResult:
    """A run that meets every default-leaderboard requirement."""
    base = dict(
        schema_version=2,
        run_status="complete",
        execution_backend="docker",
        coverage_rate=1.0,
        metadata=RunMetadata(
            prompt_version="v2",
            benchmark_version="v1",
            pack_checksum_verified=True,
            pack_digest_externally_verified=True,
            non_exact_output_used=False,
            reviewer_oracle_reachable=False,
        ),
    )
    base.update(overrides)
    return _minimal_run(**base)


def test_leaderboard_eligibility_rules():
    # Default eligibility requires an externally verified run: docker-backed, full
    # coverage, and a pack content match against an out-of-band digest.
    assert leaderboard_eligible(_verified_run()) is True
    # Pre-v2 and non-complete runs are excluded regardless of verification.
    assert leaderboard_eligible(_verified_run(schema_version=1)) is False
    for status in ("partial", "invalid", "failed", "cancelled", "legacy"):
        assert leaderboard_eligible(_verified_run(run_status=status)) is False
    # A self-consistent pack (its own pack.sha256 matches) that was NOT checked
    # against an external digest is ineligible: regenerating pack.sha256 cannot
    # buy a leaderboard spot. Inspectable only with include_unverified.
    internal_only = _verified_run(
        metadata=RunMetadata(
            prompt_version="v2",
            benchmark_version="v1",
            pack_checksum_verified=True,
            pack_digest_externally_verified=False,
            reviewer_oracle_reachable=False,
        )
    )
    assert leaderboard_eligible(internal_only) is False
    assert leaderboard_eligible(internal_only, include_unverified=True) is True
    # Trusted-local runs are unverified too: excluded by default, included on opt-in.
    local = _minimal_run(schema_version=2, run_status="complete", execution_backend="trusted-local")
    assert leaderboard_eligible(local) is False
    assert leaderboard_eligible(local, include_unverified=True) is True


def test_self_consistent_pack_is_not_externally_verified(tmp_path):
    import shutil

    from arena.benchmark.pack_hash import write_checksum

    # Copy a valid pack and regenerate its own pack.sha256, exactly what an
    # attacker who edited a pack would do, then run it with no external digest.
    pack = tmp_path / "v1_copy"
    shutil.copytree(V1, pack)
    write_checksum(pack)
    run = run_benchmark(
        pack, ControlReviewer("perfect"), output_dir=tmp_path / "runs", persist=False, mode="review"
    )
    # The pack matches its own hash, but that is only self-consistency...
    assert run.metadata.pack_checksum_verified is True
    # ...it was never verified against an external digest, so it cannot be eligible.
    assert run.metadata.pack_digest_externally_verified is False
    assert leaderboard_eligible(run) is False


def test_expected_pack_sha256_mismatch_aborts_before_run_dir(tmp_path):
    runs = tmp_path / "runs"
    with pytest.raises(ValidationError):
        run_benchmark(
            V1,
            ControlReviewer("perfect"),
            output_dir=runs,
            persist=False,
            mode="review",
            expected_pack_sha256="0" * 64,
        )
    assert not runs.exists()  # aborted before any run directory was created


def test_expected_pack_sha256_match_runs(tmp_path):
    run = run_benchmark(
        V1,
        ControlReviewer("perfect"),
        output_dir=tmp_path / "runs",
        persist=False,
        mode="review",
        expected_pack_sha256=pack_checksum(V1),
    )
    assert run.run_status == "complete"  # matching digest: the run proceeds


def test_run_level_beta_does_not_depend_on_case_order():
    """A whole-run F-beta must not be whichever beta the last case declared.

    `selected_beta` was reassigned inside the per-case loop, so on a pack with
    mixed metrics.beta values the run-level number was computed with the trailing
    case's weighting -- reordering the manifest silently changed the published
    headline with no reviewer behaving differently.
    """
    from arena.benchmark.benchmark_runner import _run_level_beta

    class _Case:
        def __init__(self, beta: float) -> None:
            self.metrics = SimpleNamespace(beta=beta)

    # Unanimous: that value is faithful for the whole run.
    assert _run_level_beta([_Case(2.0), _Case(2.0)]) == 2.0
    # Mixed: no single value is faithful, so fall back to balanced, not to the
    # last case -- and the result must not depend on ordering.
    mixed = [_Case(2.0), _Case(1.0), _Case(0.5)]
    assert _run_level_beta(mixed) == 1.0
    assert _run_level_beta(list(reversed(mixed))) == 1.0
    # An empty pack still yields a usable default.
    assert _run_level_beta([]) == 1.0


def test_missing_test_runner_is_an_unavailable_backend_not_a_failing_suite(tmp_path):
    """A runner this interpreter cannot import must invalidate the run.

    Local execution pins `pytest` to the harness's own interpreter, so an install
    without it (the built wheel, the shipped Dockerfile) ran nothing and reported
    every case as tests_failed -- publishing validated_case_rate=0.0 for
    known-good reference patches as though the reviewer had failed, with
    run_status=complete and exit code 0.
    """
    from arena.execution.test_executor import (
        TestExecutionRequest,
        TestExecutor,
        _missing_runner,
    )

    # pytest is importable here, so a real runner is not reported missing...
    assert _missing_runner([["pytest", "-q", "tests"]]) is None
    # ...while a `-m` runner that is genuinely absent is named.
    assert _missing_runner([["python", "-m", "definitely_not_installed"]]) == (
        "definitely_not_installed"
    )
    # A command that is not a `-m` invocation is left alone: whether that binary
    # exists is the fixture's problem, and the executor reports it as it always has.
    assert _missing_runner([["definitely_not_a_runner"]]) is None

    workspace = tmp_path / "ws"
    workspace.mkdir()
    result = TestExecutor().execute(
        TestExecutionRequest(
            case_id="missing-runner-001",
            workspace_path=workspace,
            test_command=[["pytest", "-q", "tests"]],
            timeout_seconds=30,
            allow_local_execution=True,
        )
    )
    # pytest IS present in this environment, so this must NOT trip the guard.
    assert not (result.error or "").startswith("test_runner_unavailable")


def test_a_case_whose_tests_never_run_is_not_execution_backed():
    """`tests_required` alone is a claim, not a runnable suite.

    The executor only runs tests when `run_tests` and a `test_command` are both
    present, so a case declaring `run_tests: false, tests_required: true` put
    itself in the validated_case_rate denominator where `tests_passed is not
    True` marked it a miss for every reviewer forever -- publishing 0% repair
    even for the exact ground-truth fix.
    """
    from arena.core.models import BenchmarkCase
    from arena.scoring.deterministic_scorer import is_execution_backed

    def case(**execution):
        return BenchmarkCase.model_validate(
            {
                "id": "c",
                "title": "t",
                "category": "correctness",
                "severity": "low",
                "stack": ["python"],
                "description": "d",
                "input": {},
                "execution": execution,
                "ground_truth": {
                    "bugs": [
                        {
                            "summary": "s",
                            "files": [{"path": "a.py", "line_ranges": [{"start": 1, "end": 1}]}],
                            "concepts": ["x"],
                        }
                    ]
                },
                "validation": {"tests_required": True},
            }
        )

    assert is_execution_backed(case(run_tests=True, test_command="pytest -q tests")) is True
    # Claims a test requirement but nothing will ever run.
    assert is_execution_backed(case(run_tests=False)) is False
    assert is_execution_backed(case(run_tests=True)) is False  # no command to run


def _gated_case(**overrides):
    """A synthetic case with a runnable suite, unless overridden."""
    from arena.core.models import BenchmarkCase

    base = {
        "id": "gated",
        "title": "t",
        "category": "correctness",
        "severity": "low",
        "stack": ["python"],
        "description": "d",
        "input": {},
        "execution": {"run_tests": True, "test_command": "pytest -q tests"},
        "validation": {"patch_required": True, "tests_required": True},
        "ground_truth": {
            "bugs": [
                {
                    "summary": "s",
                    "files": [{"path": "a.py", "line_ranges": [{"start": 1, "end": 1}]}],
                    "concepts": ["x"],
                }
            ]
        },
    }
    base.update(overrides)
    return BenchmarkCase.model_validate(base)


def test_execution_backed_requires_a_suite_that_can_actually_run():
    """`tests_required` alone is a claim, not a runnable suite.

    A case with `run_tests: false, tests_required: true` never executes anything,
    but counting it as execution-backed put it in the validated_case_rate
    denominator where `tests_passed is not True` marked it a miss for every
    reviewer -- publishing 0% repair even for the exact ground-truth fix.
    """
    from arena.scoring.deterministic_scorer import is_execution_backed

    assert is_execution_backed(_gated_case()) is True
    assert (
        is_execution_backed(
            _gated_case(execution={"run_tests": False}, validation={"tests_required": True})
        )
        is False
    )
    # Declared but with nothing to run: the executor needs a command.
    assert is_execution_backed(_gated_case(execution={"run_tests": True})) is False


def _reasons_for(error: str | None) -> list[str]:
    from arena.execution.test_executor import TestExecutionResult
    from arena.patching.patch_models import PatchApplyResult
    from arena.scoring.deterministic_scorer import score_deterministic_case
    from arena.scoring.scorer import score_case
    from tests.test_multi_bug_scoring import _response

    case = _gated_case()
    review = score_case(case, _response([]))
    patch = PatchApplyResult(
        case_id=case.id, applied=True, workspace_path="ws", patch_text="x", duration_ms=1
    )
    tests = TestExecutionResult(
        case_id=case.id, ran=True, passed=False, execution_mode="local", error=error
    )
    return score_deterministic_case(case, review, patch, tests, [], beta=1.0).failure_reasons


def test_a_harness_failure_is_not_recorded_as_a_failing_suite():
    """ "We never got an answer" must not read as "the suite said no".

    A missing runner or an absent Docker backend is the environment's doing.
    Recording those as tests_failed charged a correct repair to the reviewer and
    hid that the run was degraded. Anything the PATCH caused stays tests_failed.
    """
    assert "tests_failed" in _reasons_for(None)
    assert "execution_inconclusive" not in _reasons_for(None)

    for harness_error in (
        "test_runner_unavailable:pytest",
        "docker_required_but_unavailable",
        "docker_image_not_present",
        "local_execution_disabled",
    ):
        reasons = _reasons_for(harness_error)
        assert "execution_inconclusive" in reasons, harness_error
        assert "tests_failed" not in reasons, harness_error


def test_a_sabotaged_suite_is_still_the_patch_s_fault():
    """`no_test_evidence` means the patch stopped the suite: not an excuse.

    Calling it inconclusive would turn the anti-cheat signal into a harness
    problem and stop it counting against the reviewer.
    """
    reasons = _reasons_for("no_test_evidence")
    assert "tests_failed" in reasons
    assert "execution_inconclusive" not in reasons


def test_inconclusive_execution_still_blocks_a_validated_repair():
    """Whatever it is called, nothing was confirmed, so nothing passes."""
    from arena.benchmark.benchmark_runner import _BLOCKING_FAILURE_REASONS

    assert "execution_inconclusive" in _BLOCKING_FAILURE_REASONS
    assert "tests_failed" in _BLOCKING_FAILURE_REASONS


def test_a_reviewer_cannot_void_its_own_run_by_flooding_output():
    """Anything the PATCH causes is the patch's failure, not a harness excuse.

    Classifying the output-cap kill as a missing backend let a reviewer suppress
    its own bad result: one flooded case marks the run partial, and eligibility
    rejects a partial run outright -- even under include_unverified. A hang is
    already treated as the patch's failure; a flood is the same class.
    """
    from arena.scoring.deterministic_scorer import _INCONCLUSIVE_EXECUTION_ERRORS, _is_inconclusive

    for patch_caused in ("test_output_too_large", "test_execution_timed_out", "no_test_evidence"):
        assert patch_caused not in _INCONCLUSIVE_EXECUTION_ERRORS, patch_caused
        assert _is_inconclusive(_result(patch_caused)) is False, patch_caused

    # ...while a genuinely absent backend still is the environment's doing.
    for environmental in (
        "docker_required_but_unavailable",
        "docker_image_not_present",
        "local_execution_disabled",
        "test_runner_unavailable:pytest",
    ):
        assert _is_inconclusive(_result(environmental)) is True, environmental


def _result(error: str):
    from arena.execution.test_executor import TestExecutionResult

    return TestExecutionResult(
        case_id="c", ran=True, passed=False, execution_mode="local", error=error
    )


def test_a_deadline_truncated_suite_is_not_charged_to_the_reviewer(tmp_path):
    """Whose budget ran out decides what a timeout means.

    --max-wall-seconds clamps a case's own test timeout, so the harness can cut a
    suite short. Scored as an ordinary timeout, that published 0% repair for what
    may be a perfect fix, on a run still labelled complete and still leaderboard
    eligible. A hang against the case's OWN budget remains the patch's failure.
    """
    import shutil

    source = Path("benchmark_sets/v1/async_race_condition_001")
    pack = tmp_path / "pack"
    case_dir = pack / "slow_case_001"
    shutil.copytree(source, case_dir)
    (pack / "manifest.yaml").write_text(
        "version: slow\nname: Deadline probe\ncases:\n  - slow_case_001\n", encoding="utf-8"
    )
    case_dir.joinpath("case.yaml").write_text(
        case_dir.joinpath("case.yaml")
        .read_text(encoding="utf-8")
        .replace("id: async_race_condition_001", "id: slow_case_001"),
        encoding="utf-8",
    )
    suite = next(case_dir.joinpath("tests").rglob("test_*.py"))
    suite.write_text("import time\ntime.sleep(4)\n" + suite.read_text(encoding="utf-8"), "utf-8")

    def run(**budget):
        return run_benchmark(
            pack,
            _reference_reviewer(),
            output_dir=tmp_path / f"runs{len(budget)}",
            persist=False,
            mode="full",
            allow_local_execution=True,
            **budget,
        )

    # Given room, the ground-truth fix validates.
    unconstrained = run()
    assert unconstrained.run_status == "complete"
    assert unconstrained.deterministic_metrics.validated_case_rate == 1.0

    # Clamped, the same patch must not be recorded as having failed the suite.
    truncated = run(max_wall_seconds=7.0)
    reasons = truncated.case_results[0].failure_reasons
    assert "execution_inconclusive" in reasons
    assert "tests_failed" not in reasons
    assert truncated.case_results[0].execution_unavailable is True
    # ...and the run says so rather than publishing a clean 0%.
    assert truncated.run_status == "partial"
    assert leaderboard_eligible(truncated, include_unverified=True) is False


def _reference_reviewer():
    from arena.reviewers.reference_patch import ReferencePatchReviewer

    return ReferencePatchReviewer()


def test_no_measurable_case_reports_not_measured_rather_than_zero():
    """An empty denominator is "not measured", not "repaired nothing".

    Publishing 0.0 for a run with nothing execution-backed fabricates a result,
    and reads on a leaderboard exactly like a reviewer that failed everything.
    """
    from arena.core.models import CaseResult, ReviewerResponse, ReviewResult, ScoreBreakdown
    from arena.patching.patch_models import PatchApplyResult
    from arena.scoring.deterministic_scorer import (
        aggregate_deterministic_metrics,
        score_deterministic_case,
    )
    from arena.scoring.scorer import score_case
    from tests.test_multi_bug_scoring import _response

    # A case judged only by a structural validator: never execution-backed.
    case = _gated_case(
        execution={"run_tests": False},
        validation={
            "patch_required": True,
            "structural_validators": ["sql_has_tenant_or_owner_filter"],
        },
    )
    review = score_case(case, _response([]))
    score = score_deterministic_case(
        case,
        review,
        PatchApplyResult(
            case_id=case.id, applied=True, workspace_path="ws", patch_text="x", duration_ms=1
        ),
        None,
        [],
        beta=1.0,
    )
    assert score.validation_eligible is False

    result = CaseResult(
        case_id=case.id,
        title=case.title,
        category=case.category,
        severity=case.severity,
        ground_truth_summary="s",
        response=ReviewerResponse(
            raw_response="{}",
            parsed_response=ReviewResult(findings=[], overall_risk="low", review_summary="s"),
        ),
        scored_findings=[],
        breakdown=ScoreBreakdown(),
        score=0.0,
        bug_found=False,
        correct_file=False,
        correct_line=False,
        line_match="wrong_file",
        false_positive_count=0,
        deterministic_case_score=score,
        deterministic_pass=False,
    )

    metrics = aggregate_deterministic_metrics(
        [result], beta=1.0, total_cost=0.0, total_latency_ms=0
    )

    assert metrics.validated_eligible_case_count == 0
    assert metrics.validated_case_rate is None
    assert metrics.deterministic_pass_rate is None
    assert metrics.complete_repair_rate is None
