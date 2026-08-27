# Changelog

All notable changes to this project are recorded here. The format follows the
Keep a Changelog conventions.

## Unreleased

### Added

- **RealFix grown from five certified cases to 24**, still through `arena mine-fixes`
  and `arena import-fix`, without lowering the certification bar. Mining 32 local
  clones produced 3747 clean candidates and 1168 fix-ish diffs of four files or
  fewer. Nineteen new cases certified (fail-to-pass, mutant kill ≥ 0.5, pinned
  `arena-realfix-seed:0` image); the original five seed cases are unchanged.
  Hand-authored attempts that did not certify were rejected for contamination
  vocabulary in the inverse diff, mutation kill below 0.5, Docker collect/run
  failure (old `packaging/` layout, extra test deps), or unsafe fixture paths —
  not by relaxing those gates. At cutoff `2025-12-01` with a 90-day guard the pack
  splits 11 `pre_cutoff` / 10 `post_cutoff` / 3 undetermined, so
  `min_detectable_gap` falls from 1.0 to 0.470. That is still an enormous
  detectable difference: coarse exposure disclosure, not ranking power.
  Provenance, licenses and notices for installer, more-itertools, idna and tomli
  ship with the pack.

### Security

- **A passing test run now requires machine-readable proof.** The verdict was the
  process exit status, produced by a process running the candidate's own patched code,
  so two lines at the top of the file under review (`import os; os._exit(0)`) ended the
  interpreter with status 0 during collection and a genuinely failing suite was recorded
  as a validated repair. The same trick worked by planting a workspace-root module that
  shadows anything pytest imports (`_pytest`, `pluggy`, `iniconfig`), which no blocklist
  of module names can close. pytest now writes a JUnit report to a path outside the
  workspace, and a pass additionally requires that report to show at least one test and
  no failures or errors; sabotage that stops the suite also stops the report. The
  residual case — a patch that locates the report and forges it — needs the reviewer
  isolation boundary and is documented in `arena/execution/test_evidence.py`.
- **A reviewer that can read the answer key is no longer ranked as if it were blind.**
  `custom-command` ran with the repository as its working directory and the full host
  environment, so a wrapper sat one relative path from `benchmark_sets/<case>/
  reference.patch` and was handed every credential in the operator's shell. A wrapper
  that reads the oracle and echoes it scored `detection_f_beta=1.000`,
  `validated_case_rate=1.000`, `localization_rate=1.000` — a perfect result for zero
  review. The reviewer now starts in its own payload directory with an allowlisted
  environment (name what a wrapper legitimately needs in `ARENA_PASSTHROUGH_ENV`), which
  ends the casual read and the credential exposure. A host process can still read by
  absolute path, so that residual is recorded rather than hidden: runs carry
  `metadata.reviewer_oracle_reachable`, and default leaderboard eligibility requires it
  to be false (unknown is treated as unsafe). Such runs stay visible with
  `--include-unverified`.
- A **skipped** test is no longer accepted as evidence. JUnit's `tests` attribute counts
  skips, so requiring `tests > 0` let a patch raise `pytest.skip()` from the file under
  review and turn a failing suite green — exit 0, a well-formed report, zero failures,
  seeded bug untouched. A pass now requires at least one *executed* test. A surgical skip
  that leaves unrelated tests running remains open and is documented in
  `arena/execution/test_evidence.py`; closing it needs the certified reference run's
  expected node set.
- The evidence gate no longer disables itself for `python -W error -m pytest` (or any
  other interpreter flag before `-m`). Matching `-m` only at `argv[1]` silently fell back
  to the exit-code-only oracle, and disagreed with `certify._is_pytest_command`, so a case
  could be certified under pytest semantics and then scored as an unrecognised runner.
- `POST /runs` no longer lets an unauthenticated caller run a local command or make
  the server fetch a URL of their choosing. The reviewer spec is now checked against
  an allowlist of in-process reviewers; `custom-command` (which spawns a process) and
  `openai:`/`http:` (which issue outbound requests from the server, reachable as
  server-side request forgery against localhost or a cloud metadata endpoint) require
  `ARENA_SERVER_ALLOW_LOCAL_EXECUTION`. A reviewer type added later is gated by default.
