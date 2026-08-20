# Historical-fix ingestion (`arena mine-fixes`, `arena import-fix`)

## Finding candidates first (`arena mine-fixes`)

`import-fix` needs a commit pair and a spec. Finding the pair by hand is the
reason a real-fix pack grows slowly, so `arena mine-fixes` proposes them:

```bash
arena mine-fixes --repo /path/to/local/repository --limit 500 --max-files 6
arena mine-fixes --repo /path/to/repo --json --output candidates.json
```

A candidate is a **non-merge commit that changed both a test file and a source
file**. That pairing is the selection rule behind SWE-bench's fail-to-pass
signal: the tests the fix added are exactly the ones expected to fail at the
parent and pass at the fix, and the parent is the buggy state a reviewer would be
shown. Commits touching more than `--max-files` are skipped, because a review
case is one seeded defect rather than a refactor, and the smallest diffs are
ranked first since they certify most cleanly.

Mining **proposes**; it never decides. Only `arena certify-pack` decides, by
running the tests at both commits (baseline fails, reference passes, mutants
killed). And it never infers semantics: `--json` emits a scaffolded import spec
with every Git-derivable field filled in (commits, source paths, test paths,
ground-truth file list) and every semantic field left as an explicit `TODO`, so a
human writes prose instead of transcribing paths. A guessed category or ground
truth would silently corrupt the benchmark, which is exactly what the importer's
contract forbids.

# `arena import-fix`

`arena import-fix` turns a known buggy/fixed commit pair in a **local** Git
repository into a candidate Code Review Arena pack. It is the first
post-architecture product feature and is deliberately conservative:
local-only, offline, provider-neutral, deterministic, non-AI, non-publishing, and
non-executing during import.

```bash
arena import-fix \
  --repo /path/to/local/repository \
  --buggy-commit <full-object-id> \
  --fixed-commit <full-object-id> \
  --spec import-spec.yaml \
  --output /path/to/candidate-pack \
  --source-label owner/repository      # optional, persisted; never a local path
```

## Why the generated case is synthetic

The importer builds a **reverse-fix review case**. Given a buggy commit `B` and a
fixed commit `F` (with `B` an ancestor of `F`) it generates:

```
after/           selected source content at B   (the buggy state under review)
before/          selected source content at F   (the fixed state)
reference.patch  exact repair diff  B -> F       (after/ + reference.patch == F)
pr.diff          inverse review diff F -> B       (before/ + pr.diff == after/)
tests/           selected test content at F
```

This matches the Arena convention `before -> pr.diff -> after (buggy)` and
`after/ + reference.patch -> fixed solution`. It is a **synthetic** review case
derived from a real historical fix; it is **not** the original bug-introducing
pull request, and its `pr.diff` is the reverse of the real change. Treat the
synthetic review as a plausible "what if this fix were proposed in reverse"
exercise, not as the literal history.

## Required human specification

The importer never infers semantics. A strict `import-spec.yaml` supplies
everything that cannot be derived safely (title, category, severity, description,
ground truth, execution/validation config, and which committed paths are source
vs tests). It reuses the normal case models, so a spec that would not make a valid
`BenchmarkCase` is rejected. See the command help and `arena/importer/import_spec.py`
for the exact shape; unknown fields fail.

## Local, offline threat model

The source repository's mutable working tree is **ignored completely**: the
importer reads committed objects only (`git ls-tree`, `git cat-file`, and a
tree-object `diff-tree --raw` comparison), reusing the Phase 1D isolated Git
environment (private empty HOME/config, no system/global config, no hooks, no
pager/editor/credentials, no network, fixed locale, bounded incremental output,
timeout and process-group cleanup). Every source command additionally sets
`GIT_NO_REPLACE_OBJECTS=1` (replacement refs are ignored, not followed),
`GIT_NO_LAZY_FETCH=1` (a missing object fails offline instead of fetching) and
`GIT_LITERAL_PATHSPECS=1`. It never runs `checkout`, `clone`, `fetch`, build
scripts, or repository code.

Commit ids must be full object ids (40 hex for SHA-1, 64 for SHA-256); abbreviated
ids, branch names, tags, and non-commit objects are rejected, as are unrelated
histories and a fixed commit that is not descended from the buggy commit. Truncated
or rewritten ancestry cannot support deterministic range admission, so a **shallow
repository** (`shallow_repository`) and a nonempty graft file
(`repository_history_override`) are both rejected.

## Patches are generated in a fresh repository

`pr.diff` and `reference.patch` are **not** generated inside the source repository.
The importer reads the exact selected blob bytes at B and F, stages them into a
brand-new private repository (empty local/global/system config, empty
`info/attributes`, no worktree, no hooks/filters/external-diff/textconv/replacement
refs), proves each staged blob equals the exact source bytes, and diffs the two
trees there with explicit deterministic flags (`--text --no-color --no-ext-diff
--no-textconv --no-renames --default-prefix --full-index --unified=3
--diff-algorithm=myers --no-indent-heuristic`). As a result the source repository's
`.gitattributes`, `.git/info/attributes`, local diff configuration (for example
`diff.noprefix`, custom prefixes, an alternate algorithm), replacement refs, current
branch and HEAD, and worktree-vs-bare form **cannot** change the generated patches.
Both patches are still proven to reproduce their target trees through the Phase 1D
Git-authoritative pipeline.

