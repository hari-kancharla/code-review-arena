# Code Review Arena

Execution-backed benchmark for AI code-review agents.

[![CI](https://github.com/hari-kancharla/code-review-arena/actions/workflows/ci.yml/badge.svg)](https://github.com/hari-kancharla/code-review-arena/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![DOI](https://zenodo.org/badge/1251661586.svg)](https://doi.org/10.5281/zenodo.24425641)

Code Review Arena measures whether an AI review agent can find a seeded bug in a pull
request and actually fix it. It applies each suggested patch in an isolated workspace,
runs the required tests and validators, and scores detection separately from
validation, so a sharp-looking comment is never mistaken for a working repair.
Everything runs locally and the harness is model-agnostic.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -e ".[dev]"

arena run benchmark_sets/audit_v1 --reviewer reference-patch --mode full --allow-local-execution
arena leaderboard runs/ --metric validated_case_rate --include-unverified
```

This scores the known-good reference patches on the `audit_v1` pack (10 of 10
validated) and prints the leaderboard. `--allow-local-execution` runs the pack's own
test commands in copied workspaces, so use it only with packs you trust. For a full
walk-through, including the dashboard, see [docs/DEMO.md](docs/DEMO.md).

## Prerequisites

- Python 3.11 or newer
- Git (patches are applied with `git apply`)
- Docker, optional, for isolated test execution and leaderboard-eligible runs
- Node.js 20 or newer, optional, for the dashboard

No API keys are needed to run the built-in reviewers.

## How it works

Each case is a seeded pull request with one or more known bugs, the files a fix should
touch, and the checks a fix must pass. The reviewer sees only the diff and a bounded
set of relevant files, with no title, description, or ground truth. It returns findings
and an optional patch, and the harness takes it from there:

```
diff + files  ->  reviewer  ->  patch  ->  apply in workspace  ->  tests  ->  validators  ->  score
```

Every full run reports two headline numbers:

| Metric | What it measures |
|---|---|
| `detection_f_beta` | The reviewer found the seeded bugs (file granularity; line precision is reported separately as `localization_rate`) |
| `validated_case_rate` | Its patch applied, passed the required tests, and satisfied the validators (the primary full-mode metric) |

The gap between them is the point. On `audit_v1` the `control:keyword_gamer` control
detects all ten bugs (`detection_f_beta=1.000`) yet validates none of its patches
(`validated_case_rate=0.000`), while `reference-patch` validates all ten.

The report also breaks repair down into review-accuracy, repair-success, and
trustworthiness dimensions. See [docs/metrics.md](docs/metrics.md) for every metric
and [docs/security-model.md](docs/security-model.md) for what the reviewer can and
cannot see.

## Benchmark your own model

1. **Check the contract.** `arena schema` prints the JSON a reviewer must return.

2. **Connect your model.** Point the harness at a local model server (Ollama, vLLM,
   LM Studio, llama.cpp) with no wrapper:

   ```bash
   arena run benchmark_sets/audit_v1 --reviewer openai:http://localhost:11434/v1 \
     --model llama3 --mode full --allow-local-execution
   ```

   Or wrap any model in a command that reads the case JSON and prints review JSON.
   [`scripts/fake_reviewer.py`](scripts/fake_reviewer.py) is a working example:

   ```bash
   arena verify-reviewer benchmark_sets/audit_v1 \
     --command "python scripts/fake_reviewer.py --case {case_json}"   # one-case dry run

   arena run benchmark_sets/audit_v1 --reviewer custom-command \
     --command "python scripts/fake_reviewer.py --case {case_json}" \
     --mode full --allow-local-execution
   ```

3. **Make it count.** Only runs with `docker-command`, which runs the same wrapper in a
   container with no network and a read-only payload, are blind enough for the default
   leaderboard. See [docs/docker-command-reviewer.md](docs/docker-command-reviewer.md)
   and [docs/security-model.md](docs/security-model.md#leaderboard-eligibility).

Run `arena preflight <pack> --reviewer <spec>` first to check the pack, the reviewer
config, and local prerequisites without executing anything. The reviewer interface,
built-in controls, and run limits are documented in
[docs/reviewer-interface.md](docs/reviewer-interface.md).

## Benchmark packs

| Pack | Cases | Purpose | Validation |
|---|---:|---|---|
| `benchmark_sets/v1` | 9 | Authored baseline cases | review scoring + validation |
| `benchmark_sets/audit_v1` | 10 | Authored patch-required audit cases | patch apply + tests + validators |
| `benchmark_sets/audit_v2` | 10 | Authored logic-defect cases | patch apply + tests |
| `benchmark_sets/integrity_pilot_v0` | 8 pairs | Validation-integrity review (separate track) | visible CI + hidden trusted oracle |

Use `audit_v1` or `audit_v2` for scoring. `v1` is an older calibration pack: three of
its nine cases (TypeScript, SQL) have no runnable test suite, so they are excluded from
`validated_case_rate`, and every run publishes the denominator it used. Case lists are
in [docs/audit-pack-v1.md](docs/audit-pack-v1.md) and
[docs/audit-pack-v2.md](docs/audit-pack-v2.md).

The historical-fix cases (RealFix) are not shipped here. Each one bundles a full copy of
a project's source and tests, so they live in
[realfix-benchmark](https://github.com/hari-kancharla/realfix-benchmark), which pins
this harness and runs through the same commands. This repository keeps the tooling
that builds them: `arena mine-fixes` and `arena import-fix`
([docs/historical-fix-ingestion.md](docs/historical-fix-ingestion.md)).

Because those cases come from public repositories, their fixes may be in a model's
training data. Each case records when its answer became public, and a run can declare
the model's knowledge cutoff to split results into pre- and post-cutoff cohorts. See
[docs/training-data-exposure.md](docs/training-data-exposure.md) for the method and
what it does not claim.

## Integrity track

CRA-Integrity asks a different question: can a reviewer tell that the green checks on a
pull request no longer mean anything? Each pair holds two green pull requests for the
same task, one genuine and one compromised (a weakened assertion, a stubbed function, a
regenerated golden file). Only the genuine one satisfies a hidden, independently
executed contract.

```bash
arena integrity-audit benchmark_sets/integrity_pilot_v0
arena integrity-certify benchmark_sets/integrity_pilot_v0 \
    --allow-local-execution --determinism-runs 2 --strict certified
arena integrity-run benchmark_sets/integrity_pilot_v0 \
    --reviewer integrity:reference --allow-local-execution
```

The headline metric is pairwise discrimination: approve the genuine variant and block
the compromised one, so a reviewer that blocks everything scores zero. Pairs with
incomplete runtime evidence are reported as `indeterminate` instead of being scored.
Certification tops out at `certified` rather than `verified`, because oracle
confidentiality cannot be proven from inside the pack. See
[docs/integrity-track.md](docs/integrity-track.md).

## Reports and dashboard

```bash
arena audit-report runs/ --output docs/reports/audit-v1-results.md

arena serve                                  # local API on port 8000
cd dashboard && npm install && npm run dev   # dashboard at http://localhost:3000
```

The dashboard shows the leaderboard, per-pack reports, case traces, and the
methodology. See [docs/audit-report.md](docs/audit-report.md).

## Project structure

```
arena/              # Python package and the `arena` CLI
  benchmark/        #   pack loading, validation, run orchestration
  reviewers/        #   built-in controls and model / command adapters
  patching/         #   isolated workspaces and patch application
  execution/        #   sandboxed test execution (local or Docker)
  scoring/          #   detection, localization, and validation scoring
  validators/       #   structural repair-shape checks
  integrity/        #   the CRA-Integrity track
  importer/         #   historical-fix mining and import
  reports/          #   JSON, Markdown, HTML reports and leaderboard
  server/           #   FastAPI service used by the dashboard
benchmark_sets/     # the shipped packs
dashboard/          # Next.js dashboard
docs/               # methodology, reference, and guides
scripts/            # sandbox image build and example reviewer
tests/
```

Module responsibilities are in [docs/architecture.md](docs/architecture.md).

## Documentation

- [docs/DEMO.md](docs/DEMO.md): fresh clone to a rendered leaderboard
- [docs/metrics.md](docs/metrics.md) and [docs/scoring.md](docs/scoring.md): what is measured and how
- [docs/reviewer-interface.md](docs/reviewer-interface.md): the reviewer contract and built-in reviewers
- [docs/security-model.md](docs/security-model.md): isolation, integrity checks, and leaderboard eligibility
- [docs/adding-new-cases.md](docs/adding-new-cases.md): authoring a benchmark case
- [docs/README.md](docs/README.md): the full index

## Limitations

- The packs are curated and small (29 cases across `v1`, `audit_v1` and `audit_v2`,
  plus eight integrity pairs). The historical-fix cases live in a separate dataset
  repository and are counted there.
- The integrity pilot's compromised pull requests are authored. They establish the
  evaluation abstraction; they say nothing about how often coding agents produce
  this failure in practice, and eight pairs cannot rank reviewers.
- RealFix cases are synthetic reverse-review presentations of historical fixes, not
  necessarily original bug-introducing pull requests. They demonstrate methodology and
  support no model-performance conclusions.
- Concept matching is lexical (curated keywords), not semantic; well-paraphrased
  findings can be under-credited. Execution metrics do not have this problem.
- Structural validators are comment-stripped heuristics: hand-authored, may reject
  alternate valid repairs, and string literals are not stripped. Tests are the gate.
- Passing tests is execution evidence, not proof of complete correctness.
- This is a local audit harness, not a large-scale public ranking.

## Citing

If you use Code Review Arena in research, please cite it. GitHub's "Cite this
repository" button reads [CITATION.cff](CITATION.cff), and each tagged release is
archived on Zenodo with its own DOI. To cite the project as a whole, use the
concept DOI [10.5281/zenodo.24425641](https://doi.org/10.5281/zenodo.24425641), which
always resolves to the latest release; v0.3.0 itself is
[10.5281/zenodo.24425642](https://doi.org/10.5281/zenodo.24425642).

## Contributing

[CONTRIBUTING.md](CONTRIBUTING.md) covers local setup, the `make check` gate (lint,
type check, tests, pack validation, contamination scan), and how to author a case
correctly. To report a vulnerability, see [SECURITY.md](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE).