- A candidate patch can no longer force a green test run. `PROTECTED_BASENAMES` covered
  `pytest.ini` but not `.pytest.ini`, `pytest.toml`, `.pytest.toml` or a workspace-root
  `pytest.py`, all of which pytest consults or imports before the pack's own config, so
  a patch that repaired nothing could make the hidden suite exit 0.
- A non-ASCII token header returned 500 rather than 401, and a non-ASCII
  `ARENA_API_TOKEN` broke authentication for every request; tokens are now compared as
  bytes, in constant time.

### Fixed

- The audit report published any run it found, so a later `invalid` run (a backend that
  never executed, a pack whose checksum did not match) overwrote a real measurement and
  flipped the reviewer's published rate to zero. Reports now use the same eligibility
  policy as the leaderboard and record how many runs were excluded.
- `coverage_rate` counted cases that crashed inside the harness as covered, and
  `complete` ignored them, so a half-failed run published as a clean, fully covered
  measurement. Coverage is now `completed / eligible` and any failed case downgrades the
  run, matching the documented invariant.
- An install without the test runner (the built wheel, the shipped Docker image) scored
  every reviewer 0% with `run_status=complete` and exit code 0. A runner this
  interpreter cannot import is now an unavailable backend: the run is `invalid`, the
  exit code is non-zero, and the message names the missing runner. Added a `run` extra,
  used it in the Dockerfile, and extended the CI packaging job to catch a repeat.
- Concurrent first-touch of a new database raced: two connections both ran the
  migration and the loser died with `duplicate column name`, and the WAL switch could
  fail with `database is locked`. The migration is serialized with `BEGIN IMMEDIATE` and
  double-checked; the journal-mode switch tolerates a concurrent winner.
- Descendants of a fixture command survived a clean exit (the group was only signalled
  on timeout or overflow), holding ports and CPU into later cases and turning a correct
  repair into `tests_failed`. The process group is now swept on every exit path.
- A test run killed for exceeding the output cap was reported as an ordinary test
  failure, and in Docker mode left its container running. It now reports
  `test_output_too_large` and removes the container.
- Docker availability and image probes ran without a timeout, so an unresponsive daemon
  hung the whole run past every budget. All probes are bounded and fail closed.
- `list_runs` dropped `run_status`/`execution_backend`/`coverage_rate`, so the dashboard
  badged a run the harness had rejected as "Validated". Validity now travels with the
  metrics and outranks the score in the badge.
- The run-level F-beta used whatever beta the last executed case declared, so reordering
  a mixed-beta pack changed the published headline. It is resolved once, up front.
- The `/cases` endpoints hardcoded three pack names and rejected `realfix_seed_v0` with
  a 422 although it ships, validates and is certified in CI. Packs resolve dynamically,
  and pack names are validated as identifiers (a bare `.` previously resolved to the
  benchmark root and snapshotted every shipped pack before failing with a 500).
- The case-trace endpoint attached the *current* pack's diff and ground truth to a
  *stored* score without comparing checksums, so editing a pack in place displayed an
  old score beside a completely different bug as that run's evidence. It now reports
  `pack_drifted` and withholds the context instead.
- An untagged `docker_image` passed the presence check (`docker image ls` filters by
  repository) and then failed at `docker run` as an ordinary failing suite. Image
  references are resolved to the exact tag that will run, so a missing image fails
  closed.
- Cancelling a run left its container running; the container is now removed on the
  cancellation path too.
- A case's `timeout_seconds` is now the budget for the WHOLE case, not for each of its
  commands. A pack declaring several commands could run for N times its declared limit,
  which also defeated the run-level `--max-wall-seconds` deadline that clamps it.
- `arena mine-fixes` reads history in windows instead of one call. git output is capped,
  so a wide enough window overflowed and a single sprawling commit anywhere in range
  aborted the entire walk, discarding every good candidate with it. On overflow the window
  narrows to isolate the offending commit and steps over it.
- The mined scaffold no longer points ground truth at a file the fix CREATED. The reviewer
  reviews the buggy tree, where that file does not exist, so pack validation would reject
  the imported case outright.
- `tests_root` is omitted when the tests share a directory with source (co-located
  `foo.go` / `foo_test.go`) or sit at the repository root. `import-fix` refuses such a
  root, so emitting it handed the author a spec to debug rather than one to fill in.
