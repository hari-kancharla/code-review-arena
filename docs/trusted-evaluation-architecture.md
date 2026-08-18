# Trusted Evaluation Architecture (v0.2; Phase 1A-1D landed)

This is the living design document for the v0.2 milestone. The goal is not "no bugs";
it is enforceable invariants so that any unknown, incomplete, tampered, or untrusted
state is represented as `unknown` / `inconclusive` / `unverified` / `invalid`, never as a
successful or verified result. It is updated as each phase lands.

## Threat model

Benchmark packs and reviewers are both treated as adversarial.

- A pack may contain crafted path fields, symlinks/special files, hidden files, oversized
  inputs, or a `reference.patch` that targets protected files.
- A reviewer may try to read the oracle (reference patch, tests, ground truth), hardcode
  by case id, escape the workspace, exhaust memory/CPU, spawn descendants, exceed budgets,
  or emit malformed output.
- A caller may submit a self-asserted "verified" result or alter evidence after the fact.

The development machine is constrained (no model inference or heavy parallel work locally;
those belong in CI). Local gate = ruff check + ruff format --check + mypy + pytest + pack
validation + contamination check.

## Trust levels (target)

`development` < `self_reported` < `reproducible` < `official_verified`. Trust is computed
from explicit facts and returns reason codes, never inferred from a single boolean. Today
everything the repo produces is at most `self_reported`, because the reviewer process is
not isolated from the oracle (see Reviewer boundary). The public packs ship their own
`reference.patch` and stable case ids, so they are development packs, not a basis for
official rankings.

## Invariants (target, by phase)

- Pack boundary: every pack-supplied path is a validated relative path contained in its
  declared root; execution runs only from an immutable, fully hashed snapshot, never the
  mutable source directory.
- Run validity: `complete` requires zero failed, zero skipped, completed == eligible, and
  every required execution conclusive; coverage = completed / eligible.
- Reviewer boundary: an isolated reviewer receives only the blinded payload (opaque id,
  diff, required source files) and cannot read the repo, packs, tests, or reference patch.
- Execution boundary: a reproducible run records the exact harness commit, pack digest,
  and container image digest; mutable tags are not a trust identity.
- Result attestation: official results require an approved evaluator signature; a caller
  cannot self-assert `official_verified`.

## Boundaries (filled in as phases land)

- Pack boundary: Phase 1. Phase 1A (strict portable paths/ids), Phase 1B (strict
  bounded external schemas, pre-parse byte limits, bounded YAML/JSON structure, exact
  reviewer-output contract with development-only salvage, contextual reviewer-path
  admission, comparability metadata), Phase 1C (immutable pack snapshot: every
  pack consumer reads a sealed, mutation-checked copy, not the mutable source) and
  Phase 1D (Git-authoritative patch application: the actual post-application Git tree,
  not handwritten diff parsing, decides what changed) have landed.
- Run validity: Phase 2.
- Reviewer boundary: Phase 3.
- Execution boundary: Phase 4.
- Result attestation boundary: Phases 4 and 6.

## Deferred limitations (honest, current state)

- Reviewer containment, not isolation. `custom-command` now starts in its own payload
  directory with an allowlisted environment, so it no longer begins inside the repository
  and is no longer handed the operator's shell credentials. That stops the casual read;
  it does NOT stop a determined one, because the process still runs on the host and can
  open `reference.patch` by absolute path. A wrapper that does so scores a perfect 1.000
  on every metric, which measures filesystem access rather than review skill.

  Because that cannot be prevented without a real isolation boundary, it is *recorded*
  instead: every run carries `metadata.reviewer_oracle_reachable`, true for any reviewer
  that could reach the answer key (`custom-command`, and `reference-patch`, which reads it
  by design). Default leaderboard eligibility requires it to be false, and unknown (an
  older run) is treated as unsafe. Such runs remain inspectable with
  `--include-unverified`. All current results are still self-reported; the real fix is the
  reviewer boundary below.
  The Phase 1C snapshot removes mutable-source TOCTOU from Arena's OWN pack consumers; it
  does NOT sandbox the reviewer process.
