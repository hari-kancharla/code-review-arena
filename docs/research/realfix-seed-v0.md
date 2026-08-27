# RealFix Seed v0

**Status: a 24-case historical-fix pack.** It is **not** paper scale and it
**does not** support ranking conclusions about model performance.
`min_detectable_gap` at cutoff `2025-12-01` with a 90-day guard is **0.470**
(11 `pre_cutoff`, 10 `post_cutoff`) — down from 1.0 on the five-case seed, and
still an enormous detectable difference. The pack is an executable, certified
expansion of the original methodology seed, not RealFix Pilot v1.

It converts real, historical bug fixes from mature open-source Python projects
into execution-verified Code Review Arena cases using the existing deterministic
importer (`arena mine-fixes`, `arena import-fix`) and the existing Docker
certification ladder. Certification, contamination lint, and the
detection-versus-validation split were not relaxed to inflate the count.

These are **synthetic reverse-review cases derived from real fixes**: the buggy
tree is the source *before* the historical repair and the synthetic `pr.diff` is
the inverse of the real change. They are **not** the original bug-introducing pull
requests. Their ground truth is anchored in a real defect, a real maintainer fix,
and a real regression test.

## Accepted cases (24)

Mutation rates below are from Docker certification with `--limit 20`. The five
original seed cases were previously verified across three determinism runs; the
nineteen new cases certified at the same mutant-kill threshold (≥ 0.5) on a
single run and are verified with `--determinism-runs 3` by the Docker CI job.

| case_id | repo | license | category | date | src LOC | mutation |
|---|---|---|---|---|---:|---|
| attrs_frozen_error_message_001 | python-attrs/attrs | MIT | correctness | 2026-03-14 | 8 | 0 viable |
| click_shared_default_precedence_001 | pallets/click | BSD-3-Clause | correctness | 2025-09-22 | 16 | 55% (20) |
| idna_invalid_alabel_001 | kjd/idna | BSD-3-Clause | correctness | 2021-10-03 | 5 | 75% (20) |
| idna_non_ascii_bytes_encode_001 | kjd/idna | BSD-3-Clause | correctness | 2022-04-27 | 5 | 75% (20) |
| idna_non_string_input_001 | kjd/idna | BSD-3-Clause | correctness | 2026-05-05 | 10 | 75% (20) |
| idna_unknown_codepoint_joiner_001 | kjd/idna | BSD-3-Clause | correctness | 2024-04-24 | 8 | 75% (20) |
| installer_path_traversal_001 | pypa/installer | MIT | security | 2026-03-28 | 9 | 62% (13) |
| installer_unbound_executable_001 | pypa/installer | MIT | correctness | 2022-07-16 | 2 | 100% (9) |
| more_itertools_chunked_even_001 | more-itertools/more-itertools | MIT | correctness | 2021-08-01 | 3 | 95% (20) |
| more_itertools_last_reversed_none_001 | more-itertools/more-itertools | MIT | correctness | 2025-07-14 | 2 | 100% (20) |
| more_itertools_numeric_range_reversed_empty_001 | more-itertools/more-itertools | MIT | correctness | 2026-04-10 | 10 | 100% (20) |
| more_itertools_split_after_maxsplit_001 | more-itertools/more-itertools | MIT | correctness | 2022-11-22 | 4 | 95% (20) |
| more_itertools_split_before_empty_001 | more-itertools/more-itertools | MIT | correctness | 2021-03-10 | 3 | 95% (20) |
| more_itertools_windowed_zero_n_001 | more-itertools/more-itertools | MIT | correctness | 2026-04-01 | 7 | 100% (20) |
| packaging_direct_url_at_in_password_001 | pypa/packaging | Apache-2.0 OR BSD-2-Clause | security | 2026-06-05 | 2 | 60% (20) |
| packaging_empty_project_name_001 | pypa/packaging | Apache-2.0 OR BSD-2-Clause | correctness | 2026-06-29 | 16 | 100% (13) |
| packaging_infinity_self_comparison_001 | pypa/packaging | Apache-2.0 OR BSD-2-Clause | correctness | 2026-03-08 | 12 | 100% (10) |
| packaging_license_empty_parens_001 | pypa/packaging | Apache-2.0 OR BSD-2-Clause | correctness | 2025-05-22 | 17 | 100% (16) |
| packaging_marker_extra_normalization_001 | pypa/packaging | Apache-2.0 OR BSD-2-Clause | correctness | 2026-01-05 | 28 | 100% |
| packaging_name_validation_newline_001 | pypa/packaging | Apache-2.0 OR BSD-2-Clause | security | 2025-08-21 | 2 | 100% |
| packaging_nested_extra_normalization_001 | pypa/packaging | Apache-2.0 OR BSD-2-Clause | correctness | 2026-06-16 | 6 | 100% (20) |
| packaging_normalized_name_double_hyphen_001 | pypa/packaging | Apache-2.0 OR BSD-2-Clause | correctness | 2026-06-08 | 2 | 100% (13) |
| rich_table_padding_width_001 | Textualize/rich | MIT | correctness | 2026-01-23 | 16 | 80% (20) |
| tomli_text_mode_load_001 | hukkin/tomli | MIT | correctness | 2022-01-30 | 8 | 65% (20) |

