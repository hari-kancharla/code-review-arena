"""Execution-backed deterministic scoring built on top of review localization."""

from __future__ import annotations

from arena.core.models import (
    BenchmarkCase,
    CaseResult,
    DeterministicCaseScore,
    DeterministicMetrics,
)
from arena.execution.test_executor import TestExecutionResult
from arena.patching.patch_models import PatchApplyResult
from arena.scoring.metrics import f_beta_score, precision, rate, recall, wilson_interval
from arena.validators.base import ValidatorResult

# Executor errors that mean the harness never obtained a verdict, as opposed to
# the suite returning one.
#
# The dividing line is WHO caused it. Anything the candidate's own patch can
# provoke stays a failing suite, because a reviewer must not be able to trade a
# bad score for a harness excuse: a hang (test_execution_timed_out), an output
# flood (test_output_too_large) and a suppressed report (no_test_evidence) are
# all reachable from patched code, and certification proves the reference patch
# triggers none of them. Only conditions the reviewer cannot influence -- an
# absent backend, an unusable pack command, or the run's own wall-clock deadline
# cutting the suite short -- are inconclusive.
_INCONCLUSIVE_EXECUTION_ERRORS = frozenset(
    {
        "test_deadline_truncated",
        "docker_required_but_unavailable",
        "docker_image_not_present",
        "local_execution_disabled",
        "workspace_not_found",
        "empty_test_command",
    }
)


def _is_inconclusive(tests: TestExecutionResult | None) -> bool:
    """Whether the harness, not the suite, is why there is no pass."""
    if tests is None:
        return False
    error = tests.error or ""
    return error in _INCONCLUSIVE_EXECUTION_ERRORS or error.startswith(
        ("test_runner_unavailable", "invalid_test_command")
    )


def is_execution_backed(case: BenchmarkCase) -> bool:
    """Whether this case can produce EXECUTION-BACKED validation evidence.

    Only a runnable test suite counts. A structural validator is a deterministic
    lexical/AST heuristic, documented as a weaker signal than execution, and a
    heuristic can be satisfied by a patch that does not repair the defect -- so a
    case gated only by validators is excluded from validated_case_rate entirely
    (neither a pass nor a failure). Its validator evidence still surfaces in
    structural_pass_rate and repair_confidence, so nothing is lost, only
    un-conflated.

    The test must actually be RUNNABLE, which is what the executor requires: a
    case declaring `tests_required: true` with `run_tests: false` (or no
    test_command) never executes anything, yet counting it here put it in the
    denominator where `tests_passed is not True` marked it a miss for every
    reviewer forever -- publishing 0% repair even for the exact ground-truth fix.

    This is the single definition of that predicate. It is shared with the
    placeholder built for a case that crashed inside the harness, which must
    agree: if the two drift, a crashed validator-only case is charged to the
    reviewer as a miss inside a denominator that otherwise excludes it.
    """
    return bool(case.execution.run_tests and case.execution.test_command)