## Changed-path classification and selection

Changed paths come from a **tree-object comparison** (`diff-tree --raw`) and exact
selected-tree maps, never a textual source diff. Every path changed between `B` and
`F` must be classified as selected **source** (copied into `before/` and `after/`,
and diffed) or selected **tests** (copied from `F` into `tests/`, excluded from the
diffs because benchmark tests are protected evidence). A changed path outside both
selections, or a source/tests overlap, fails loudly -- nothing is silently omitted.
Binary or non-UTF-8 source changes are decided from the exact changed bytes and
rejected; symlinks, gitlinks/submodules, special modes, unsafe or colliding paths,
and oversized inputs are rejected.

Each source selector is admitted **individually** (no silent deduplication):
duplicate selectors, ancestor/descendant overlaps, and a selector matching no file
in either `B` or `F` are rejected, while a selector that exists in only one commit
remains a valid addition or deletion. `tests_root`, when supplied, must be a
nonempty directory disjoint from every source selector; if tests are required
(`run_tests` or `tests_required`) a missing, empty, or file-valued tests root fails.

## Deterministic provenance and publication

The same committed objects and spec produce **byte-identical** output and the same
`pack.sha256` on every run, regardless of the source working tree, untracked or
dirty `.gitattributes`, `.git/info/attributes`, local diff configuration,
replacement refs, current branch/HEAD, or worktree-vs-bare form. Each case carries a
strict, bounded `provenance.json` (schema version `2`) recording the mode
(`reverse_fix`), a validated `owner/repository`-style source label, Git object
format, diff-policy version, the full buggy/fixed/merge-base ids, the selected
source paths and tests root, the expanded buggy/fixed source files and fixed test
files, and the exact changed source and test paths, plus the SHA-256 of both
generated diffs. It deliberately omits generation time, username, hostname, and any
absolute repository/output/temporary path or local Git config. Every generated file
is written through a shared exclusive complete-write helper that verifies byte
count, content and mode. Publication is **no-overwrite**: the output directory is
claimed atomically (`mkdir`) and the validated contents are moved in, rejecting an
existing file, empty directory or symlink (re-checked immediately before
publishing). A failed import removes its staging directory and never publishes a
partial pack. The report distinguishes buggy, fixed and union source file counts.

## Import does not certify

Import runs strict validation and a contamination scan, and proves both generated
diffs reproduce their target trees through the Phase 1D Git-authoritative pipeline,
but it does **not** run tests, structural validators, mutation testing, or
certification, and it does not add the case to the shipped `benchmark_sets`. The
import report ends with `certification: not run`. After reviewing the candidate
manually, certify it as a separate, explicit step (Docker by default):

```bash
arena validate <candidate-pack>
arena lint-cases <candidate-pack> --strict
arena certify-pack <candidate-pack>
```

## Contamination scans both sides of the review diff

The contamination scan treats the whole `pr.diff` as review surface, not just its
added lines. In an inverse RealFix diff (`F -> B`) the removed lines are the
historical fixed implementation, so a removed line, including a comment or
docstring carried over from the fix, can hand the reviewer the defect, its cause,
or its repair just as an added line can. The scan reports added-line and
removed-line hits under distinct surfaces (`diff_added_line`, `diff_removed_line`)
alongside after-tree comments and test names.

`arena lint-cases --strict` fails on the answer surfaces a reviewer could echo
without reasoning (added lines, comments, test names) and reports removed-line
matches for audit rather than failing on them: on a forward-review diff a removed
line is the prior correct code the reviewer is meant to see. On an inverse RealFix
diff a removed line can instead carry the fix, so the import gate rejects a
removed-line leak at build time and the manual case audit reviews removed-line
matches before a case is accepted.

## Limitations of this first version

- Local repositories only; no hosted GitHub/GitLab ingestion or network access.
- Shallow clones and grafted/replaced histories are rejected (they cannot support
  deterministic range admission); the source must have complete local ancestry.
- Binary source changes are rejected; only UTF-8 text diffs are supported.
- Each invocation imports exactly one case from one commit pair.
- A ground-truth bug must live in the buggy tree, so a fix that only **adds** new
  source files cannot express those additions as reviewable bugs and is reported as
  an uncovered change. Modifications and deletions are fully supported.
- A fix that changes a Phase 1D protected file (for example `pyproject.toml` or a
  `conftest.py`) cannot be reproduced as a reviewer-editable repair and is rejected.
- An imported case is a **candidate only**. It is not production-ready, certified,
  or research-valid until it passes the certification ladder and human review.
