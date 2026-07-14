# Changelog

All notable changes to this project are recorded here. The format follows the
Keep a Changelog conventions.

## Unreleased

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