- No held-out official packs; public packs contain their own answers. An internally
  consistent snapshot does not make a self-reported run official; the external trust
  anchor is still a pinned, out-of-band expected digest (`--expected-pack-sha256`).
- Docker "verified path" is exercised in CI's Docker job (sandbox image build,
  `tests/test_docker_execution.py`, and RealFix seed certification). Reviewer
  isolation is still missing, so Docker execution does not make a run official.
- Per-bug repair attribution is suite-level (overstated for multi-bug cases).
- Snapshot mutation detection bounds the source filesystem at copy time but cannot prove
  the absence of every concurrent-modification race; it fails closed when one is detected.
- Git-authoritative patch application (Phase 1D) makes the post-application Git tree the
  security and scoring authority, but it does NOT sandbox the reviewer process and does not
  make a self-reported run official; run-validity, execution and attestation phases remain
  pending.

---

## Phase 1: Strict pack boundary and immutable snapshot

### Verification matrix (current code, before changes)

| Claim | Verified | Location |
|---|---|---|
| Pack path fields are raw `str`, not a contained type | yes | `arena/core/models.py` GroundTruthFile.path:41, AcceptableFinding.path:63, CaseInput.before_dir/after_dir/tests_dir:101-103, ValidationConfig.protected_paths:140 |
| Only `BenchmarkCase` sets `extra="forbid"`; nested models accept unknown keys | yes | `arena/core/models.py:152` is the only `ConfigDict(extra="forbid")` |
| Pack hash excludes every dotfile/dotdir, so hidden files do not change the digest | yes | `arena/benchmark/pack_hash.py` `_content_files` skips `part.startswith(".")` |
| No immutable snapshot: validation/hash run on the live source, execution re-reads it (TOCTOU) | yes | `pack_checksum`/`load_cases` read the source dir; executors copy from it later |
| Protected-path enforcement rests on the handwritten `+++` parser | yes | `arena/patching/patch_parser.py` `touched_files` parses `+++` only |

### Fields moving to `SafeRelativePath`

`CaseInput.diff`, `CaseInput.before_dir`, `CaseInput.after_dir`, `CaseInput.tests_dir`,
`GroundTruthFile.path`, `AcceptableFinding.path`, `ValidationConfig.protected_paths` (each
element), and any future oracle/patch-allowlist path.

### `SafeRelativePath` rejects

empty paths; absolute POSIX paths; Windows drive (`C:`) and UNC (`\\server`) paths; `.`
and `..` components; backslash separators; NUL and other control characters; Unicode
separator/space lookalikes that could change interpretation under normalization. Containment
against the declared root resolves the root first (never an already-escaped path).

### Pack snapshot data flow (target)

`source dir -> snapshot_pack(source) -> immutable PackSnapshot (regular files only, links/
special rejected, metadata rechecked before/after copy, every file hashed except the
checksum artifact, size/count bounded, validated) -> all loading / certification / mutation
/ scoring / execution read ONLY the snapshot -> snapshot cleaned at end`. We never hash one
tree and execute another.

### Patch enforcement (landed in Phase 1D)

The post-application git tree is authoritative, not the handwritten parser: a temp git repo
is built on the baseline, `git apply --check --index` preflights, the identical bytes apply
atomically (no partial `--reject`), and the actual changed/added/renamed/deleted paths are
read via NUL-delimited git output, each validated with `SafeRelativePath`, with protected-file
changes and unsafe modes rejected on the resulting tree, the workspace rescanned, and any
violation discarding the whole workspace with a structured reason. The same pipeline applies
to candidate, reference, alternate-valid, and known-wrong patches. See "Phase 1D" below.

### Files expected to change

