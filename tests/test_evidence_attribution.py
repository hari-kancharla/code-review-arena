"""Evidence attribution: per-finding status, per-bug repair, and case status."""

from arena.benchmark.benchmark_runner import _attribute_evidence, _repair_confidence
from arena.core.models import BenchmarkCase, Finding, ReviewerResponse, ReviewResult
from arena.execution.test_executor import TestExecutionResult
from arena.patching.patch_models import PatchApplyResult
from arena.scoring.deterministic_scorer import (
    aggregate_deterministic_metrics,
    score_deterministic_case,
)
from arena.scoring.scorer import score_case
from arena.validators.base import ValidatorResult


def _case() -> BenchmarkCase:
    return BenchmarkCase.model_validate(
        {
            "id": "syn",
            "title": "t",
            "category": "correctness",
            "severity": "high",
            "stack": ["python"],
            "description": "d",
            "input": {},
            "ground_truth": {
                "bugs": [
                    {
                        "summary": "Race",
                        "files": [{"path": "app/a.py", "line_ranges": [{"start": 5, "end": 10}]}],
                        "concepts": ["race condition"],
                        "must_mention": ["lock"],
                        "acceptable_fix_keywords": ["lock"],
                    },
                    {
                        "summary": "SQLi",
                        "files": [{"path": "app/b.py", "line_ranges": [{"start": 1, "end": 3}]}],
                        "concepts": ["sql injection"],
                        "must_mention": ["parameterize"],
                        "acceptable_fix_keywords": ["bind"],
                    },
                ]
            },
            # An executable case: eligibility for validated_case_rate follows what
            # the executor would actually run, so the suite must be RUNNABLE
            # (run_tests + a command), not merely declared required. The
            # _deterministic helper supplies the matching test results.
            "execution": {"run_tests": True, "test_command": "pytest -q tests"},
            "validation": {"tests_required": True},
        }
    )


def _finding(file: str, start: int, end: int, text: str, fix: str) -> Finding:
    return Finding(
        title=text,
        summary=text,
        category="correctness",
        severity="high",
        file=file,
        line_start=start,
        line_end=end,
        evidence=text,
        suggested_fix=fix,
        confidence=0.9,
    )


def _response(findings: list[Finding]) -> ReviewerResponse:
    result = ReviewResult(findings=findings, overall_risk="high", review_summary="s")
    return ReviewerResponse(raw_response=result.model_dump_json(), parsed_response=result)


BUG0 = _finding("app/a.py", 5, 10, "Race condition needs a lock", "use a lock")
BUG1 = _finding("app/b.py", 1, 3, "SQL injection: parameterize", "use bind params")


def _deterministic(case, review, *, tests_passed: bool):
    patch = PatchApplyResult(
        case_id=case.id, applied=True, workspace_path="ws", patch_text="x", duration_ms=1
    )
    tests = TestExecutionResult(
        case_id=case.id, ran=True, passed=tests_passed, execution_mode="local"
    )
    return score_deterministic_case(case, review, patch, tests, [], beta=1.0)


def test_bug_ids_are_auto_assigned():
    case = _case()
    assert [bug.id for bug in case.ground_truth.bugs] == ["bug-1", "bug-2"]


def test_patch_required_case_without_an_executable_gate_is_not_validated():
    # A case requiring a patch but with no tests and no structural validators
    # cannot confirm a repair. A clean patch apply alone must not count as
    # validated, and the case is not eligible for validated_case_rate.
    case = BenchmarkCase.model_validate(
        {
            "id": "no_gate",
            "title": "t",
            "category": "correctness",
            "severity": "high",
            "stack": ["python"],
            "description": "d",
            "input": {},
            "ground_truth": {
                "bugs": [
                    {
                        "summary": "b",
                        "files": [{"path": "a.py", "line_ranges": [{"start": 1, "end": 1}]}],
                        "concepts": ["x"],
                    }
                ]
            },
            "validation": {"patch_required": True},
        }
    )
    review = score_case(case, _response([]))
    patch = PatchApplyResult(
        case_id=case.id, applied=True, workspace_path="ws", patch_text="x", duration_ms=1
    )
    det = score_deterministic_case(case, review, patch, None, [], beta=1.0)
    assert det.validation_eligible is False
    assert "no_execution_evidence" in det.failure_reasons
    assert det.deterministic_pass is False


