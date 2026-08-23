# Code Review Arena

Execution-backed benchmark for AI code-review agents.

[![CI](https://github.com/harihkk/code-review-arena/actions/workflows/ci.yml/badge.svg)](https://github.com/harihkk/code-review-arena/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

## About

Code Review Arena measures whether an AI review agent can find a seeded bug in a pull
request and actually fix it. It applies each suggested patch in an isolated workspace,
runs the required tests and validators, and scores detection separately from validation,
so a sharp-looking comment is never mistaken for a working repair. Everything runs
locally and the harness is model-agnostic.

## How it works

Each case is a seeded pull request with one or more known bugs, the files a fix should
touch, and the checks a fix must pass. The reviewer payload is blind: case id, stack,
the diff, and a bounded set of relevant files, with no title, description, category,
severity, or any ground truth, and no pre-patch test or static-analysis output (whose
failing assertions would disclose the expected values). `--reveal-metadata` and
`--reveal-test-output` exist only for debugging or an openly test-assisted run, which is
recorded as such in the run metadata. The reviewer returns its findings and an optional
patch, and the harness takes it from there:

```
diff + files  ->  reviewer  ->  patch  ->  apply in workspace  ->  tests  ->  validators  ->  score
```

Every full run produces two headline numbers (the report also breaks repair down
into review-accuracy, repair-success, and trustworthiness dimensions):

| Metric | What it measures |
|---|---|
| `detection_f_beta` | The reviewer found the seeded bugs (file granularity; line precision is reported separately as `localization_rate`) |
| `validated_case_rate` | Its patch applied, passed the required tests, and satisfied the validators (the primary full-mode metric) |

The gap between them is the whole point. On `audit_v1` the `control:keyword_gamer`
control detects all ten bugs (`detection_f_beta=1.000`) yet validates none of its
patches (`validated_case_rate=0.000`), while `reference-patch` validates all ten.
`arena audit-report` and the dashboard show that gap per reviewer.

Signals are labeled by their strength: test execution and patch application are
execution-backed evidence; structural validators and concept matching are deterministic
heuristics (comment-stripped lexical and AST checks, not semantic understanding) and are
documented as such.

## Integrity and security model

The harness assumes reviewers and benchmark packs may be adversarial:

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

## Quickstart

Python 3.11 or newer is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"

arena validate benchmark_sets/audit_v1
arena run benchmark_sets/audit_v1 --reviewer reference-patch --mode full --allow-local-execution
arena leaderboard runs/ --metric validated_case_rate --beta 1.0 --include-unverified
```

Five packs ship today. `benchmark_sets/v1`, `benchmark_sets/audit_v1`, and
`benchmark_sets/audit_v2` are authored calibration and audit packs (ten cases each);
`benchmark_sets/realfix_seed_v0` is a three-case methodology seed derived from
historical fixes that executes only through its pinned Docker image (see the
Reference section). For the commands above, swap in `audit_v1` or `audit_v2`; both
are patch-backed and runnable with `--allow-local-execution`.
`benchmark_sets/integrity_pilot_v0` belongs to a separate track with its own
commands (see below).

`--allow-local-execution` opts into the fixture-owned test commands that run in copied
workspaces. Use it only with fixtures you trust. Runs that execute this way are marked
trusted-local and are excluded from the leaderboard unless you pass `--include-unverified`.

### CRA-Integrity: reviewing a green pull request you cannot trust

A separate track. Instead of asking whether a reviewer can find a planted defect,
it asks whether a reviewer can tell that the green checks on a pull request no
longer mean anything -- because an assertion was weakened, a test left collection,
a golden file was regenerated from the code it was meant to check, the function
under test was stubbed out, or the implementation answers the ticket's examples
rather than its rule.

Each pair ships two pull requests against the same task, the same baseline and the
same visible command: one genuine, one compromised. Both are green. Only one
satisfies a hidden, independently executed contract. The reviewer is not told which
it received.

```bash
python -m arena.cli.main integrity-audit benchmark_sets/integrity_pilot_v0
python -m arena.cli.main integrity-certify benchmark_sets/integrity_pilot_v0 \
    --allow-local-execution --determinism-runs 2 --strict verified
python -m arena.cli.main integrity-run benchmark_sets/integrity_pilot_v0 \
    --reviewer integrity:reference --allow-local-execution
```

The headline metric is pairwise discrimination -- approve the genuine variant *and*
block the compromised one. A reviewer that blocks both scores perfect attack recall
and zero discrimination, which is exactly why recall is never reported alone. See
[docs/integrity-track.md](docs/integrity-track.md).

### Docker backend (the verified path)

Docker is the standard, isolation-backed way to run case tests. Build the sandbox image
once (it holds only Python and the packs' pinned test dependencies, no arena source):

```bash
bash scripts/build_bench_image.sh        # builds the arena-bench:1 image
```

Point a pack at it with `default_docker_image: arena-bench:1` in its `manifest.yaml`, or
set `docker_image` on a case. The executor never pulls a missing image (the name comes
from the pack), so the image must be built first; otherwise execution-backed runs cleanly
skip and report `invalid`. Docker-backed runs are leaderboard-eligible without
`--include-unverified`.

## Benchmark your own model

The built-in reviewers are the deterministic controls and `reference-patch`. To score a
real model, wrap it in any local command that reads the case JSON and prints review JSON,
then point `custom-command` at it. The payload is blind (no ground truth and no
descriptive metadata), so a reviewer cannot pass on metadata alone.

```bash
arena schema                       # the JSON contract your wrapper must emit
arena verify-reviewer benchmark_sets/audit_v1 \
  --command "python scripts/fake_reviewer.py --case {case_json}"   # one-case dry run

arena run benchmark_sets/audit_v1 \
  --reviewer custom-command \
  --command "python scripts/fake_reviewer.py --case {case_json}" \
  --mode full \
  --allow-local-execution
```

`scripts/fake_reviewer.py` is a working example to copy. The placeholders `{case_json}`,
`{diff_file}`, `{case_id}`, and `{workspace}` are expanded per case. `--max-wall-seconds`
and `--max-cost` cap a run; `--enable-repair` opts into a deterministic salvage of
almost-valid JSON (logged on the response).

## Reports and dashboard

```bash
arena audit-report runs/ --output docs/reports/audit-v1-results.md   # build the report from saved runs

arena serve            # local API
cd dashboard
npm install
npm run dev            # dashboard at http://localhost:3000
```

Main dashboard routes are `/leaderboard`, `/reports/audit-v1`, `/reports/audit-v2`, `/cases`,
`/methodology`, and `/docs`. For a full walk-through from a fresh clone, see
[docs/DEMO.md](docs/DEMO.md).

## Reference

Benchmark packs:

| Pack | Cases | Purpose | Validation |
|---|---:|---|---|
| `benchmark_sets/v1` | 10 | Authored baseline cases | review scoring + validation |
| `benchmark_sets/audit_v1` | 10 | Authored patch-required audit cases | patch apply + tests + validators |
| `benchmark_sets/audit_v2` | 10 | Authored logic-defect cases | patch apply + tests + validators |
| `benchmark_sets/realfix_seed_v0` | 3 | Historical-fix methodology seed | Docker-backed patch apply + tests |
| `benchmark_sets/integrity_pilot_v0` | 8 pairs | Validation-integrity review (separate track) | visible CI + hidden trusted oracle |

The first three packs are authored calibration and audit packs. The RealFix seed
cases are synthetic reverse-review presentations derived from real historical fixes
in attrs, click, and rich (upstream licenses and notices ship inside the pack): the
review diff is the inverse of the historical fix, not necessarily an original
bug-introducing pull request. Three cases demonstrate the ingestion-to-certification
methodology end to end; they support no conclusions about model performance. Larger
historical datasets are built outside the core harness with `arena import-fix`
([docs/historical-fix-ingestion.md](docs/historical-fix-ingestion.md)). The seed
executes only in its pinned image; build it first with
`bash docker/realfix_seed/build.sh`.

Metrics:

| Metric | Meaning |
|---|---|
| `validated_case_rate` | Primary full-mode score: validated cases over eligible cases |
| `detection_f_beta` | Found and localized seeded bugs |
| `patch_apply_rate` | Required patches that applied cleanly |
| `test_pass_rate` | Required regression-test runs that passed |
| `structural_pass_rate` | Required structural validator checks that passed |
| `false_positives_per_case` | Unsupported findings per evaluated case |

Control reviewers (deterministic harness checks, not external model results):

| Reviewer | Role |
|---|---|
| `reference-patch` | Loads committed known-good `reference.patch` artifacts |
| `control:perfect_patch` | Harness success control |
| `control:keyword_gamer` | Detection-only adversarial control |
| `control:bad_patch` | Detects bugs but supplies failing fixes |
| `control:detects_no_patch` | Detects bugs without a patch |
| `control:malformed_patch` | Supplies invalid patch output |
| `custom-command` | Runs your local reviewer command over structured input and output |

`mock:<mode>` remains a deprecated alias for `control:<mode>` for one release.

Audit Pack v1 cases:

| Category | Seeded bug |
|---|---|
| Security | FastAPI tenant admin bypass |
| Security | SQL ownership leak |
| Security | JWT audience and issuer validation |
| Distributed systems | Kafka duplicate event |
| Distributed systems | Out-of-order event |
| RAG safety | Fabricated citation |
| RAG safety | Prompt injection policy override |
| Concurrency | Async race |
| Reliability | Idempotency tenant scope |
| API correctness | Pagination cursor bug |

## Development

```bash
make check                      # full gate: lint, typecheck, tests, pack validation, contamination
cd dashboard && npm run build   # dashboard build gate
```

See [docs/](docs/README.md) for architecture, metrics, the reviewer interface, case
authoring, and the audit report.

## Limitations

- The packs are curated and small (33 cases across `v1`, `audit_v1`, `audit_v2`,
  and the three-case `realfix_seed_v0` seed, plus eight integrity pairs).
- The integrity pilot's compromised pull requests are authored. They establish the
  evaluation abstraction; they say nothing about how often coding agents produce
  this failure in practice, and eight pairs cannot rank reviewers.
- RealFix seed cases are synthetic reverse-review presentations of historical fixes,
  not necessarily original bug-introducing pull requests; the three-case seed
  demonstrates methodology and supports no model-performance conclusions.
- Concept matching is lexical (curated keywords), not semantic; well-paraphrased
  findings can be under-credited. Execution metrics do not have this problem.
- Structural validators are comment-stripped heuristics: hand-authored, may reject
  alternate valid repairs, and string literals are not stripped. Tests are the gate.
- Passing tests is execution evidence, not proof of complete correctness.
- This is a local audit harness, not a large-scale public ranking.

## Contributing and security

[CONTRIBUTING.md](CONTRIBUTING.md) covers the local setup, the `make check` gate,
and how to author a benchmark case correctly. To report a vulnerability, see
[SECURITY.md](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE).