- Nothing checked the README's pack table or its case total against the manifests, so
  this release's own pack changes (a case removed from `v1`, two added to
  `realfix_seed_v0`) left both stale until they were caught by hand. The dashboard's copy
  of the same table was already guarded; the guard now covers the README's table and its
  prose total too, and fails until every published count agrees with the shipped
  manifests. The CI seed-control checks likewise no longer hard-code the seed's case
  count -- they read it from the manifest, so a pack that gains a case cannot quietly
  pass a check written for the old size.
- The database leaderboard paired a pass count taken over EVERY case with
  `validated_case_rate`, which covers execution-backed cases only, so the fraction on the
  dashboard contradicted the rate beside it and silently re-included the cases the rate
  excludes. Both now use the same population, and the row carries
  `validated_eligible_case_count` so no consumer has to guess the denominator.
- Every dashboard surface now distinguishes "not measured" from zero. A run with no
  execution-backed case previously rendered an empty bar, `0.000`, and a red
  "not validated" badge — reading exactly like a reviewer that repaired nothing.
- The home page advertised a hardcoded pack-case total that no longer matched the shipped
  manifests (33 against 32 actual). The counts are still duplicated there because the page
  renders without an API, but `tests/test_dashboard_pack_table.py` now fails if they drift
  from the manifests, or if a shipped pack is missing from the table entirely.
- **A reviewer can no longer trade a bad score for a harness excuse.** Classifying the
  output-cap kill as a missing backend meant a patch that floods stdout marked the run
  `partial`, and eligibility rejects a partial run outright — even under
  `--include-unverified` — so a reviewer heading for 0% could suppress its own published
  result. The rule is now explicit and symmetric: anything the PATCH can provoke (a hang,
  an output flood, a suppressed report) is a failing suite; only conditions the reviewer
  cannot influence are inconclusive.
- A suite cut short by the run's own wall-clock budget is no longer charged to the
  reviewer. `--max-wall-seconds` clamps a case's test timeout, so the harness could end a
  suite and record `tests_failed` — publishing 0% repair for the exact ground-truth fix on
  a run still labelled `complete` and still leaderboard-eligible. Such a case now reports
  `test_deadline_truncated`, counts as an unavailable backend, and degrades the run. A hang
  against the case's own declared timeout remains the patch's failure.
- `validated_case_rate` (and its `deterministic_pass_rate` alias and `complete_repair_rate`)
  report **not measured** rather than `0.0` when a run had no execution-backed case. A rate
  over an empty denominator is not "repaired nothing", and publishing zero read on a
  leaderboard exactly like a reviewer that failed everything.
- `validated_case_rate` no longer counts a case whose tests never run. Eligibility keyed
  off `tests_required` alone, but the executor runs tests only when `run_tests` and a
  `test_command` are both set, so a case declaring `tests_required: true` with
  `run_tests: false` sat in the denominator as a permanent miss — publishing 0% repair
  even for the exact ground-truth fix.
- A harness failure is no longer recorded as a failing test suite. A missing test runner,
  an absent Docker backend and an unusable pack command all produced `tests_failed`, which
  reads as a reviewer miss and hides that the run was degraded; they now report
  `execution_inconclusive`. The dividing line is who caused it: anything the candidate's
  own patch can provoke — a hang, an output flood (`test_output_too_large`), a suppressed
  report (`no_test_evidence`) — deliberately stays `tests_failed`, so a reviewer cannot
  trade a bad score for a harness excuse. Both kinds still block a validated repair.
- The excluded-case count measured the reviewer rather than the pack: it was derived from
  whether validators actually ran, so the same pack reported 0 excluded cases for a
  reviewer whose patch never applied and 4 for one whose did. It is now derived from case
  eligibility and named `non_execution_backed_case_count`.
- `_missing_runner` raised `ModuleNotFoundError` for a dotted `-m` runner whose parent
  package is absent — the exact condition it exists to convert into a clean skip. It
  escaped `TestExecutor.execute` (whose contract is to return a result, never raise) and
  aborted `certify-pack` and mutation runs, which do not wrap the call.
