"""Training-data exposure: when a case's answer became public, and what that means.

This measures ONE leakage channel: whether the upstream fix a case was derived
from predates a declared model knowledge cutoff. Every realfix case is built from
a public repository, so the fix, its regression test and usually a pull-request
discussion explaining it are plausibly in the pretraining corpus of any model
under evaluation, and a model may reproduce a repair it remembers rather than one
it reasoned to. Published studies of GitHub-derived benchmarks report exactly this
effect, which is why the split is worth measuring at all.

What ``post_cutoff`` does NOT mean:

- not that the model has not seen the case. It excludes neither retrieval at
  review time, nor post-training and fine-tuning data, nor the plain fact that the
  model already knows the repository, its idioms, its API and its test suite, with
  only this one fix being new.
- not that a declared cutoff is true. A cutoff is an operator claim about a
  vendor claim; the harness cannot verify one and never infers one from a model id.

Because none of that can be proven, this module never certifies a case as clean.
It assigns cohorts and discloses them, and every ambiguity resolves toward MORE
assumed exposure, never less.

Unrelated to arena/benchmark/contamination.py, which asks whether a case leaks its
own answer inside the pack. Same English word, different question.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from arena.core.models import BenchmarkCase, ExposureCohort


@dataclass(frozen=True)
class CohortAssignment:
    """One case's exposure verdict, with the evidence that produced it."""

    cohort: ExposureCohort
    exposure_date: str | None
    basis: str | None
    # Machine-readable: authored | no_cutoff_declared | no_date | within_guard_band | dated
    reason: str


def effective_exposure_date(
    case: BenchmarkCase, manifest_published_date: str | None
) -> tuple[str | None, str | None]:
    """The earliest date this case's answer could have been read, and its basis.

    Two independent channels expose a case, and the earlier one governs:

    - the upstream fix becoming public in its own repository, and
    - this pack being published, because case.yaml carries ``must_mention`` and
      ``acceptable_fix_keywords`` and reference.patch carries the gold answer
      outright. A pack in a public repository is its own answer key.

    Both are ``YYYY-MM-DD``, so lexicographic and chronological order coincide and
    ``min`` is exact without constructing a date object.
    """
    origin = case.origin
    fix_date = origin.public_fix_date if origin is not None else None
    if fix_date is None and manifest_published_date is None:
        return None, None
    if fix_date is None:
        return manifest_published_date, "pack_published_date"
    if manifest_published_date is None:
        basis = origin.public_fix_date_basis if origin is not None else None
        return fix_date, basis
    if manifest_published_date < fix_date:
        return manifest_published_date, "pack_published_date"
    return fix_date, (origin.public_fix_date_basis if origin is not None else None)


def assign_cohort(
    case: BenchmarkCase,
    manifest_published_date: str | None,
    cutoff: str | None,
    grace_days: int,
) -> CohortAssignment:
    """Place one case relative to a declared knowledge cutoff.

    The rules are ordered, and each exists to prevent a specific way of
    manufacturing a flattering result.
    """
    origin = case.origin
    # 1. An authored case was written for this benchmark rather than lifted from
    #    public history, so an upstream-fix cutoff simply does not apply to it.
    #    It is not thereby "clean": if the pack itself is public, rule 4 still
    #    judges it on the pack's publication date.
    if origin is not None and origin.kind == "authored" and manifest_published_date is None:
        return CohortAssignment("not_applicable", None, None, "authored")

    exposure_date, basis = effective_exposure_date(case, manifest_published_date)

    # 2. With no declared cutoff there is nothing to compare against. The case is
    #    undetermined -- never quietly assumed to be on the favourable side.
    if cutoff is None:
        return CohortAssignment("undetermined", exposure_date, basis, "no_cutoff_declared")

    # 3. No date at all. This is the fail-closed rule that matters most: an
    #    undated case is NEVER imputed to either cohort and never defaulted to
    #    the wall clock, because "we do not know" must not become "it is new".
    if exposure_date is None:
        return CohortAssignment("undetermined", None, None, "no_date")

    # 4. Compare, with a symmetric guard band. A hard boundary is indefensible:
    #    crawl-to-train lag, backports into older branches and post-fix
    #    discussion all smear the date at which content could enter a corpus.
    #    The band is closed on both sides -- a case exactly `grace` days away is
    #    undetermined, not a cohort member -- so a borderline case can never be
    #    the one that tips a published difference.
    exposure = date.fromisoformat(exposure_date)
    boundary = date.fromisoformat(cutoff)
    grace = timedelta(days=grace_days)
    if exposure < boundary - grace:
        return CohortAssignment("pre_cutoff", exposure_date, basis, "dated")
    if exposure > boundary + grace:
        return CohortAssignment("post_cutoff", exposure_date, basis, "dated")
    return CohortAssignment("undetermined", exposure_date, basis, "within_guard_band")


def pack_exposure_profile(cases: list[BenchmarkCase], published_date: str | None) -> dict[str, int]:
    """Counts by origin kind plus dated/undated, for the run manifest and CLI.

    Published whether or not a cutoff was declared, because the composition of a
    pack is a fact about the pack and a reader should see it before any rate.
    """
    profile = {
        "authored": 0,
        "derived_public": 0,
        "unknown": 0,
        "dated": 0,
        "undated": 0,
    }
    for case in cases:
        kind = case.origin.kind if case.origin is not None else "unknown"
        profile[kind] = profile.get(kind, 0) + 1
        exposure_date, _ = effective_exposure_date(case, published_date)
        profile["dated" if exposure_date is not None else "undated"] += 1
    return profile