def score_deterministic_case(
    case: BenchmarkCase,
    review: CaseResult,
    patch: PatchApplyResult,
    tests: TestExecutionResult | None,
    validators: list[ValidatorResult],
    beta: float,
) -> DeterministicCaseScore:
    # Detection is judged at file granularity (concept + file); line-level
    # localization is a separate, finer signal and never collapses a detected
    # bug into a miss.
    true_positives = review.bugs_matched
    false_negatives = max(review.bugs_total - review.bugs_matched, 0)
    false_positives = review.false_positive_count
    value_precision = precision(true_positives, false_positives)
    value_recall = recall(true_positives, false_negatives)
    structural_ran = bool(validators)
    structural_passed = all(item.passed for item in validators) if validators else None
    tests_ran = bool(tests and tests.ran)
    tests_passed = tests.passed if tests_ran and tests is not None else None
    patch_provided = bool(patch.patch_text.strip())
    reasons: list[str] = []

    all_bugs_detected = review.bugs_total > 0 and review.bugs_matched >= review.bugs_total
    if not review.bug_found:
        reasons.append("detection_failed")
    elif case.validation.detection_requirement == "all_bugs" and not all_bugs_detected:
        # Some, but not all, seeded bugs were found; a partial review is not a
        # complete one for a case that requires every bug.
        reasons.append("incomplete_bug_detection")
    if patch.unsafe_paths:
        reasons.append("patch_unsafe_paths")
    if patch.touched_protected:
        reasons.append("patch_touched_protected_files")
    if case.validation.patch_required and not patch_provided:
        reasons.append("patch_required_but_missing")
    if case.validation.patch_required and not patch.applied:
        reasons.append("patch_apply_failed")
    tests_required = case.execution.run_tests or case.validation.tests_required
    if tests_required and tests_passed is not True:
        # Distinguish "the suite said no" from "we never got an answer". A
        # missing runner or Docker backend is the environment's doing, not a
        # verdict on the patch, and recording it as tests_failed reads as a
        # reviewer miss while hiding that the run was degraded. Anything the
        # PATCH caused -- a hang, an output flood, a suppressed report -- stays
        # tests_failed. deterministic_pass is False either way, since nothing was
        # confirmed; the evidence just says which happened.
        reasons.append("execution_inconclusive" if _is_inconclusive(tests) else "tests_failed")
    if case.validation.structural_validators and structural_passed is not True:
        reasons.append("structural_validation_failed")
    if false_positives > case.validation.max_false_positives:
        reasons.append("false_positive")
    # A repair needs SOME gate to be judged at all: a patch-required case with
    # neither tests nor a validator cannot confirm anything, so a clean patch
    # apply alone must never count as a repair (a no-op patch would pass).
    has_gate = tests_required or bool(case.validation.structural_validators)
    if case.validation.patch_required and not has_gate:
        reasons.append("no_execution_evidence")
    # ...but only test execution is EXECUTION-BACKED evidence, so eligibility for
    # validated_case_rate is decided by the shared predicate below.
    validation_eligible = is_execution_backed(case)

    return DeterministicCaseScore(
        case_id=case.id,
        detected_bug=review.bug_found,
        localized_correctly=review.correct_line,
        patch_provided=patch_provided,
        patch_applied=patch.applied,
        tests_ran=tests_ran,
        tests_passed=tests_passed,
        structural_validation_ran=structural_ran,
        structural_validation_passed=structural_passed,
        true_positive_count=true_positives,
        false_positive_count=false_positives,
        false_negative_count=false_negatives,
        precision=round(value_precision, 6),
        recall=round(value_recall, 6),
        f1=round(f_beta_score(value_precision, value_recall), 6),
        f_beta=round(f_beta_score(value_precision, value_recall, beta), 6),
        patch_apply_score=1.0 if patch.applied else 0.0,
        execution_score=1.0 if not tests_required or tests_passed else 0.0,
        structural_score=1.0 if not validators or structural_passed else 0.0,
        deterministic_pass=not reasons,
        validation_eligible=validation_eligible,
        failure_reasons=reasons,
    )