`attrs_frozen_error_message_001` is **execution-verified; mutation evidence is
unavailable because the current operators produced zero viable mutants** for its
small change. It is not claimed to have demonstrated mutation adequacy; its
assurance rests on the deterministic baseline-fails / reference-passes verdict
across three runs, not on killing mutants. Every other case shows a mutant kill
rate at or above the 0.5 certification threshold.

Per-case evidence (repository URL, license URL, buggy/fixed commit ids, issue/PR,
selectors, changed paths, the defect, the exercising regression test, and why the
ground truth is supported) is committed under
`benchmark_sources/realfix_seed_v0/<case-id>/evidence.yaml`.

## Redistribution and third-party notices

The pack vendors complete source and test snapshots from upstream projects so each
case is runnable. The upstream license in effect at each pinned commit is preserved
verbatim under `benchmark_sets/realfix_seed_v0/licenses/`, and
`benchmark_sets/realfix_seed_v0/THIRD_PARTY_NOTICES.md` records, per case, the
project, repository, pinned buggy/fixed commits, applicable license file, and the
included content. Upstream per-file copyright/SPDX notices are retained in the
vendored trees. The notice and license files are covered by `pack.sha256` and the
deterministic rebuild check. This is a redistribution record, not legal advice.

- python-attrs/attrs — MIT (`licenses/attrs-MIT.txt`)
- Textualize/rich — MIT (`licenses/rich-MIT.txt`)
- pallets/click — BSD-3-Clause (`licenses/click-BSD-3-Clause.txt`)
- pypa/packaging — Apache-2.0 OR BSD-2-Clause (`licenses/packaging-*.txt`)
- pypa/installer — MIT (`licenses/installer-MIT.txt`)
- more-itertools/more-itertools — MIT (`licenses/more-itertools-MIT.txt`)
- kjd/idna — BSD-3-Clause (`licenses/idna-BSD-3-Clause.txt`)
- hukkin/tomli — MIT (`licenses/tomli-MIT.txt`)

## Candidate pool and admission

- Repositories mined with `arena mine-fixes`: **32** local full-history clones
  (the four seed projects plus installer, more-itertools, idna, tomli, poetry-core,
  zipp, and other well-known stdlib-adjacent libraries the importer already
  accepts).
- Clean miner candidates: **3747**. Fix-ish subjects with ≤ 4 changed files:
  **1168**.
- Hand-authored import specs attempted this expansion: **28** new cases (plus the
  five already-shipped seed cases, which were kept).
- Newly certified: **19**. Newly rejected: **9**. The original five seed cases
  remain. Deterministic registry: `realfix-seed-v0-rejections.jsonl`.

### Why most candidates still fail

The importer (correctly, by design) requires **every** path changed between the
buggy and fixed commits to fall under a declared source selector or the tests
root. Mature projects almost always bundle a changelog/docs edit into the fix
commit, which makes that commit unimportable as-is. That remains the dominant
filter.

Of the isolated, source+test-only commits that *were* authored into specs this
round, certification still failed for:

| reason | examples |
|---|---|
| `contamination_detected` (vocab in the inverse diff, comments, or test names) | poetry-core union / invalid-constraint, packaging trailing-whitespace URL, packaging prefix trailing-zeros, packaging nested-group parens |
| mutant kill rate below 0.5 | `tomli_loads_typeerror_001` (35%) |
| Docker collect/run failure on both trees | old `packaging/` layout (`packaging_specifier_prefix_epoch_001`), `packaging_invalid_version_pre_letter_001` |
| extra test dependencies not in `arena-realfix-seed:0` | zipp (`jaraco` extras), jinja/markupsafe, platformdirs (`pytest-mock`) |
| `unsafe_tree_path` under `tests_root` | poetry-core fixtures whose names contain `,` |

