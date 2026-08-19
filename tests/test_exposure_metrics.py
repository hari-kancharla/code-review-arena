"""Cohort aggregation and the publication gate.

The gate exists because the tempting failure of this whole feature is to publish
a difference between two tiny cohorts and let a reader treat it as a measurement
of memorization. These tests are written as attempts to get a number published
that should not be.
"""

import pytest

from arena.core import limits
from arena.core.models import (
    CaseResult,
    DeterministicCaseScore,
    ReviewerResponse,
    RunMetadata,
    ScoreBreakdown,
)
from arena.scoring.exposure_metrics import aggregate_exposure_metrics


def _case(case_id: str, cohort: str, *, eligible: bool = True, passed: bool = True) -> CaseResult:
    return CaseResult(
        case_id=case_id,
        title="t",
        category="correctness",
        severity="high",
        ground_truth_summary="s",
        response=ReviewerResponse(raw_response="{}"),
        scored_findings=[],
        breakdown=ScoreBreakdown(),
        score=0.0,
        bug_found=False,
        correct_file=False,
        correct_line=False,
        line_match="none",
        false_positive_count=0,
        exposure_cohort=cohort,
        exposure_cohort_reason="dated",
        deterministic_case_score=DeterministicCaseScore(
            case_id=case_id,
            validation_eligible=eligible,
            deterministic_pass=passed,
            detected_bug=False,
            localized_correctly=False,
            true_positive_count=0,
            false_positive_count=0,
            false_negative_count=1,
            precision=0.0,
            recall=0.0,
            f1=0.0,
            f_beta=0.0,
            patch_provided=True,
            patch_applied=True,
            patch_apply_score=1.0,
            tests_ran=True,
            structural_validation_ran=False,
            structural_score=0.0,
            execution_score=1.0 if passed else 0.0,
        ),
    )


def _metadata(**overrides) -> RunMetadata:
    fields = {
        "prompt_version": "1",
        "benchmark_version": "v1",
        "model_knowledge_cutoff": "2025-01-31",
        "model_knowledge_cutoff_basis": "vendor_documented",
        "model_knowledge_cutoff_source": "https://example.invalid/card",
        "cutoff_grace_days": 90,
        "reviewer_retrieval": "none",
    }
    fields.update(overrides)
    return RunMetadata(**fields)


def _aggregate(cases, **meta):
    return aggregate_exposure_metrics(
        cases,
        metadata=_metadata(**meta),
        pack_checksum="abc",
        source_labels={case.case_id: "acme/repo" for case in cases},
    )


def _powered_cases(pre_pass: int, post_pass: int) -> list[CaseResult]:
    n = limits.MIN_COHORT_CASES
    cases = [_case(f"pre{i}", "pre_cutoff", passed=i < pre_pass) for i in range(n)]
    cases += [_case(f"post{i}", "post_cutoff", passed=i < post_pass) for i in range(n)]
    return cases


# --- The gate --------------------------------------------------------------


def test_a_difference_is_not_published_for_tiny_cohorts():
    """One case against one case must never produce a headline number."""
    result = _aggregate([_case("a", "pre_cutoff"), _case("b", "post_cutoff")])

    assert result.exposure_gap is None
    assert result.publishable is False
    assert "cohort_too_small:pre" in result.suppression_reasons
    assert "cohort_too_small:post" in result.suppression_reasons


def test_no_declared_cutoff_suppresses_the_difference():
    result = _aggregate(
        _powered_cases(8, 4),
        model_knowledge_cutoff=None,
        model_knowledge_cutoff_basis=None,
        model_knowledge_cutoff_source=None,
    )

    assert "no_declared_cutoff" in result.suppression_reasons
    assert result.exposure_gap is None


def test_unruled_out_retrieval_suppresses_the_difference_by_default():
    """The default declaration is "unknown", and that is meant to suppress.

    A cutoff-based difference says nothing about a reviewer that may have been
    searching the web while it reviewed.
    """
    result = _aggregate(_powered_cases(8, 4), reviewer_retrieval="unknown")

    assert "retrieval_not_ruled_out" in result.suppression_reasons
    assert result.exposure_gap is None


def test_too_many_undetermined_cases_suppress_the_difference():
    cases = _powered_cases(8, 4)
    cases += [_case(f"u{i}", "undetermined") for i in range(20)]

    result = _aggregate(cases)

    assert "too_many_undetermined" in result.suppression_reasons


def test_a_powered_comparison_publishes_a_difference_with_an_interval():
    result = _aggregate(_powered_cases(8, 4))

    assert result.suppression_reasons == []
    assert result.publishable is True
    assert result.exposure_gap == pytest.approx(0.5)
    assert result.exposure_gap_ci_low is not None and result.exposure_gap_ci_high is not None
    # Wide enough at n=8 per arm that the interval is not mistaken for precision.
    assert result.exposure_gap_ci_high - result.exposure_gap_ci_low > 0.3


# --- Unit coherence --------------------------------------------------------


def test_cohort_counts_sum_to_the_validation_eligible_population():
    """The cohorts partition exactly the denominator the headline rate uses."""
    cases = [
        _case("a", "pre_cutoff"),
        _case("b", "post_cutoff"),
        _case("c", "undetermined"),
        _case("d", "not_applicable"),
        # Not validation-eligible: outside the population entirely.
        _case("e", "pre_cutoff", eligible=False),
    ]

    result = _aggregate(cases)

    assert sum(item.eligible_case_count for item in result.cohorts) == 4


def test_an_unstamped_case_counts_as_undetermined_not_post_cutoff():
    """Absence of a stamp must never be read as evidence of freshness."""
    case = _case("a", "pre_cutoff")
    case.exposure_cohort = None

    result = _aggregate([case])

    sizes = {item.cohort: item.eligible_case_count for item in result.cohorts}
    assert sizes["undetermined"] == 1
    assert sizes["post_cutoff"] == 0


def test_the_minimum_detectable_gap_is_published_even_when_suppressed():
    """It is the number that states the cohort sizes' resolving power."""
    result = _aggregate([_case("a", "pre_cutoff"), _case("b", "post_cutoff")])

    assert result.suppression_reasons
    assert result.min_detectable_gap == 1.0


def test_the_source_cross_tab_is_published():
    """At small n a cohort is nearly collinear with which repository it came from."""
    cases = [_case("a", "pre_cutoff"), _case("b", "post_cutoff")]
    result = aggregate_exposure_metrics(
        cases,
        metadata=_metadata(),
        pack_checksum="abc",
        source_labels={"a": "pypa/packaging", "b": "python-attrs/attrs"},
    )

    assert result.source_composition == {
        "pypa/packaging": {"pre_cutoff": 1},
        "python-attrs/attrs": {"post_cutoff": 1},
    }