`arena/security/paths.py` (SafeRelativePath type + strengthened validators), `arena/core/
models.py` (apply the type; strict-schema base in the schema sub-step), `arena/benchmark/
pack_hash.py` and a new `arena/benchmark/snapshot.py` (immutable snapshot), `arena/benchmark/
case_loader.py` and `certify.py` and `mutation.py` and `benchmark_runner.py` (load/execute
from the snapshot), `arena/patching/patch_applier.py` and `patch_parser.py` (git-result
patch pipeline), and new tests under `tests/`.

### Test matrix (adversarial + property)

traversal in every path field; absolute POSIX and Windows paths; UNC; quoted git paths;
escaped spaces/tabs/quotes/non-ASCII filenames; rename/copy into protected paths; delete and
recreate protected paths; symlink and gitlink modes; case-only filename collisions; Unicode
normalization collisions; hidden executable files; concurrent replacement during snapshot;
source modified after snapshot; reference patch violating candidate-patch policy. Property
tests fuzz path and diff parsing.

### Path policy (decided)

Portable ASCII profile only: ASCII letters, digits, `_`, `-`, `.`, `/`. No empty,
`.`, or `..` components; no leading/trailing slash; no backslash; no colon; no
component ending in a dot or space; Windows reserved device names rejected
case-insensitively including with an extension (NUL.txt); max total path length
1024, max component length 255. Unicode filenames are NOT supported in pack paths;
this removes confusable/normalization attack classes by construction rather than
blacklisting examples. Case-insensitive and Unicode-normalization COLLISIONS (two
distinct paths that collide on a case-insensitive or NFC filesystem) are enforced
again at snapshot construction in Phase 1C.

Error integration: the Pydantic-facing validators (`SafeRelativePath`, `SafeCaseId`)
raise `ValueError`, so a bad value is a normal Pydantic error with an accurate field
location (e.g. `input.after_dir`, `ground_truth.bugs.0.files.0.path`,
`validation.protected_paths.0`). Domain wrappers (`validate_relative_path`,
`validate_case_id`) translate to Arena's `ValidationError` for filesystem callers.

### Increment status

- 1A strict portable pack paths + case ids at the schema boundary: DONE.
  One shared `_component_error` validator underpins both `SafeRelativePath` and
  `SafeCaseId`, so a case id (which becomes a directory name) gets the exact same
  portable-component policy as a path segment: ASCII profile, no separators, no
  reserved device names even with an extension (NUL.txt), no trailing dot or space,
  bounded length, exactly one component. Applied to `GroundTruthFile.path`,
  `AcceptableFinding.path`, `CaseInput.{diff,before_dir,after_dir,tests_dir}`,
  `ValidationConfig.protected_paths`, `BenchmarkCase.id`, and `CaseManifest.cases`
  (with duplicate-id rejection). `load_cases` enforces the identity invariant
  (manifest id == directory name == `BenchmarkCase.id`) and rejects case-insensitive
  id collisions. Adversarial parametrized suite + Pydantic-location regression +
  property-based (hypothesis) suite + TypeAdapter/JSON/YAML/round-trip/schema/optional/
  default tests + loader identity/collision regressions. Packs load and validate,
  contamination clean, full gate green (333 passed, 4 skipped).
  Total-path-length policy: 1024 chars total, 255 per component (128 for a case id).
  These are feasibility bounds, not the final word: Phase 1C re-checks case-insensitive
  and Unicode-normalization COLLISIONS and real filesystem feasibility on the snapshot.

  Checksum-coverage invariant (added): the pack checksum currently excludes
  dot-prefixed components and `__pycache__`, so content there is not covered by the
  digest and could be swapped silently. Until Phase 1C snapshot hashing covers every
  regular file, (a) no path component or case id may start with a dot, and a case id
  must additionally start with an ASCII alphanumeric (no leading `_`/`-`); and (b)
  `validate_dataset`/`load_and_validate_pack` FAIL CLOSED on any file the checksum omits
  (`pack_hash.unhashable_content`). The checksum exclusion is the root artifact only
  (`relative == pack.sha256`): a nested `<case>/after/pack.sha256` is ordinary pack
  content and is covered by the digest. The four vestigial `.gitkeep` placeholders in `v1`
  (empty `tests/` dirs of `run_tests: false` cases) were removed to satisfy this; they
  were already outside the digest, so the checksum is unchanged. This admission guard is
  removed in Phase 1C once snapshot hashing includes every file.
