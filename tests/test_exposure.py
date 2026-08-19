"""Cohort assignment for training-data exposure.

Every rule here exists to stop a flattering result being manufactured, so the
tests are written as attempts to manufacture one: an undated case sliding into
the favourable cohort, a borderline case tipping a comparison, an authored case
claiming immunity it has not earned.
"""

import pytest

from arena.benchmark.exposure import assign_cohort, effective_exposure_date, pack_exposure_profile
from arena.core import limits
from arena.core.models import BenchmarkCase

_BUG = {
    "summary": "s",
    "files": [{"path": "a.py", "line_ranges": [{"start": 1, "end": 1}]}],
    "concepts": ["c"],
}


def _case(origin: dict | None = None, case_id: str = "case1") -> BenchmarkCase:
    payload: dict = {
        "id": case_id,
        "title": "t",
        "category": "correctness",
        "severity": "high",
        "stack": ["python"],
        "description": "d",
        "input": {},
        "ground_truth": {"bugs": [dict(_BUG)]},
    }
    if origin is not None:
        payload["origin"] = origin
    return BenchmarkCase.model_validate(payload)


def _derived(date: str) -> dict:
    return {
        "kind": "derived_public",
        "public_fix_date": date,
        "public_fix_date_basis": "git_committer_date",
        "source_label": "pypa/packaging",
    }


CUTOFF = "2026-01-01"
GRACE = 90


# --- The fail-closed rules -------------------------------------------------


def test_an_undated_case_is_never_placed_in_a_cohort():
    """The single most important rule: unknown must not become "new".

    If a case with no date drifted into post_cutoff, every pack could be made to
    look uncontaminated simply by declining to record dates.
    """
    assignment = assign_cohort(_case(), None, CUTOFF, GRACE)

    assert assignment.cohort == "undetermined"
    assert assignment.reason == "no_date"
    assert assignment.cohort != "post_cutoff"


def test_an_undeclared_cutoff_leaves_every_case_undetermined():
    assignment = assign_cohort(_case(_derived("2020-01-01")), None, None, GRACE)

    assert assignment.cohort == "undetermined"
    assert assignment.reason == "no_cutoff_declared"


def test_an_authored_case_is_not_applicable_when_the_pack_is_undated():
    assignment = assign_cohort(_case({"kind": "authored"}), None, CUTOFF, GRACE)

    assert assignment.cohort == "not_applicable"
    assert assignment.reason == "authored"


def test_an_authored_case_in_a_published_pack_is_still_judged_on_the_pack_date():
    """Authored is not a claim of immunity.

    A pack in a public repository is its own answer key: case.yaml carries
    must_mention and acceptable_fix_keywords, and reference.patch carries the
    gold answer outright. An authored case published long before a model's
    cutoff is exactly as memorizable as a mined one.
    """
    assignment = assign_cohort(_case({"kind": "authored"}), "2020-05-05", CUTOFF, GRACE)

    assert assignment.cohort == "pre_cutoff"
    assert assignment.exposure_date == "2020-05-05"
    assert assignment.basis == "pack_published_date"


# --- The guard band --------------------------------------------------------


@pytest.mark.parametrize(
    ("fix_date", "expected", "reason"),
    [
        # Comfortably before and after.
        ("2024-01-01", "pre_cutoff", "dated"),
        ("2027-01-01", "post_cutoff", "dated"),
        # One day outside the band on each side: the first case that counts.
        ("2025-10-02", "pre_cutoff", "dated"),
        ("2026-04-02", "post_cutoff", "dated"),
        # Exactly on the band edge (cutoff -/+ 90 days). Closed on both sides, so
        # a borderline case can never be the one that tips a published
        # difference.
        ("2025-10-03", "undetermined", "within_guard_band"),
        ("2026-04-01", "undetermined", "within_guard_band"),
        # Exactly on the cutoff itself.
        ("2026-01-01", "undetermined", "within_guard_band"),
    ],
)
def test_guard_band_boundaries(fix_date, expected, reason):
    assignment = assign_cohort(_case(_derived(fix_date)), None, CUTOFF, GRACE)

    assert (assignment.cohort, assignment.reason) == (expected, reason)


def test_a_wider_band_can_only_move_cases_out_of_cohorts():
    dated = _case(_derived("2025-10-02"))

    assert assign_cohort(dated, None, CUTOFF, 90).cohort == "pre_cutoff"
    assert assign_cohort(dated, None, CUTOFF, 365).cohort == "undetermined"


# --- Two exposure channels -------------------------------------------------


def test_the_earlier_of_the_fix_date_and_the_pack_date_wins():
    case = _case(_derived("2026-06-01"))

    date, basis = effective_exposure_date(case, "2019-01-01")

    assert (date, basis) == ("2019-01-01", "pack_published_date")
    assert assign_cohort(case, "2019-01-01", CUTOFF, GRACE).cohort == "pre_cutoff"


def test_a_later_pack_date_does_not_hide_an_early_fix():
    case = _case(_derived("2019-01-01"))

    date, basis = effective_exposure_date(case, "2026-06-01")

    assert date == "2019-01-01"
    assert basis == "git_committer_date"


# --- Composition -----------------------------------------------------------


def test_pack_profile_counts_kinds_and_dated_cases():
    cases = [
        _case(_derived("2025-01-01"), "a"),
        _case({"kind": "authored"}, "b"),
        _case(None, "c"),
    ]

    profile = pack_exposure_profile(cases, None)

    assert profile["derived_public"] == 1
    assert profile["authored"] == 1
    assert profile["unknown"] == 1
    assert profile["dated"] == 1
    assert profile["undated"] == 2


def test_the_minimum_cohort_size_is_pinned():
    """Raising this to make a number appear is a defect, not a tuning change."""
    assert limits.MIN_COHORT_CASES == 8
    assert limits.DEFAULT_CUTOFF_GRACE_DAYS == 90