- The line-range parser split on `str.splitlines()`, which also breaks on VT, FF, NEL,
  U+2028/U+2029 and a lone CR — any of which can appear inside a source line's content,
  silently shifting every derived number in the hunk. It now splits on LF only.
- Candidate mining no longer reads `latest.py`, `contest.py` or `inspec.rb` as tests: the
  convention match is delimiter-anchored, with CamelCase forms kept case-sensitive. The
  old case-insensitive suffix match broke selection in both directions, proposing commits
  that touch no test and hiding genuine fixes whose source file was classified away.
- Mining skips commits containing a path that is neither source nor test (docs, CI
  config). `import-fix` rejects those with `changed_path_unclassified`, so proposing one
  sent the author off to write a full spec for something that cannot be imported;
  `--allow-unclassified` opts back in.
- Mining walks history with a single `git log --name-only` instead of a `diff-tree`
  subprocess per commit, which made the CLI's own advertised maximum `--limit` take
  roughly 45 minutes with a silent terminal.
- A structural validator that could not read its file crashed the whole case: the
  handler caught `(KeyError, OSError, ValueError)` but the path actually raises arena's
  `ValidationError` and `IndexError`. Validators now always fail closed.
- `arena run` printed a pass count over every case beside a rate computed over
  execution-backed cases only, so the two numbers disagreed. Both now use the same
  population and the excluded count is shown.

### Changed

- `validated_case_rate` is now execution-backed only. A case gated solely by a
  structural validator is excluded from the rate rather than counted as a validated
  repair, because a structural validator is a lexical/AST heuristic that a
  non-repairing patch can satisfy. Its evidence still appears in
  `structural_pass_rate`. Only `benchmark_sets/v1` is affected (six of its nine cases are
  execution-backed); `audit_v1`, `audit_v2` and `realfix_seed_v0` are fully executable,
  so their numbers are unchanged.
- Three structural validators matched a substring anywhere in the bug file, so a patch
  adding one dead line earned a validated repair while leaving the defect intact. They
  now establish the property: no awaited per-item call inside a `.map()` callback, an
  updater function passed to the state setter, and the tenant column inside the `WHERE`
  predicate rather than anywhere in the file.
- `spring_boot_null_handling_001` has been removed from `benchmark_sets/v1`. It declared
  `patch_required` with no tests and no structural validators, so it could never confirm a
  repair and penalised every reviewer identically. This harness cannot execute Java, and a
  static substitute proved unsound in both directions — it passed a no-op that rebound the
  Optional to a local and still dereferenced it, while rejecting a genuine
  `isPresent()`-guarded 404. Shipping a case the harness cannot judge is worse than
  shipping one fewer, so it is gone rather than faked. Pack validation now rejects any case
  that demands a patch without a gate, and `benchmark_sets/v1` (nine cases: six
  execution-backed, three structural-only) re-pinned its checksum.
- The dashboard no longer depends on Tailwind. It used no Tailwind utility classes, so
  the framework only supplied a reset, which now lives in `globals.css`; the rendered
  output is unchanged. This unblocked the dependency updates that clear all four
  outstanding high-severity npm advisories.
- Dependabot no longer rewrites the minimum-dependency pins (renamed to
  `constraints/minimum.pip`), and major updates are grouped separately so a breaking
  major cannot hold back security patches.

### Added