def test_complete_repair_when_all_detected_and_validated():
    case = _case()
    review = score_case(case, _response([BUG0, BUG1]))
    det = _deterministic(case, review, tests_passed=True)
    bug_repairs, findings, status = _attribute_evidence(
        case, review, det, execution_validated=True, integrity_violated=False
    )
    assert status == "complete_repair"
    assert all(bug.detected and bug.repaired for bug in bug_repairs)
    assert {finding.evidence_status for finding in findings} == {"repair_validated"}


def test_detected_but_unrepaired_when_repair_fails():
    case = _case()
    review = score_case(case, _response([BUG0, BUG1]))
    det = _deterministic(case, review, tests_passed=False)
    bug_repairs, findings, status = _attribute_evidence(
        case, review, det, execution_validated=False, integrity_violated=False
    )
    assert status == "detected_but_unrepaired"
    assert all(bug.detected and not bug.repaired for bug in bug_repairs)
    assert {finding.evidence_status for finding in findings} == {"detected_but_unrepaired"}


def test_partial_repair_when_validated_but_detection_incomplete():
    case = _case()
    review = score_case(case, _response([BUG0]))  # only one of two bugs found
    det = _deterministic(case, review, tests_passed=True)
    bug_repairs, _, status = _attribute_evidence(
        case, review, det, execution_validated=True, integrity_violated=False
    )
    assert status == "partial_repair"
    assert sum(bug.detected for bug in bug_repairs) == 1


def test_unsupported_finding_is_flagged():
    case = _case()
    noise = _finding("unrelated/x.py", 1, 1, "style nit", "rename")
    review = score_case(case, _response([BUG0, BUG1, noise]))
    det = _deterministic(case, review, tests_passed=True)
    _, findings, _ = _attribute_evidence(
        case, review, det, execution_validated=True, integrity_violated=False
    )
    assert "unsupported" in {finding.evidence_status for finding in findings}


def test_tampering_overrides_case_status():
    case = _case()
    review = score_case(case, _response([BUG0, BUG1]))
    det = _deterministic(case, review, tests_passed=True)
    _, _, status = _attribute_evidence(
        case, review, det, execution_validated=False, integrity_violated=True
    )
    assert status == "tampering"


def _attributed(case, review, *, validated):
    det = _deterministic(case, review, tests_passed=validated)
    bug_repairs, findings, status = _attribute_evidence(
        case, review, det, execution_validated=validated, integrity_violated=False
    )
    return review.model_copy(
        update={
            "deterministic_case_score": det,
            "case_status": status,
            "bug_repairs": bug_repairs,
            "scored_findings": findings,
        }
    )


def test_aggregate_reports_evidence_dimensions():
    case = _case()
    repaired = _attributed(case, score_case(case, _response([BUG0, BUG1])), validated=True)
    metrics = aggregate_deterministic_metrics(
        [repaired], beta=1.0, total_cost=0.0, total_latency_ms=0
    )
    assert metrics.complete_repair_rate == 1.0
    assert metrics.bug_completeness_rate == 1.0
    assert metrics.supported_claim_rate == 1.0


def test_aggregate_separates_detection_from_repair():
    case = _case()
    detected_only = _attributed(case, score_case(case, _response([BUG0, BUG1])), validated=False)
    metrics = aggregate_deterministic_metrics(
        [detected_only], beta=1.0, total_cost=0.0, total_latency_ms=0
    )
    # Both bugs were detected but the repair did not validate.
    assert metrics.bug_completeness_rate == 1.0
    assert metrics.complete_repair_rate == 0.0


def test_supported_claim_rate_counts_only_judged_findings():
    case = _case()
    noise = _finding("unrelated/x.py", 1, 1, "style nit", "rename")
    review = score_case(case, _response([BUG0, BUG1, noise]))
    attributed = _attributed(case, review, validated=True)
    metrics = aggregate_deterministic_metrics(
        [attributed], beta=1.0, total_cost=0.0, total_latency_ms=0
    )
    # Two supported claims out of three judged findings.
    assert metrics.supported_claim_rate == round(2 / 3, 6)