def aggregate_deterministic_metrics(
    cases: list[CaseResult],
    beta: float,
    total_cost: float,
    total_latency_ms: int,
) -> DeterministicMetrics:
    eligible = [case for case in cases if case.deterministic_case_score]
    scores = [case.deterministic_case_score for case in eligible if case.deterministic_case_score]
    detection_tp = sum(score.true_positive_count for score in scores)
    detection_fn = sum(score.false_negative_count for score in scores)
    fp = sum(score.false_positive_count for score in scores)
    case_count = len(scores)
    detection_precision = precision(detection_tp, fp)
    detection_recall = recall(detection_tp, detection_fn)
    detected_cases = sum(score.detected_bug for score in scores)
    localized_cases = sum(score.detected_bug and score.localized_correctly for score in scores)
    # validated_case_rate is computed only over cases that HAVE an executable
    # validation gate. A case with no gate cannot confirm a repair, so it is
    # excluded rather than counted as a pass (which a no-op patch could earn) or
    # as a uniform failure for every reviewer.
    validatable = [
        case
        for case in eligible
        if case.deterministic_case_score and case.deterministic_case_score.validation_eligible
    ]
    validatable_count = len(validatable)
    # Cases the rate could not cover, counted so the gap between the pack's size
    # and the denominator is explicit rather than something a reader must infer.
    # Derived from eligibility, which is a property of the CASE: keying it off
    # whether validators actually ran made it a property of the REVIEWER, so the
    # same pack reported 0 excluded cases for a reviewer whose patch never
    # applied and 4 for one whose did.
    non_execution_backed_count = sum(1 for score in scores if not score.validation_eligible)
    validated_tp = sum(
        bool(case.deterministic_case_score and case.deterministic_case_score.deterministic_pass)
        for case in validatable
    )
    validated_fn = validatable_count - validated_tp
    validated_precision = precision(validated_tp, fp)
    validated_recall = recall(validated_tp, validated_fn)
    validated_ci = wilson_interval(validated_tp, validatable_count)
    patch_provided = sum(score.patch_provided for score in scores)
    patch_applied = sum(score.patch_applied for score in scores)
    tests_ran = sum(score.tests_ran for score in scores)
    tests_passed = sum(score.tests_passed is True for score in scores)
    validation_ran = sum(score.structural_validation_ran for score in scores)
    validation_passed = sum(score.structural_validation_passed is True for score in scores)
    # Evidence-derived dimensions, read from the per-case attribution. Repair
    # success, like validated_case_rate, is over validatable cases.
    complete_repairs = sum(case.case_status == "complete_repair" for case in validatable)
    bug_complete = sum(
        bool(case.bug_repairs) and all(repair.detected for repair in case.bug_repairs)
        for case in eligible
    )
    judged_findings = [
        finding for case in eligible for finding in case.scored_findings if not finding.is_neutral
    ]
    supported = sum(finding.is_true_positive for finding in judged_findings)
    return DeterministicMetrics(
        detection_precision=round(detection_precision, 6),
        detection_recall=round(detection_recall, 6),
        detection_f1=round(f_beta_score(detection_precision, detection_recall), 6),
        detection_f_beta=round(f_beta_score(detection_precision, detection_recall, beta), 6),
        validated_precision=round(validated_precision, 6),
        validated_recall=round(validated_recall, 6),
        validated_f1=round(f_beta_score(validated_precision, validated_recall), 6),
        validated_f_beta=round(f_beta_score(validated_precision, validated_recall, beta), 6),
        beta=beta,
        deterministic_pass_rate=(
            round(validated_tp / validatable_count, 6) if validatable_count else None
        ),
        # Unit-coherent case-level repair rate (see DeterministicMetrics). Equal
        # to deterministic_pass_rate; named for the leaderboard/product surface.
        validated_case_rate=(
            round(validated_tp / validatable_count, 6) if validatable_count else None
        ),
        validated_case_rate_ci_low=validated_ci[0] if validated_ci else None,
        validated_case_rate_ci_high=validated_ci[1] if validated_ci else None,
        validated_eligible_case_count=validatable_count,
        non_execution_backed_case_count=non_execution_backed_count,
        complete_repair_rate=(
            round(complete_repairs / validatable_count, 6) if validatable_count else None
        ),
        bug_completeness_rate=round(bug_complete / case_count, 6) if case_count else 0.0,
        supported_claim_rate=(
            round(supported / len(judged_findings), 6) if judged_findings else None
        ),
        localization_rate=rate(localized_cases, detected_cases),
        patch_apply_rate=rate(patch_applied, patch_provided),
        test_pass_rate=rate(tests_passed, tests_ran),
        structural_pass_rate=rate(validation_passed, validation_ran),
        false_positives_per_case=round(fp / len(scores), 6) if scores else 0.0,
        cost_per_validated_fix=round(total_cost / validated_tp, 6) if validated_tp else None,
        latency_per_case_ms=round(total_latency_ms / len(scores), 2) if scores else 0.0,
    )
