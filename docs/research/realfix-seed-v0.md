# RealFix Seed v0

**Status: a five-case methodology seed.** This is **not** RealFix Pilot v1, it is
**not** paper scale, and it supports **no** conclusions about model performance.
The eventual RealFix Pilot v1 target remains a diverse set of **at least 12**
cases; this seed exists to prove the end-to-end methodology and serve as a small
executable example inside the harness repository.

It converts real, historical bug fixes from mature open-source Python projects
into execution-verified Code Review Arena cases using the merged deterministic
importer (`arena import-fix`) and the existing Docker certification ladder, with no
production-code changes. Every case here reached the existing `verified` level
through Docker.

These are **synthetic reverse-review cases derived from real fixes**: the buggy
tree is the source *before* the historical repair and the synthetic `pr.diff` is
the inverse of the real change. They are **not** the original bug-introducing pull
requests. Their ground truth is anchored in a real defect, a real maintainer fix,
and a real regression test.

## Accepted cases (3)

| case_id | repo | license | category | severity | changed src LOC | baseline | reference | determinism | mutants | mutation evidence | level |
|---|---|---|---|---|---|---|---|---|---|---|---|
| attrs_frozen_error_message_001 | python-attrs/attrs | MIT | correctness | low | 8 | fails | passes | 3 runs hold | 0 viable | unavailable (0 viable mutants) | verified |
| click_shared_default_precedence_001 | pallets/click | BSD-3-Clause | correctness | medium | 16 | fails (exit 1) | passes | 3 runs hold | 20 | 55% killed | verified |
| rich_table_padding_width_001 | Textualize/rich | MIT | correctness | low | 16 | fails | passes | 3 runs hold | 20 | 80% killed | verified |

`attrs_frozen_error_message_001` is **execution-verified; mutation evidence is
unavailable because the current operators produced zero viable mutants** for its
small change. It is not claimed to have demonstrated mutation adequacy; its
assurance rests on the deterministic baseline-fails / reference-passes verdict
across three runs, not on killing mutants. The other four cases additionally show
mutation kill rates above the 0.5 certification threshold -- the two `packaging` cases at
100%, the highest in the pack.

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

## Candidate pool and admission

- Candidates screened: **25** (pallets/click, pallets/jinja, pallets/markupsafe, python-attrs/attrs, Textualize/rich).
- Accepted: **3**. Rejected: **22**. Deterministic registry: `realfix-seed-v0-rejections.jsonl`.
- Rejection reasons: `unrelated_changes` (the fix commit also touches CHANGES/docs/lint), `no_clear_bug_evidence`, `test_only`, `flaky`.

### Key methodology finding: changelog-bundling friction

The importer (correctly, by design) requires **every** path changed between the
buggy and fixed commits to fall under a declared source selector or the tests
root. Mature projects almost always bundle a changelog/docs edit into the fix
commit, which makes that commit unimportable as-is. Only "clean" commits whose
entire diff is source + tests are usable, and those are rare (Click had ~4 clean
bug-fix commits since 2025-09-01; Jinja and MarkupSafe had none). This is the
dominant reason the certified yield is far below 12, and it was **not** worked
around by modifying the importer.

## Distributions (accepted cases)

- Repositories: pallets/click (1), python-attrs/attrs (1), Textualize/rich (1) — 3 distinct repos.
- Licenses: MIT (2), BSD-3-Clause (1).
- Categories: correctness (3). *(Single category — below the diversity target.)*
- Commit dates: 2025-09-22, 2026-01-23, 2026-03-14 — **3/3 on or after 2025-09-01**; none before 2025-01-01.
- Diff size: all five are **small** (< 30 changed source lines: 2, 8, 16, 16, 28). No medium or substantial fixes in this seed.

## Docker environment

- Image tag: `arena-realfix-seed:0` (built from `docker/realfix_seed/`).
- Local image id: `sha256:8a1c4957a9a5c3b87b13f00a08bb54272f9cbd7129f519f6cc98bbef5fa500a6`.
- Base: `python:3.11-slim` (Python 3.11.15). Pinned: `pytest==8.3.5`, `hypothesis==6.140.3` (import-time dependency of attrs' test module only; no property-based test is exercised). `PYTHONPATH=/workspace/src`.
- No Arena source, no repository checkout, no network at test time (`--network none`), no credentials, no mutable installation during a run.

## Certification (Docker)

`arena certify-pack benchmark_sets/realfix_seed_v0 --limit 20 --determinism-runs 3`
→ pack level **verified**; all 5 cases `verified`.

- Mutation: click 55% (20 mutants), rich 80% (20 mutants); attrs has 0 viable
  mutants, so it carries no mutation evidence and rests on baseline-fails +
  reference-passes + determinism (how the existing ladder treats zero-viable-mutant
  cases).
- Determinism: baseline-fails / reference-passes held across 3 runs each.
- Deterministic rebuild: re-importing all five cases reproduces byte-identical
  case directories; the pack checksum is idempotent.

## Control runs (Docker, full mode)

| reviewer | validated repair |
|---|---|
| reference-patch | 3 / 3 |
| control:perfect_patch | 0 / 3 |
| control:bad_patch | 0 / 3 |
| control:keyword_gamer | 0 / 3 |
| control:detects_no_patch | 0 / 3 |
| control:malformed_patch | 0 / 3 |

`reference-patch` (apply the gold patch) validates 3/3 — the meaningful
perfect-patch control for new cases. The failure controls all validate 0/3, and
keyword-gamer obtains no validated repair. `control:perfect_patch`,
`control:bad_patch` and `control:keyword_gamer` are fixture oracles keyed to the
bespoke `v1`/`audit_*` case ids; they have no answer for new cases and produce no
patch (0/3). This is a property of those control reviewers, not of the seed cases,
and was not worked around by changing production code.

## Pack integrity

- `pack.sha256`: `a6c44967fcdff0f97797c62336bb8cb05720eb8265f0d636393893324b5219f2`
- Case ids are disjoint from `v1`, `audit_v1`, `audit_v2`; the shipped packs are
  byte-for-byte unchanged.

## Dataset packaging decision

This seed **vendors complete runnable snapshots** for reproducibility: each case
includes the required source and test trees at the pinned commits. Three cases
already add hundreds of files for that reason. **Continuing this model inside the
core harness repository will not scale cleanly** to a 12+ case Pilot v1 or beyond.

Future RealFix expansion should therefore be maintained as a **separate versioned
dataset repository or content-addressed release artifact**, not grown indefinitely
inside `code-review-arena`. The core `code-review-arena` repository will remain the
harness and may keep only this small seed as an executable example. (That external
dataset repository / artifact system is intentionally **not** implemented in this
change; this is only the packaging decision.)

## Limitations

- **Count:** 3 verified cases, far below the 12+ Pilot v1 target. Admission
  standards were not lowered; the changelog-bundling friction and the
  mutation-adequacy bar limited the clean, certifiable yield.
- **Diversity:** single category (correctness), all small fixes, 3 repos, 2
  licenses — below the size/category/repo-count targets for Pilot v1.
- **attrs case** has zero viable mutants; its strength rests on the deterministic
  baseline-fails/reference-passes verdict rather than mutation evidence.
- **Controls:** the fixture-bound perfect/bad/keyword controls do not generalize
  to new cases; `reference-patch` is used as the general perfect-patch oracle.
- This is a methodology seed. It is not paper scale and supports no statistical
  conclusions about model performance.