- 1B strict, bounded external contracts: DONE (see "Phase 1B" below for the full
  reviewer-output contract). Every external nested model inherits a strict base
  (`extra="forbid"`, `strict=True`, `validate_default=True`, `allow_inf_nan=False`); pack,
  reviewer-output, and API-request models all reject unknown fields, type coercion, and
  NaN/inf. Raw byte ceilings are enforced before any YAML/JSON/diff/patch parsing and at
  the ASGI request-body boundary; YAML parsing additionally forbids aliases and duplicate
  keys and bounds depth/node count; collection, string, numeric (dedicated domain limits),
  and identity/uniqueness bounds are enforced. A coherent minimum-dependency CI job
  (Python 3.11, FastAPI 0.110, Pydantic 2.6, HTTPX 0.27) guards the declared floors.
- 1C immutable pack snapshot + rewire load/cert/mutation/exec to the snapshot, including
  case-insensitive / normalization collision rejection: DONE (see "Phase 1C" below). A
  context-managed `PackSnapshot` securely copies the source into a sealed, mutation-checked
  temp tree; every pack consumer reads only the snapshot. The pack checksum now covers every
  regular file except the root `pack.sha256`, so the earlier `unhashable_content` admission
  guard was removed.
- 1D git-result patch pipeline (temp repo + git apply --check + NUL-delimited paths): DONE.
  One shared `arena/patching/git_pipeline.py` transaction applies every patch class inside an
  isolated Git repo and reads the authoritative result from Git; `PatchApplier` and
  `fixed_solution` are wrappers over it, and no `git apply`/`--reject`/handwritten security
  gate remains outside it. See "Phase 1D" below.

---

## Phase 1B: strict external contracts and the reviewer-output contract

### Strict external schemas

Every externally controlled nested model (pack files, reviewer output, API requests)
inherits `_StrictExternal` (`ConfigDict(extra="forbid", strict=True,
validate_default=True, allow_inf_nan=False)`). Strictness does not propagate in Pydantic,
so each nested model inherits it explicitly. Consequences: unknown fields fail, string ->
number / string -> bool coercion fails, defaults are validated, and `NaN`/`Infinity`
fail. Collections are bounded and reject exact duplicates where duplicates cannot add
meaning (bug concepts, must_mention, fix keywords, stack, line ranges, acceptable
findings, protected paths, structural validators); a manifest must contain at least one
case; bug ids are unique after auto-assignment (case-fold included); argv command/token
counts are bounded; numeric fields use dedicated domain limits (score weight 0..100,
penalties 0..100, beta >0..100, timeouts/wall-time/cost), not a reused magnitude cap.
`BenchmarkCase.case_dir` is internal runtime state and is rejected from pack input.

### Raw byte and structure limits

`core/limits.py` centralizes documented operational-safety bounds. Raw byte ceilings are
checked BEFORE parsing for `manifest.yaml`, `case.yaml`, `pr.diff`, `reference.patch`,
ground-truth/source reads, HTTP reviewer responses (on the decoded body, streamed, so a
compressed bomb cannot bypass it; Content-Length is not authoritative), custom-command
output (truncated output is never parsed), and the API request body (a pure ASGI
middleware buffers at most limit+1 bytes and returns 413 before the route runs). YAML
parsing forbids aliases and duplicate keys and bounds depth/node count; the strict JSON
decoder rejects duplicate keys, `NaN`/`Infinity`/`-Infinity`, exponent-overflow
non-finite floats, and excessive depth/node count. These bound memory and parser
amplification only; they do not solve filesystem TOCTOU or immutable traversal (Phase 1C),
and patch semantics remain Phase 1D.

### Reviewer-output contract

