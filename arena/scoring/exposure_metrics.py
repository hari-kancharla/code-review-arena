"""Aggregate per-case exposure stamps into cohort results, and gate publication.

The denominator rule here is not negotiable, and is deliberately the SAME
expression the headline metric uses in
``arena.scoring.deterministic_scorer.aggregate_deterministic_metrics``: a
cohort's population is the validation-eligible cases assigned to it, and its
numerator is the ones whose repair was validated. A cohort rate is therefore
provably ``validated_case_rate`` restricted to a cohort, and the cohort counts
sum to ``validated_eligible_case_count`` by construction.

Inventing a second definition is the exact unit-coherence failure this codebase
already diagnosed once, in the deprecated ``validated_precision``/``f1``/
``f_beta`` (a case-level numerator over a finding-level denominator), and had to
re-learn when ``non_execution_backed_case_count`` was keyed off the reviewer
rather than the case.
"""

from __future__ import annotations

import hashlib

from arena.core import limits
from arena.core.models import CaseResult, CohortResult, ExposureCohort, ExposureMetrics, RunMetadata
from arena.scoring.metrics import (
    min_detectable_difference,
    newcombe_difference_interval,
    wilson_interval,
)

_COHORTS: tuple[ExposureCohort, ...] = (
    "pre_cutoff",
    "post_cutoff",
    "undetermined",
    "not_applicable",
)

# Above this share of undetermined cases the split is not a split: too much of
# the pack sits in neither arm for a comparison between the arms to describe it.
_MAX_UNDETERMINED_SHARE = 0.20


def _validatable(cases: list[CaseResult]) -> list[CaseResult]:
    return [
        case
        for case in cases
        if case.deterministic_case_score and case.deterministic_case_score.validation_eligible
    ]


def _validated(case: CaseResult) -> bool:
    return bool(case.deterministic_case_score and case.deterministic_case_score.deterministic_pass)


def aggregate_exposure_metrics(
    cases: list[CaseResult],
    *,
    metadata: RunMetadata,
    pack_checksum: str | None,
    source_labels: dict[str, str | None],
) -> ExposureMetrics:
    """Cohort counts and rates always; the pre/post difference only when powered."""
    validatable = _validatable(cases)

    cohorts: list[CohortResult] = []
    by_cohort: dict[str, list[CaseResult]] = {name: [] for name in _COHORTS}
    for case in validatable:
        # An unstamped case (a review-mode run, or one from before this field
        # existed) is undetermined, never quietly counted as post-cutoff.
        by_cohort.setdefault(case.exposure_cohort or "undetermined", []).append(case)

    for name in _COHORTS:
        members = by_cohort.get(name, [])
        passed = sum(_validated(case) for case in members)
        interval = wilson_interval(passed, len(members))
        cohorts.append(
            CohortResult(
                cohort=name,
                eligible_case_count=len(members),
                validated_case_count=passed,
                validated_case_rate=(passed / len(members)) if members else None,
                ci_low=interval[0] if interval else None,
                ci_high=interval[1] if interval else None,
            )
        )

    reason_counts: dict[str, int] = {}
    for case in validatable:
        reason = case.exposure_cohort_reason or "unstamped"
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

    # Repository x cohort. At small pack sizes cohort membership is close to
    # collinear with which repository a case came from, and publishing the
    # cross-tab is the only honest way to show that a "gap" may be a difference
    # between two projects rather than between two eras.
    composition: dict[str, dict[str, int]] = {}
    for case in validatable:
        label = source_labels.get(case.case_id) or "authored"
        bucket = composition.setdefault(label, {})
        key = case.exposure_cohort or "undetermined"
        bucket[key] = bucket.get(key, 0) + 1

    pre = next(item for item in cohorts if item.cohort == "pre_cutoff")
    post = next(item for item in cohorts if item.cohort == "post_cutoff")
    undetermined = next(item for item in cohorts if item.cohort == "undetermined")

    suppression: list[str] = []
    if metadata.model_knowledge_cutoff is None:
        suppression.append("no_declared_cutoff")
    if pre.eligible_case_count < limits.MIN_COHORT_CASES:
        suppression.append("cohort_too_small:pre")
    if post.eligible_case_count < limits.MIN_COHORT_CASES:
        suppression.append("cohort_too_small:post")
    split_total = (
        pre.eligible_case_count + post.eligible_case_count + undetermined.eligible_case_count
    )
    if split_total and undetermined.eligible_case_count / split_total > _MAX_UNDETERMINED_SHARE:
        suppression.append("too_many_undetermined")
    # Suppresses by DEFAULT, because the default declaration is "unknown". That is
    # intended: a cutoff-based difference is not defensible for a reviewer that
    # might have been searching the web while it reviewed. Do not remove this
    # check because the field looks unused -- its whole job is to be unset.
    if metadata.reviewer_retrieval != "none":
        suppression.append("retrieval_not_ruled_out")

    # Computed and published even when the difference is suppressed: this is the
    # field that states the cohort sizes' resolving power before a reader has to
    # work it out. The base is the POOLED rate across both arms rather than one
    # arm's, so a single-case cohort scoring 1.0 cannot drive it to a degenerate
    # answer.
    pooled_n = pre.eligible_case_count + post.eligible_case_count
    pooled_rate = (
        (pre.validated_case_count + post.validated_case_count) / pooled_n if pooled_n else 0.5
    )
    detectable = min_detectable_difference(
        pre.eligible_case_count, post.eligible_case_count, pooled_rate
    )

    gap: float | None = None
    gap_low: float | None = None
    gap_high: float | None = None
    if not suppression:
        gap = round((pre.validated_case_rate or 0.0) - (post.validated_case_rate or 0.0), 6)
        interval = newcombe_difference_interval(
            pre.validated_case_count,
            pre.eligible_case_count,
            post.validated_case_count,
            post.eligible_case_count,
        )
        if interval is not None:
            gap_low, gap_high = interval

    key_material = (
        f"{pack_checksum}|{metadata.model_knowledge_cutoff}|{metadata.cutoff_grace_days}|1"
    )
    return ExposureMetrics(
        analysis_version=1,
        declared_cutoff=metadata.model_knowledge_cutoff,
        cutoff_basis=metadata.model_knowledge_cutoff_basis,
        cutoff_grace_days=metadata.cutoff_grace_days,
        reviewer_retrieval=metadata.reviewer_retrieval,
        cohorts=cohorts,
        cohort_reason_counts=reason_counts,
        source_composition=composition,
        exposure_gap=gap,
        exposure_gap_ci_low=gap_low,
        exposure_gap_ci_high=gap_high,
        min_detectable_gap=detectable,
        publishable=not suppression,
        suppression_reasons=suppression,
        exposure_analysis_key=hashlib.sha256(key_material.encode("utf-8")).hexdigest()[:16],
    )