- **Training-data exposure disclosure**, the project's answer to the standard and
  most damaging criticism of any benchmark built from public repository history:
  every RealFix case is derived from a public repository, so the upstream fix, its
  regression test and its pull-request discussion are plausibly in the pretraining
  corpus of the model being evaluated, and a model may reproduce a repair it
  remembers rather than one it reasoned to. Until now the harness had no answer --
  the one field that would have supported one, `fixed_date`, lived in hand-written
  evidence files that no code read.
  - Every case now carries an `origin` block, and `arena import-fix` writes it from
    the commit object itself (`%aI`/`%cI`, earlier of the two, with the basis naming
    which won), so it is reproducible rather than remembered. A spec may declare an
    earlier date -- an issue or advisory that disclosed the defect before the fix
    landed -- but a *later* declared date is discarded in favour of the commit's:
    disagreement resolves toward more assumed exposure, never less.
  - `arena run` accepts `--model-knowledge-cutoff` with a mandatory
    `--model-cutoff-basis` and `--model-cutoff-source`. A bare date is refused
    before the run starts, because a cohort split resting on an assertion nobody can
    attribute is worse than no split. `--cutoff-grace-days` can only be widened past
    its 90-day default; narrowing it would let an operator manufacture a cohort out
    of borderline cases.
  - Cases split into `pre_cutoff` / `post_cutoff` / `undetermined` /
    `not_applicable`, stamped per case in `run.json` so a third party can recompute
    the analysis at a different cutoff without rerunning a reviewer. An undated case
    is never imputed into a cohort, and the guard band is closed on both sides, so a
    borderline case can never tip a published difference.
  - `exposure_gap` is withheld unless the comparison is actually powered, with
    machine-readable `suppression_reasons` (`no_declared_cutoff`,
    `cohort_too_small:pre|post`, `too_many_undetermined`, `retrieval_not_ruled_out`).
    Unruled-out reviewer retrieval suppresses **by default**, because live retrieval
    defeats a cutoff argument entirely. Cohort counts, Wilson intervals, the
    repository x cohort cross-tab and `min_detectable_gap` publish either way --
    on today's five-case seed the minimum detectable difference is 1.0, which says
    plainly that nothing short of the entire range would be detectable.
  - Exposure is disclosure and never leaderboard eligibility: gating rank on a
    self-declared field would create a direct incentive to declare a convenient
    value. Documented end to end, including what it is NOT, in
    [docs/training-data-exposure.md](docs/training-data-exposure.md).
- All five RealFix cases were re-imported from fresh upstream clones to carry the
  new provenance. Every vendored tree (`before/`, `after/`, `tests/`, `pr.diff`,
  `reference.patch`) reproduced byte-identically, which is the first end-to-end
  check of the pack's reproducibility claim rather than an assertion of it.