Exact is the default comparable contract. The raw response must be exactly one strict
JSON object that validates as `ReviewResult`, with every finding path admitted. The
default parser performs NO tolerant transformation: no Markdown-fence stripping, brace
extraction, trailing-comma removal, bare-list wrapping, field insertion, or finding
dropping. Fenced, prose-wrapped, trailing-comma, bare-list, unknown-field,
one-invalid-finding, duplicate-key, and non-finite inputs are all `invalid`.

Parse status is one of `exact`, `tolerant`, `repaired`, `invalid`:

- `exact` and `invalid` are comparable. `invalid` is a legitimate reviewer-contract
  failure that still scores (it takes the invalid-output penalty); it does not make a run
  untrusted or non-comparable.
- `tolerant` and `repaired` are produced only by development-only salvage
  (`--enable-repair`) and make the run NON-COMPARABLE by default.

Salvage, when enabled, tries exact, then documented tolerant transforms
(`strip_markdown_fence`, `extract_json_object`, `remove_trailing_commas` -- a string-aware
scanner that never touches commas inside string content), then deterministic repair
(`wrap_findings_list`, `default_overall_risk`, `default_review_summary`,
`drop_invalid_findings`). Repair never invents a findings list: a top-level list may be
wrapped, but a dict must explicitly carry `findings` as a JSON array; a missing/null/
non-list `findings` stays invalid. Salvage never calls a model and never relaxes strict
decoding (duplicate keys and non-finite numbers stay rejected). The raw response is always
retained.

Parse evidence is persisted on `ReviewerResponse`: `parse_status`, `parse_actions` (fixed
vocabulary, bounded), `input_finding_count`/`retained_finding_count` (`int | None`),
`dropped_finding_count`, and a bounded `parse_error_summary` that never echoes the full
output. New responses satisfy invariants (invalid has no parsed result or retained
findings; exact has one attempt, no actions, no drops; tolerant records actions and drops
nothing; repaired records an action and satisfies input = retained + dropped).

### Reviewer-path admission

`Finding.file` is reviewer-controlled. A leading `./` is removed and the remainder must
satisfy the same portable relative-path policy as pack paths. A Git `a/`/`b/` prefix is
resolved only against the reviewer-visible known paths (relevant-file keys plus
diff-referenced paths, never ground truth): complete-known keeps the complete path,
stripped-known uses the stripped path, both-known is rejected as ambiguous, neither-known
keeps the complete path, and with no context the prefix is never stripped. A real
top-level `a/` or `b/` directory is therefore not corrupted. The canonical path is stored
on the parsed finding; the raw response keeps the original.

### Comparability and legacy compatibility

`RunMetadata` carries `reviewer_parse_status_counts` and a fail-closed
`non_exact_output_used` (False when every case is exact or invalid, True when any case was
salvaged, None for old runs). The single leaderboard eligibility policy (file and database)
additionally requires `non_exact_output_used is False` by default; True and None are
non-comparable by default but visible with `--include-unverified`. This rides in the
stored run JSON, so `RUN_SCHEMA_VERSION` is unchanged and no SQL migration is needed. Old
saved runs and reviewer responses load unchanged: a response without `parse_status` derives
it from `invalid_output`/`parse_attempts` and loads with unknown (None) counts rather than
fabricated evidence; a run with `non_exact_output_used: null` is treated as legacy/unknown.

---

## Phase 1C: immutable pack snapshots

### The problem

Arena validated and hashed a mutable source pack and then re-read and copied that same
source for reviewer context, certification, mutation and execution. A source file could
therefore change between validation and use, so Arena could validate one tree and execute
another (a time-of-check/time-of-use gap on its own pack consumers).

### Snapshot lifecycle

`arena/benchmark/snapshot.py` provides a context-managed `PackSnapshot`:

```text
mutable source pack
  -> capture the complete source identity (root, every dir incl. empty, every file)
  -> descriptor-anchored secure copy into a private temp tree (mutation-checked, hashed)
  -> require the source identity to match exactly after copying
  -> seal: full manifest (files AND dirs) + manifest digest + public pack checksum
  -> load/validate/context/certify/mutate/execute read ONLY the snapshot
  -> rebuild and re-verify the full seal before evidence is sealed
  -> remove the temp tree (on normal return and on exceptions)
```

