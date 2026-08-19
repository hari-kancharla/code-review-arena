# Training-data exposure

Every case in `realfix_seed_v0` is derived from a public GitHub repository. The
upstream fix, its regression test, and usually a pull-request discussion
explaining the defect are plausibly in the pretraining corpus of any model being
evaluated. A model may therefore reproduce a repair it remembers rather than one
it reasoned to.

This is the standard and most damaging criticism of any benchmark built from
public repository history, and it is well evidenced. Published analyses of
SWE-bench report that models recall benchmark-repository file paths far more
often than paths from repositories outside the benchmark, that the great
majority of SWE-bench-Lite issues predate current model cutoffs, and that a
substantial share of "successful" patches correspond to solutions present in the
issue text itself.

Arena's answer is disclosure, not a cleanliness claim. It cannot prove a model
has not seen a case; nobody can. What it can do is record **when each case's
answer became public**, record **what the operator claims about the model**, and
publish the split between them with the statistics that say whether the split
means anything.

## Not the same thing as `arena lint-cases`

Two different questions share the word "contamination":

| Question | Where |
|---|---|
| Does a case leak its **own answer** inside the pack (ground-truth vocabulary visible in the diff, comments or test names)? | `arena/benchmark/contamination.py`, surfaced by `arena lint-cases` |
| Was a case's answer **already public** before the model was trained? | `arena/benchmark/exposure.py`, surfaced on every `full`/`patch` run |

They are unrelated. A case can be spotless under the first and heavily exposed
under the second.

## What a case declares

Every case may carry an `origin` block, and every case in every shipped pack now
does:

```yaml
origin:
  kind: derived_public          # authored | derived_public | unknown
  public_fix_date: '2025-08-21'
  public_fix_date_basis: git_author_date
  source_label: pypa/packaging
```

`arena import-fix` writes this itself. The date is read from the commit object
with `git show -s --format=%aI%n%cI` and is the **earlier** of the commit's
author and committer dates, with `public_fix_date_basis` naming which one won.
It is not a human's recollection, it is part of the commit the object id already
pins, so a re-import of the same commit pair reproduces it byte for byte.

An import spec may declare a date itself. That declaration is honoured **only
when it is earlier** than the commit's own date — the case where a human knows
the defect was disclosed in an issue or advisory before the fix landed. A
declared date later than the commit's is discarded in favour of the commit's,
because "later" is the flattering direction: it would move a case toward the
post-cutoff cohort and make a model look less likely to have memorized it.
Disagreement always resolves toward more assumed exposure, never less.

Authored packs (`v1`, `audit_v1`, `audit_v2`) declare `default_origin_kind:
authored` once in their manifest. Those cases were written for this benchmark
rather than lifted from public history.

**Authored is not a claim of immunity.** A pack in a public repository is its own
answer key: `case.yaml` carries `must_mention` and `acceptable_fix_keywords`, and
`reference.patch` carries the gold answer outright. A manifest may therefore
declare `published_date`, and a case's effective exposure date is the earlier of
its upstream fix date and its pack's publication date.

## What a run declares

A knowledge cutoff is an **operator claim about a vendor claim**. The harness
never infers one from a model id and never asserts one itself:

```bash
arena run benchmark_sets/realfix_seed_v0 --reviewer <spec> --mode full \
  --model-knowledge-cutoff 2025-12-01 \
  --model-cutoff-basis vendor_documented \
  --model-cutoff-source "https://…/model-card" \
  --reviewer-retrieval none
```

The claim is accepted only complete. A bare date is rejected before the run
starts, because a cohort split resting on an assertion nobody can attribute or
check is worse than no split at all.

`--cutoff-grace-days` can only be **widened** past its 90-day default, enforced
by the CLI. Narrowing it would let an operator manufacture a cohort out of
borderline cases.

## Cohorts

| Cohort | Meaning |
|---|---|
| `pre_cutoff` | Exposure date is more than the guard band before the declared cutoff |
| `post_cutoff` | Exposure date is more than the guard band after it |
| `undetermined` | No cutoff declared, no date, or inside the guard band |
| `not_applicable` | Authored case in a pack with no declared publication date |

Two rules matter more than the rest:

- **An undated case is never placed in a cohort.** It is never imputed and never
  defaulted to the wall clock. If "we do not know" became "it is new", any pack
  could be made to look clean by declining to record dates.
- **The guard band is closed on both sides.** A case exactly `grace` days from
  the cutoff is `undetermined`, so a borderline case can never be the one that
  tips a published difference. Crawl-to-train lag, backports into maintenance
  branches and post-fix discussion all smear the date at which content could
  enter a corpus; a hard boundary would be indefensible.

## The published difference, and when it is withheld

`exposure_gap` is `validated_case_rate(pre) - validated_case_rate(post)`. Cohort
rates use exactly the expression the headline metric uses, so a cohort rate is
provably `validated_case_rate` restricted to a cohort and the cohort counts sum
to `validated_eligible_case_count`.

The difference is withheld entirely unless every one of these holds, and the
machine-readable reasons are published when it is not:

| Reason | Meaning |
|---|---|
| `no_declared_cutoff` | Nothing to compare against |
| `cohort_too_small:pre` / `:post` | Either arm below `MIN_COHORT_CASES` (8) |
| `too_many_undetermined` | More than 20% of the split population is undetermined |
| `retrieval_not_ruled_out` | `--reviewer-retrieval` is not `none` |

`retrieval_not_ruled_out` **suppresses by default**, because the default
declaration is `unknown`. That is intended: a cutoff argument says nothing about
a reviewer that could have been searching the web while it reviewed, and for a
`custom-command` reviewer running as a host process the harness cannot prove
otherwise.

Cohort counts, cohort rates with Wilson intervals, the reason census, the
repository × cohort cross-tab and `min_detectable_gap` are published **either
way**. `min_detectable_gap` is the number that states the cohort sizes' resolving
power before a reader has to work it out: on today's five-case seed it is `1.0`,
meaning nothing short of the entire range would be detectable.

## What this is not

- **Not a memorization measurement.** `exposure_gap` describes a split that is
  confounded with case difficulty, repository, language and era. Cases are not
  randomly assigned to cohorts; at small pack sizes cohort membership is close to
  collinear with which repository a case came from, which is exactly why
  `source_composition` is published beside it.
- **Not proof a `post_cutoff` case is unseen.** It excludes neither retrieval at
  review time, nor post-training and fine-tuning data, nor the plain fact that
  the model already knows the repository, its idioms, its API and its test suite,
  with only this one fix being new.
- **Not a leaderboard gate.** Exposure is disclosure and never eligibility.
  Gating rank on a self-declared field would create a direct incentive to declare
  whatever value is convenient.
- **Not a p-value.** There is deliberately no significance test. At these cohort
  sizes a p-value adds nothing the interval does not already say, and invites
  multiplicity problems the harness would then have to defend.

## Recomputing it yourself

Every case's cohort, exposure date, basis and reason are persisted per case in
`run.json`, and the run manifest records the declared cutoff, its basis, its
citation and the guard band. A third party can therefore re-derive the entire
analysis at a different cutoff or a wider band from a stored run, with no
reviewer calls and no access to the pack. `exposure_analysis_key` identifies the
(pack, cutoff, band, analysis version) tuple a published number came from.