Admission standards were not lowered. Add-only guard commits that would produce
an empty derived ground-truth range were not imported, because
`test_line_range_derivation.py` requires a behavioural hunk.

## Distributions (accepted cases)

- Repositories: pypa/packaging (8), more-itertools/more-itertools (6), kjd/idna (4),
  pypa/installer (2), python-attrs/attrs (1), pallets/click (1), Textualize/rich (1),
  hukkin/tomli (1) — 8 distinct repos.
- Licenses: Apache-2.0 OR BSD-2-Clause (8), MIT (11), BSD-3-Clause (5).
- Categories: correctness (21), security (3).
- Exposure cohorts at cutoff `2025-12-01`, 90-day guard (from commit-object
  `origin.public_fix_date`, not a stored label): **11 pre** (< 2025-09-02),
  **10 post** (> 2026-03-01), **3 undetermined** (inside the closed guard band).
  Undetermined share of the split population is 12.5% (below the 20%
  `too_many_undetermined` cap).
- Diff size: all cases are small source edits (2–28 changed source lines).

## Docker environment

- Image tag: `arena-realfix-seed:0` (built from `docker/realfix_seed/`).
- Base: `python:3.11-slim`. Pinned: `pytest==8.3.5`, `hypothesis==6.140.3`
  (import-time dependency of attrs' test module only; no property-based test is
  exercised). `PYTHONPATH=/workspace/src`.
- No Arena source, no repository checkout, no network at test time
  (`--network none`), no credentials, no mutable installation during a run.
- Every case sets `execution.docker_image: arena-realfix-seed:0` (no local
  fallback). Image contents were not expanded: candidates that needed extra
  packages were rejected rather than pulling new dependencies into the sandbox.

## Certification (Docker)

`arena certify-pack benchmark_sets/realfix_seed_v0 --limit 20 --determinism-runs 3 --strict verified`

- Mutation: every new case that certified killed at least 60% of viable mutants
  (threshold 50%). `attrs_frozen_error_message_001` still has 0 viable mutants.
- Selecting a whole test module rather than a single fail-to-pass node is what
  earns mutation evidence on large files such as `more_itertools/more.py`.
- Origin dates are read from commit objects by `arena import-fix`.

## Control runs (Docker, full mode)

CI runs `reference-patch` and `shallow-patch` against the current manifest count,
so a pack that gains a case cannot pass with fewer validated repairs than it
ships. `reference-patch` must validate every case; `shallow-patch` must validate
none. The fixture-bound `control:perfect_patch` / `control:bad_patch` /
`control:keyword_gamer` oracles remain keyed to the bespoke `v1`/`audit_*` case
ids and still produce no patch on RealFix cases; that is a property of those
controls, not of this pack, and was not worked around.

## Pack integrity

- `pack.sha256` is regenerated with `arena pack-hash --write` after the new
  snapshots, licenses and notices land.
- Case ids are disjoint from `v1`, `audit_v1`, `audit_v2`.

## Dataset packaging

This expansion still vendors complete runnable snapshots inside
`code-review-arena` so CI can certify them. Twenty-four cases are already
thousands of files; further growth of this model belongs in a versioned dataset
repository or content-addressed artifact. That external system is not part of
this change. The in-tree pack remains the executable example the harness
certifies.

## Exposure resolving power

At cutoff `2025-12-01` and 90-day grace, `min_detectable_difference(11, 10, 0.5)`
is **0.470**. Cohort-size suppression (`MIN_COHORT_CASES=8`) no longer fires.
`exposure_gap` is still unpublished by default because reviewer retrieval is
`unknown`. Even when retrieval is declared `none`, a 47-point detectable gap is
not ranking power: the pack can disclose a split, it cannot tell two nearby
reviewer rates apart.

## Limitations

- **Resolving power:** 24 cases, 11/10 pre/post. `min_detectable_gap` 0.470 is
  better than 1.0 and still far too coarse for model-performance conclusions.
- **Diversity:** eight repos, but packaging and more-itertools dominate; almost
  all cases are small correctness edits in Python libraries with no extra test
  dependencies.
- **attrs case** has zero viable mutants; its strength rests on the deterministic
  baseline-fails/reference-passes verdict rather than mutation evidence.
- **Controls:** the fixture-bound perfect/bad/keyword controls do not generalize
  to new cases; `reference-patch` is used as the general perfect-patch oracle.
- **tomli** snapshots include the upstream `tests/` data tree (~800 files). That
  is the honest tests-root copy; it was not trimmed to shrink the pack.
- Changelog-bundling still caps yield. This pack is what certified honestly, not
  what a lower bar would have admitted.