```python
with snapshot_pack(source) as snapshot:
    cases = snapshot.load_and_validate()   # case_dir points inside snapshot.root
    ...
```

Once accepted, modifying or deleting the original source does not affect the operation, and
the source is never re-read by that operation.

### Descriptor-anchored traversal (and fallback)

Where the platform supports directory file descriptors (Linux/macOS), traversal is
descriptor-anchored: the root is opened no-follow and its identity verified, every child is
enumerated via `scandir(fd)` and opened relative to its parent descriptor with no-follow
(directories with `O_DIRECTORY`), a child directory's opened identity is checked against what
was discovered, and an untrusted child is never re-resolved from a mutable absolute path. All
descriptors are closed on success and failure. On platforms without `dir_fd` support
(Windows) a conservative path-based fallback re-lstats and compares identity before reading
and rejects symlink/reparse substitutions; its residual limitation is that it cannot fully
close the descriptor-anchoring window, so it fails closed on any detected identity change
rather than claiming an identical guarantee.

### Bounded enumeration

Directory entries are counted against the file, directory and a total-entry cap WHILE the
`scandir` iterator is consumed, so a directory with millions of names is rejected before its
listing is materialized. Deterministic ordering is applied only to the already-bounded
collection. Nothing is ever silently skipped to satisfy a limit.

### Copy verification and mutation detection

The source filesystem is treated as adversarial. Symlinks, FIFOs, sockets and devices are
rejected; hardlink aliases are rejected by `(st_dev, st_ino)`. For each file the opened
descriptor is compared to the discovered entry BEFORE any byte is read (type, device, inode,
size, mtime, link count) and again after reading. Writes loop to completion and treat zero
progress as an error. The copied destination is then re-lstatted, size-checked, re-hashed and
mode-checked before it joins the manifest; a partial destination is removed on failure. The
complete source tree captured before the copy is required to match exactly afterward, which
detects added/removed/renamed/retyped directories (including empty ones) and files.

### Two integrity identities

These are distinct and not interchangeable:

- The **public pack checksum** keeps its compatible algorithm (sorted relative POSIX path,
  NUL, exact bytes, NUL), computed from the snapshot bytes, covering every regular file
  except the root `pack.sha256`. Hidden files and `__pycache__` are included; the three
  shipped packs contain none, so their stored checksums are unchanged. The earlier
  `unhashable_content` admission guard is removed because every regular file is now hashed.
- The **full snapshot seal** is a deterministic manifest digest over the COMPLETE tree:
  every regular file (including the root `pack.sha256`) and every directory (including empty
  ones), each with relative path, kind, size, per-file SHA-256, normalized mode, plus a
  manifest version. `PackSnapshot.verify()` rebuilds this manifest from the snapshot and
  rejects any drift -- added/removed/modified files, root `pack.sha256` modification,
  added/removed empty directories, mode changes, and introduced symlinks or special entries.
  The public checksum is also recomputed, but it is only one component of verification.

### Secure checksum writing

`write_checksum` never reads `pack_checksum` from the mutable source. It snapshots to compute
the intended checksum, atomically writes the root `pack.sha256` (exclusive temp + complete
write + fsync + replace), re-snapshots, and verifies that the public checksum, the stored
checksum and every non-checksum source entry are as expected. On any mismatch it restores the
prior artifact (or removes a newly created one) and raises `source_changed_before_checksum_write`,
so a known-stale checksum is never left behind. No filesystem API can prevent the source from
changing AFTER the command returns; the guarantee is that the artifact written here was
verified against the source state through the operation's completion boundary.

### Resource limits

Observed across the shipped packs: <=84 files, <=74 directories, <=40 KB total, depth <=9.
Selected limits (far above, finite): 4096 files, 4096 directories, 8192 total entries, 256 MiB
total, depth 32, plus the existing 8 MiB per-file cap. Exceeding any limit fails closed.