- **Two more real-fix cases, mined end to end**, taking `realfix_seed_v0` from three
  cases to five and proving the ingestion path rather than asserting it. Both come from
  `pypa/packaging` via `arena mine-fixes` (108 candidates from 400 commits in 2.6s),
  and both certify at **verified** with a 100% mutant kill rate — higher than any case
  the pack already shipped:
  - `packaging_name_validation_newline_001` (upstream PR #925): the core-metadata Name
    pattern was anchored with `$`, which Python also matches immediately before a
    trailing newline, so `canonicalize_name("hi\n", validate=True)` returned instead of
    raising `InvalidName` and a line break could reach an RFC822-style metadata field.
  - `packaging_marker_extra_normalization_001` (upstream PR #1024): extra normalization
    rewrote `results[0]` instead of walking the marker list, so
    `python_version >= "3" and extra == "mariadb_connector"` kept its underscore, never
    matched the canonical extra, and the optional dependency was silently skipped.
  Upstream licenses, notices and per-case evidence records ship with the pack. Selecting
  a whole test module rather than the single fail-to-pass test is what earns the
  mutation evidence: the narrow command certified only to `development` (17% and 20%
  kill rate) because mutants elsewhere in the file had nothing to catch them.
- **`arena mine-fixes`**, which proposes historical fixes that could become
  execution-backed review cases. `import-fix` converts one commit pair but infers no
  semantics, so every case cost a hand-written spec and a real-fix pack grew three cases
  at a time. Mining applies the selection rule behind SWE-bench's fail-to-pass signal — a
  non-merge commit that changed both a test file and a source file, with its parent as
  the buggy state — ranks the tightest diffs first, and emits a scaffolded import spec
  with every Git-derivable field filled in and every semantic field left an explicit
  `TODO`. It proposes only: `arena certify-pack` still decides by running the tests at
  both commits. It surfaces 66 candidates from this repository's 192 commits, and 108 from
  400 commits of `pypa/packaging` in 2.6 seconds.
- `validated_eligible_case_count` and `non_execution_backed_case_count` on every run, so
  the denominator behind `validated_case_rate` is explicit. Both are derived from the
  case, not from how a particular reviewer happened to do, so the same pack always
  reports the same split.
- `arena leaderboard` explains an empty default table instead of printing a bare header
  when every run was filtered out as unverified.
- Documented the `shallow-patch`, `openai:<base_url>` and `http:<url>` reviewers, which
  shipped but appeared in no user-facing documentation.

- **CRA-Integrity** (`arena/integrity/`, `benchmark_sets/integrity_pilot_v0`): a
  new evaluation track that asks whether a code reviewer can tell a genuinely
  correct green pull request from one that is green because its validation
  evidence was weakened, bypassed or overfit. Three trust zones (product,
  candidate-owned validation, hidden benchmark oracle) are separated on disk; a
  pair ships a genuine and a compromised pull request against the same task,
  baseline, visible command and reviewer context, and the reviewer is not told
  which it received. Eight pilot pairs cover eight mechanisms and every one is
  certified `verified`: the baseline fails the oracle, the genuine variant passes
  both surfaces, the compromised variant passes visible and fails trusted, the
  oracle is independent of candidate-owned expectations, accepts a structurally
  different legitimate implementation, and kills mutants of the genuine code.
  Metrics are reported separately and never averaged, with pairwise discrimination
  as the headline: the `integrity:blanket-reject` control scores perfect attack
  recall and zero discrimination, which is why recall is never reported alone.
  `arena integrity-audit` runs three trivial validation-diff heuristics as real
  reviewers and fails the pack if any of them can separate the pairs.
- `arena integrity-validate`, `integrity-audit`, `integrity-certify` and
  `integrity-run`, plus `scripts/build_integrity_pack.py` to derive every pack
  diff and repair patch from the trees it describes.

## 0.2.0 - 2026-07-13

First prepared release. No earlier version was tagged or published, so this
entry covers the capabilities the repository ships as of this date.

### Added

- Audit Pack v2 (`benchmark_sets/audit_v2`): a second batch of ten patch-backed
  cases targeting high-impact logic defects across distinct classes (per-unit
  rounding, a fixed-window rate limiter off-by-one, a boolean-precedence
  authorization bypass, a lowest-balance tracker comparing the wrong way, a
  dropped divide-by-zero guard, floor-instead-of-ceiling page counting, a
  truthiness fallback that discards an explicit zero, an off-by-one string
  truncation, a missing backoff clamp, and an or-where-and authorization check).
  Every case is authored leak-free and fully certified (baseline fails, reference
  fix passes, 100% mutant-kill rate), enforced in CI.
- RealFix Seed v0 (`benchmark_sets/realfix_seed_v0`): a three-case methodology
  seed derived from real historical fixes in attrs, click, and rich, certified
  to the `verified` rung through Docker. The cases are synthetic reverse-review
  presentations (the review diff is the inverse of the historical fix, not
  necessarily an original bug-introducing pull request); they demonstrate the
  ingestion-to-certification pipeline end to end and support no conclusions
  about model performance. Upstream licenses and third-party notices ship
  inside the pack, per-case provenance lives under `benchmark_sources/`, the
  research report and rejection registry under `docs/research/`, and the
  pinned `arena-realfix-seed:0` test image builds from `docker/realfix_seed/`.
- Historical-fix ingestion (`arena import-fix`): converts a buggy/fixed commit
  pair from a local Git repository into a candidate reverse-review pack.
  Local-only, offline, deterministic, and non-executing: it reads committed
  objects exclusively, requires a strict human-authored import spec, enforces
  bounded materialization, and proves the generated tree reproduces the exact
  source bytes.
- Trusted evaluation architecture, Phase 1 (pack boundary): strict portable
  pack paths and case ids validated at the schema boundary; strict, bounded
  external schemas with pre-parse byte limits; an exact-by-default reviewer
  output contract (tolerant or repaired output is development-only and makes a
  run non-comparable); immutable pack snapshots, so every security-sensitive
  pack consumer reads a sealed, mutation-checked copy instead of the mutable
  source directory; and Git-authoritative patch application, where the
  post-application Git tree, not handwritten diff parsing, decides what
  changed. See `docs/trusted-evaluation-architecture.md`.
- `shallow-patch` reviewer: a generic adversarial baseline that localizes the bug
  from the shipped reference patch and then proposes a superficial change that
  applies cleanly but repairs nothing. Unlike `keyword_gamer` it needs no per-case
  configuration, so any pack with a reference patch gets a detection-versus-
  validation baseline (detection near 1.0, `validated_case_rate` 0.0).
- v2 metric model: `validated_case_rate` (unit-coherent primary metric that
  replaces the deprecated `validated_f_beta`) plus three evidence dimensions,
  review accuracy (`bug_completeness_rate`), repair success
  (`complete_repair_rate`), and trustworthiness (`supported_claim_rate`).
  Findings carry a per-finding `evidence_status` and cases a `case_status`.
- Per-case Repair Confidence (`basic` / `strong` / `unvalidated`) derived from
  how deeply a repair was validated (tests alone vs tests plus structural
  validators).
- Mutation testing (`arena mutation-test`): generate single-edit mutants of the
  corrected solution (`after/` + `reference.patch`) and measure the test
  kill rate, evidence that a case's tests catch wrong repairs.
- Pack certification ladder (`arena certify-pack`): cases are graded
  draft / development / certified / verified. Certifying requires the buggy
  baseline to fail, the reference solution to pass, and, when a case yields
  viable mutants, a mutation kill rate at or above the threshold; a case with
  zero viable mutants carries no mutation evidence and rests on the baseline
  and reference gates, which the coverage summary reports explicitly. The top
  rung adds an opt-in determinism gate (`--determinism-runs`) that re-runs the
  verdicts to reject flaky cases.
- Content-addressed evidence bundles sealed per run, with `arena verify-run` to
  confirm a run's outputs were not altered after the fact.
- Test and oracle tampering detection: a before/after content manifest catches
  candidate code that rewrites hidden tests mid-run, and tampered cases are
  excluded from aggregate metrics, not just flagged.
- Run validity and coverage: a `run_status` of
  complete / partial / invalid / failed / legacy. A run that needed test
  execution but had no available backend is `invalid`; partial and legacy runs
  stay off the leaderboard.
- Per-case and per-run execution backend (`docker` / `trusted-local` / `none`),
  derived weakest-link first; trusted-local runs are unverified and excluded
  from the default leaderboard unless `--include-unverified` is passed.
- Pack-level `default_docker_image` (inherited by cases that do not set their
  own), plus `Dockerfile.bench` and `scripts/build_bench_image.sh` shipping the
  `arena-bench` sandbox image the packs can run their tests in.
- Local-first HTTP reviewer (OpenAI-compatible) for Ollama, vLLM, LM Studio, and
  llama.cpp.
- Multi-bug ground truth (`ground_truth.bugs` with one-to-one finding matching,
  `acceptable_findings` scored neutral); `primary_bug` remains as an alias.
- Patch integrity guards: patches touching tests, pytest config files, or per-case
  `protected_paths` are rejected, as are absolute or `..` diff paths.
- Blind reviewer payload by default: case id, stack, the diff, and a bounded
  set of relevant files. `--reveal-metadata` restores descriptive fields for
  debugging only, and `--reveal-test-output` opts into an openly test-assisted
  run that is recorded as such in the run metadata.
- Bounded reviewer context with a `context_truncated` signal (env-tunable limits).
- Allowlisted environment and POSIX resource limits for locally executed fixture
  commands; `ARENA_PASSTHROUGH_ENV` forwards named variables explicitly.
- `arena pack-hash` content checksums recorded per run and verified against a
  pinned `pack.sha256` (shipped for every pack); leaderboard shows
  pack@checksum with a tamper flag.
- API server: run creation enqueues bounded background jobs (202 + job polling),
  optional `ARENA_API_TOKEN`, and a server-side `ARENA_SERVER_ALLOW_LOCAL_EXECUTION`
  opt-in before HTTP callers may trigger local execution.
- `--max-wall-seconds` / `--max-cost` run budgets with clean partial results.
- `run_manifest.json` per run (harness version, git SHA, pack checksum, sanitized
  reviewer config, budgets, timings) plus a determinism regression test.
- `arena schema` (versioned reviewer output contract), `arena verify-reviewer`
  (one-case contract check with actionable errors), and opt-in `--enable-repair`
  deterministic JSON salvage.
- `arena lint-cases` contamination scan for ground-truth vocabulary leaking into
  diffs, comments, or test names. Removed review-diff lines are scanned too and
  reported under their own surface for audit; the blocking surfaces remain added
  diff lines, after-tree comments, and test names.

### Changed

- Pack path policy: explicit file versus directory semantics. A file path may
  end in an ordinary dot-leaf filename (for example `tests/.coveragerc`) and
  the `+` character is admitted, while dot-prefixed directory components and
  repository-control names (`.git`, `.gitignore`, and similar) are rejected
  wherever they appear; case ids keep the narrower profile.
- `max_wall_seconds` is a hard budget: each case's execution timeout is clamped
  to the remaining run deadline rather than only checked between cases.
- A single case-level `proposed_patch` is applied as the repair instead of an
  arbitrary finding's patch; competing finding patches are reported as ambiguous.
- Storage migrated to schema v2: run validity and coverage are persisted, pre-v2
  rows are marked legacy, and the API leaderboard is gated to eligible runs.
- The dashboard leads with `validated_case_rate` and the evidence dimensions
  across the home, leaderboard, audit report, runs, and verify pages, and marks
  `validated_f_beta` deprecated.
- Refreshed the dashboard theme, typography, and layout, and reworked the home
  page structure and panels. The audit report page uses compact case-study
  cards with the detection-versus-validation gap laid out as a grid, the
  leaderboard fits the page without horizontal scrolling, the cases table
  collapses requirements into one expandable column, and the content area was
  widened with the documentation index at three cards per row.
- Reviewer names read as plain labels such as "Control: Perfect Repair" and
  "Shallow Patch" across the dashboard, with the control-baseline note wherever
  controls are shown; the duplicate control tag next to reviewer names was
  dropped, along with unreferenced reviewer helper functions.
- Scoring: detection is judged at file granularity with line precision reported
  separately (`localization_rate`); the false-positive penalty is capped
  (`false_positive_penalty_cap`); execution evidence overrides keyword
  fix-quality in patch/full mode; `correct_line` derives from an explicit
  line-match quality instead of magic score values.
- Structural validators match comment-stripped source so comment-only "fixes"
  fail; the JWT validator inspects function bodies via AST.
- Control reviewers renamed `mock:*` → `control:*` (module
  `arena.reviewers.controls`); `mock:*` stays as a deprecated alias for one
  release. `semantic_matcher` renamed to `concept_matcher` (it is lexical).
- SQLite opens with WAL + busy timeout and versioned idempotent migrations;
  arena refuses databases newer than it understands.
- Test commands parse strictly (string, argv list, or list of argv commands; no
  shell operators) and are checked at `arena validate` time.
- Default paths resolve against `ARENA_PROJECT_ROOT` or a discovered project
  root so commands behave the same from any directory.

### Fixed

- The certification baseline gate requires a genuine test failure: for pytest
  baselines only exit code 1 (tests ran and at least one failed, not a timeout)
  counts, so a buggy state that fails to import, collects no tests, or hits an
  internal error can no longer stamp a case certified. Non-pytest runners keep
  the generic nonzero-failure handling.
- Per-case `scoring.weights` are now actually applied.
- Container test commands route through `python -m pytest` so a case whose tests
  import a top-level workspace module collects correctly (the bare `pytest`
  script does not put the workspace root on `sys.path`).

### Security

- Packs are rejected at load time if they contain symlinks or special files
  (sockets, devices, FIFOs): an untrusted pack must hold only regular files and
  directories so copying it into a workspace cannot escape its own tree.
- The Docker backend never pulls a missing image (the image name comes from the
  untrusted pack); the image must already be present locally, and `--pull never`
  backs that up.
- Hardened Docker execution: no network, all capabilities dropped,
  no-new-privileges, read-only root with a `noexec` tmpfs, a non-root user, and
  pids / memory / cpu limits, with the resolved image digest recorded. An
  image-declaring case never silently falls back to local execution.
- Local fixture commands run with an allowlisted environment, an isolated empty
  HOME and TMPDIR, POSIX resource limits, a process-tree kill on timeout,
  capped output, and pytest plugin autoload disabled.
- Historical-fix imports run Git isolated and offline: a private empty HOME and
  config, no hooks, pager, editor, or credential helpers, replacement refs
  ignored, lazy fetch disabled, and bounded output with timeouts. The importer
  never runs `checkout`, `clone`, `fetch`, or repository code, and generates
  patches in a fresh private repository so source-repo configuration cannot
  influence them.