def test_repair_confidence_levels():
    case = _case()
    review = score_case(case, _response([BUG0, BUG1]))
    patch = PatchApplyResult(
        case_id=case.id, applied=True, workspace_path="ws", patch_text="x", duration_ms=1
    )
    tests = TestExecutionResult(case_id=case.id, ran=True, passed=True, execution_mode="local")

    # Tests passed but no structural validators ran -> basic.
    det_basic = score_deterministic_case(case, review, patch, tests, [], beta=1.0)
    assert _repair_confidence(execution_validated=True, deterministic=det_basic) == "basic"

    # Tests plus a passing structural validator -> strong.
    validator = ValidatorResult(name="v", passed=True, confidence=1.0, message="ok")
    det_strong = score_deterministic_case(case, review, patch, tests, [validator], beta=1.0)
    assert _repair_confidence(execution_validated=True, deterministic=det_strong) == "strong"

    # A repair that did not validate -> unvalidated, regardless of validators.
    assert _repair_confidence(execution_validated=False, deterministic=det_strong) == "unvalidated"


def _structural_only_case() -> BenchmarkCase:
    """A patch-required case whose only gate is a structural validator."""
    return BenchmarkCase.model_validate(
        {
            "id": "structural_only",
            "title": "t",
            "category": "correctness",
            "severity": "high",
            "stack": ["typescript"],
            "description": "d",
            "input": {},
            "execution": {"run_tests": False},
            "ground_truth": {
                "bugs": [
                    {
                        "summary": "N+1",
                        "files": [{"path": "a.ts", "line_ranges": [{"start": 1, "end": 1}]}],
                        "concepts": ["n+1"],
                    }
                ]
            },
            "validation": {
                "patch_required": True,
                "tests_required": False,
                "structural_validators": ["graphql_uses_batching_or_dataloader"],
            },
        }
    )


def test_structural_validator_alone_does_not_confer_a_validated_repair():
    """validated_case_rate must mean execution-backed, not heuristic, evidence.

    A structural validator is a comment-stripped lexical/AST check -- the project
    documents it as weaker than execution, and it can be satisfied by a patch
    that does not repair the defect. A case gated only by one is therefore
    excluded from the rate entirely rather than counted as a validated repair.
    """
    case = _structural_only_case()
    review = score_case(case, _response([]))
    patch = PatchApplyResult(
        case_id=case.id, applied=True, workspace_path="ws", patch_text="x", duration_ms=1
    )
    passing_validator = [
        ValidatorResult(
            name="graphql_uses_batching_or_dataloader",
            passed=True,
            confidence=0.85,
            message="ok",
        )
    ]

    det = score_deterministic_case(case, review, patch, None, passing_validator, beta=1.0)

    # It is not execution-eligible...
    assert det.validation_eligible is False
    # ...but it is NOT punished either: the case simply has no executable gate,
    # which is a property of the case, not a reviewer failure.
    assert "no_execution_evidence" not in det.failure_reasons
    # And its structural evidence is still recorded.
    assert det.structural_validation_passed is True


def test_structural_only_cases_leave_validated_case_rate_undiluted():
    """Such a case must not appear in the numerator or the denominator."""
    from arena.core.models import CaseResult

    structural = _structural_only_case()
    review = score_case(structural, _response([]))
    patch = PatchApplyResult(
        case_id=structural.id, applied=True, workspace_path="ws", patch_text="x", duration_ms=1
    )
    score = score_deterministic_case(
        structural,
        review,
        patch,
        None,
        [
            ValidatorResult(
                name="graphql_uses_batching_or_dataloader",
                passed=True,
                confidence=0.85,
                message="ok",
            )
        ],
        beta=1.0,
    )
    from arena.core.models import ScoreBreakdown

    result = CaseResult(
        case_id=structural.id,
        title=structural.title,
        category=structural.category,
        severity=structural.severity,
        ground_truth_summary="seeded bug",
        response=_response([]),
        scored_findings=[],
        breakdown=ScoreBreakdown(),
        score=0.0,
        bug_found=review.bug_found,
        correct_file=review.correct_file,
        correct_line=review.correct_line,
        line_match="wrong_file",
        false_positive_count=review.false_positive_count,
        deterministic_case_score=score,
        deterministic_pass=score.deterministic_pass,
    )

    metrics = aggregate_deterministic_metrics(
        [result], beta=1.0, total_cost=0.0, total_latency_ms=0
    )

    # No eligible case at all: the rate is neither inflated to 1.0 by a heuristic
    # nor fabricated as 0.0, which would read on a leaderboard exactly like a
    # reviewer that repaired nothing. It is simply not measured.
    assert metrics.validated_eligible_case_count == 0
    assert metrics.validated_case_rate is None
    assert metrics.validated_case_rate_ci_low is None
