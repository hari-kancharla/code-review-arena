# Security model and verified runs

The harness assumes reviewers and benchmark packs may be adversarial. This page
summarizes the guarantees that follow from that assumption and what a run needs before
it counts on the default leaderboard. The full threat model and the invariants behind
each gate are in [trusted-evaluation-architecture.md](trusted-evaluation-architecture.md).

## What the reviewer sees

The reviewer payload is blind: case id, stack, the diff, and a bounded set of relevant
files. It carries no title, description, category, severity, or any ground truth, and
no pre-patch test or static-analysis output (whose failing assertions would disclose
the expected values). `--reveal-metadata` and `--reveal-test-output` exist only for
debugging or an openly test-assisted run, which is recorded as such in the run
metadata. See [custom-command-reviewer.md](custom-command-reviewer.md).

## Integrity guarantees

- Patches cannot touch the case's tests, any `conftest.py`/pytest config, or per-case
  `protected_paths`; absolute or `..` diff paths are rejected before `git apply` runs.
- Fixture test commands run with an allowlisted environment (no inherited shell
  secrets; `ARENA_PASSTHROUGH_ENV` forwards named variables explicitly) and POSIX
  resource limits (CPU, file size, open files, processes).
- `arena pack-hash --write` pins a pack's content checksum; runs record it and warn on
  mismatch. `arena lint-cases` flags ground-truth vocabulary leaking into the diff,
  comments, or test names.
- Every run writes `run_manifest.json` (harness version, git SHA, pack checksum,
  sanitized reviewer config, budgets, timings) so published numbers are auditable.
- The API server executes runs as bounded background jobs; local execution over HTTP
  requires a server-side opt-in, and `ARENA_API_TOKEN` adds token auth. It is not
  hardened for public exposure.

## Signal strength

Signals are labeled by their strength. Test execution and patch application are
execution-backed evidence. Structural validators and concept matching are deterministic
heuristics (comment-stripped lexical and AST checks, not semantic understanding) and are
documented as such in [structural-validators.md](structural-validators.md) and
[scoring.md](scoring.md).

## Local execution

`--allow-local-execution` opts into the fixture-owned test commands that run in copied
workspaces. Use it only with fixtures you trust. Runs that execute this way are marked
trusted-local and are excluded from the leaderboard unless you pass
`--include-unverified`.

## Docker backend (the verified path)

Docker is the standard, isolation-backed way to run case tests. Build the sandbox image
once (it holds only Python and the packs' pinned test dependencies, no arena source):

```bash
bash scripts/build_bench_image.sh        # builds the arena-bench:1 image
```

Point a pack at it with `default_docker_image: arena-bench:1` in its `manifest.yaml`, or
set `docker_image` on a case. The executor never pulls a missing image (the name comes
from the pack), so the image must be built first; otherwise execution-backed runs
cleanly skip and report `invalid`.

## Leaderboard eligibility

Docker is necessary for default leaderboard eligibility but not sufficient. A run also
needs:

- full coverage of the pack,
- exact reviewer output,
- a reviewer that could not reach the answer key (in practice, `docker-command`; see
  [docker-command-reviewer.md](docker-command-reviewer.md)),
- a pack digest supplied out of band with `--expected-pack-sha256`.

Anything short of all of those is inspectable only with `--include-unverified`.