### Collision and name policy

Distinct entries that collide under case folding, NFC normalization, or both (across full
relative paths and directory components) are rejected, as are names that cannot be encoded
to UTF-8. The pack-declared `SafeRelativePath` policy for case-schema path fields is
unchanged; arbitrary tree entries are allowed only when they can be copied, hashed and
represented safely.

### Evidence and error model

`RunMetadata` records nullable snapshot evidence (file count, total bytes, manifest version,
integrity-verified, and the full `snapshot_manifest_digest`) plus the snapshot content
checksum (the existing `pack_checksum` field); the temporary snapshot path is never persisted,
`RUN_SCHEMA_VERSION` is unchanged, and old runs load with null snapshot fields. Failures raise
`SnapshotError` with a stable reason code (`source_missing`, `root_symlink`, `symlink_found`,
`hardlink_found`, `unsafe_file_type`, `path_collision`, `unsupported_filename`,
`file_count_exceeded`, `directory_count_exceeded`, `entry_count_exceeded`, `total_bytes_exceeded`,
`path_too_deep`, `file_changed_during_copy`, `tree_changed_during_copy`,
`destination_write_failed`, `destination_verification_failed`,
`source_changed_before_checksum_write`, `snapshot_changed_after_sealing`) and never include
file contents. A failed snapshot is never yielded or used, and the temp tree is always removed.

### Honest scope

Snapshots remove mutable-source TOCTOU from Arena's own pack consumers. They do NOT isolate
the reviewer process, and an internally consistent snapshot does not make a self-reported run
official (the external trust anchor remains a pinned out-of-band digest). The descriptor
fallback cannot fully close the anchoring window on platforms without `dir_fd`, and no
filesystem API can prevent a source change after a command returns; both fail closed on
detection. Git-authoritative patch application landed in Phase 1D (below).

---

## Phase 1D: Git-authoritative patch application

### The problem

Patch security and scoring depended partly on handwritten parsing of reviewer diff text
(`touched_files`/`referenced_paths` over `+++`/rename headers, plus string checks for unsafe
paths and modes). A diff's headers are not the same as what Git actually changes (misleading
content lines, omitted extended headers, forged rename headers, quoted paths), so the parser
could disagree with reality. Application also used `git apply` (with `--reject`) directly in
the workspace under host Git configuration.

### One shared transaction

`arena/patching/git_pipeline.py` applies every patch class -- candidate repairs, the
canonical `reference.patch`, certification/determinism reference solutions, and any
patch-based input -- through a single transaction, so a fix protects them all. `PatchApplier`
and `fixed_solution` are thin wrappers; no `git apply`, `--reject`, or handwritten security
gate remains outside the pipeline.

### Isolated Git environment

Every Git subprocess runs with a private empty `HOME`/`XDG_CONFIG_HOME`, an empty
`GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM` plus `GIT_CONFIG_NOSYSTEM=1` and `GIT_ATTR_NOSYSTEM=1`,
an empty `core.hooksPath`, no pager/editor/credential prompts, `protocol.allow=never`,
`commit.gpgsign=false`, `core.autocrlf=false`, a fixed `C` locale, a range-checked caller
timeout, and `GIT_CEILING_DIRECTORIES` so no repository above the workspace is discovered.
Host `~/.gitconfig`, system config, aliases, filters, hooks, external-diff and signing are
never consulted. stdout/stderr are consumed incrementally by reader threads that retain at
most `GIT_OUTPUT_BYTES + 1` bytes per stream and kill the whole process group the moment a
stream (or the combined output) exceeds the ceiling -- the output is bounded WHILE it is
read, not after buffering -- distinguishing `git_timeout`, `git_output_too_large` and a
normal nonzero exit; stdin is delivered without pipe deadlock.

### Workspace and metadata isolation

Each transaction copies the accepted snapshot subtree into a fresh private workspace and
rejects any `.git`, symlink or special entry before Git runs. Git metadata lives in a `.git`
inside that workspace during the transaction and is removed before the workspace is returned,
so candidate code and tests never see `.git`, the index, the patch input (fed via stdin, never
written to the workspace) or Git object storage. A failed transaction deletes the workspace
entirely; only a successful, source-only workspace is preserved for tests and validators.

### Atomic apply and authoritative result

A baseline tree is built with `git add`, then proven byte-exact: the complete baseline path
set is validated first (rejecting dot-prefixed control files such as `.gitattributes` before
Git could honor them), and every baseline index blob is required to equal the Git blob id of
the exact worktree bytes computed with `git hash-object --no-filters` (and the object-id
length must match the detected format). So an attribute/encoding/eol/`ident`/clean-filter
conversion cannot slip a different tree past staging; `git status` is defense in depth, not
byte-equivalence proof. The patch preflights with `git apply --check --index --whitespace=nowarn -`
and, only on success, applies the identical bytes with `git apply --index --whitespace=nowarn -`
-- atomically, with no `--reject` and no partial result. The result tree id is recorded and the
authoritative changes are read with `git diff --raw -z --no-renames baseline result` (rename
detection disabled, so a rename is an explicit delete + add and both endpoints are checked).
`git ls-files -s -z` provides the full-length object ids; object IDs are not assumed to be 40
hex and SHA-256 repositories are supported. The same no-filter byte-equivalence check is then
run against every FINAL index blob, independent of `git status`.

### Path, mode, protection and equivalence policy

Every changed path and every path in the complete resulting index is UTF-8 decoded strictly
and validated under `SafeRelativePath` (rejecting traversal, absolute/UNC/drive, backslashes,
control chars, dot-prefixed, `.git`, Windows-reserved, trailing dot/space, over-length), with
NFC / case-fold / case-folded-NFC collision rejection across the full index. Protected paths
(test-collection control files, plus `.git`/`.gitmodules`/`.gitattributes`) are matched
portably -- normalized separators, NFC, `casefold()`, component-wise -- so case variants like
`Conftest.py` or `Tests/test_x.py` are caught, on both the source and destination side of every
change; the retained `is_protected_path()` diagnostic uses the same matcher. Only `100644`/
`100755` modes are allowed; `120000` (symlink) and `160000` (gitlink) and unknown modes are
rejected. Git metadata is then removed fail-closed (no `ignore_errors`) and its removal
verified, and the workspace is securely rescanned -- directories included (rejecting symlinked
directories, special entries, and any residual `.git` case-variant) and files (rejecting
symlinks, special files, hardlink aliases) -- with the workspace file set required to equal the
index file set. The handwritten parser remains only for non-authoritative diagnostics.

### Evidence and compatibility

`PatchApplyResult` and `CaseResult` gain optional, defaulted authoritative-evidence fields
(patch SHA-256, Git version, object format, baseline/result tree ids, added/deleted paths,
mode changes); `touched_files` and `patch_error` now reflect the actual Git result. A separate
bounded `git_diagnostic` carries a human-readable reason (with private workspace/HOME/metadata
paths scrubbed and never any patch bytes or file contents); `patch_error` stays the stable
machine reason. `PatchApplier` forwards its `timeout_seconds` to the pipeline (range-checked
against centralized bounds), and `fixed_solution` uses the documented default. Old saved runs
load with these fields `None`, `RUN_SCHEMA_VERSION` is not bumped, and the private Git directory
path is never persisted. The reference patch goes through the same policy as a candidate, with
no special trust: a reference patch that fails or violates policy makes the case uncertifiable.

### Honest scope

Git determines what actually changed; handwritten parsing is diagnostic only. An invalid or
rejected patch remains a scored failure, not a crash. Phase 1D does NOT isolate the reviewer
process and does not make a self-reported run official; later run-validity, execution and
attestation phases remain pending. Patch application is POSIX-bounded like the rest of pack
execution; the Git index is the portable mode authority.
