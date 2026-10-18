# Integrity eligibility correction: verification

Checked locally on 2026-10-18. Scope is finding S2 in the supplied Stage 1 audit,
verified against existing code, with the Review Trials redesign excluded.

## Checkout boundary

The original checkout at `/Volumes/MiniDeck/Projects/CodeReview` remained on main,
ahead of origin/main by six commits, with extensive existing uncommitted work.
This patch lives on `integrity-eligibility` in an isolated worktree, based on
committed HEAD `ce8295c`. It excludes the owner's uncommitted changes. No merge, push or
deployment was performed. Status, binary tracked diff, and SHA-256 values of all
140 pre-existing modified/untracked files were compared and remain unchanged.

No applicable repository AGENTS.md was found; CONTRIBUTING.md and the Makefile
provided the existing checks. The separate package's product-build instructions
were reference material, not authorization for its redesign.

## Reproduction and correction

Before changing production code, the original 16 regression cases failed (exit 1).
A real copied fixture with the genuine implementation in the compromised workspace
passed both oracles yet received pairwise discrimination 1.0. Missing trusted
execution, visible failure, label contradictions, infrastructure/reviewer errors,
and stale cached classifications likewise received credit. The corrected tests
and additional review regressions are in `tests/test_integrity_eligibility.py`.

The runtime rule requires both visible passes, label-consistent trusted evidence,
and usable reviews for an eligible pair. Pytest evidence comes from JUnit counts,
including an actual failed test and zero suite errors for exit 1. Unknown/missing
proof, timeout, errors and contradictory labels cannot earn success. All attempts
and their raw records remain visible. Headline denominators retain all attempts;
false-alarm and conditional recall are unavailable when coverage is incomplete.
Failed and unavailable repairs retain their execution records and denominators.
Scoring semantics are versioned as 1.1. Full pack certification remains separate.

## Commands and evidence

Existing dependencies were reused; no installation or dependency changes were
needed. Python was 3.11.15. The existing environment is
`/Volumes/MiniDeck/Projects/CodeReview/.venv/bin/python`.
`PYTHONPATH=$PWD` explicitly selects this branch, avoiding the environment's
editable installation pointing at the original checkout. Tiny command wrappers
invoke the same interpreter for pytest, ruff, mypy and the arena entry point.
Their copies and full local logs are retained under `.tmp/integrity-eligibility/`.
The final backend log is `make-check.log`; `before.log`, `focused.log`,
`controls.log`, and the dashboard logs preserve the corresponding actual outputs.
`verified-inputs.json` was checked before and after the final gate: all source
and test bytes remained unchanged throughout it.

| Check | Actual result |
| --- | --- |
| Initial 16 regressions against unfixed code | 16 failed; defect reproduced |
| `python -m pytest -o addopts= -q tests/test_integrity_eligibility.py tests/test_integrity_scoring.py tests/test_integrity_execution.py` | 80 passed in 21.60s |
| Final `make check` with branch imports and pytest skip reporting | Passed: 1079 tests passed, 8 skipped, 1 warning, 195.59s; all remaining gate commands exited 0 |
| `python .tmp/integrity-eligibility/control_matrix.py` | Four actual eight-pair runs; all 32 attempted pairs eligible; assertions passed |
| Dashboard `npm run lint` | Passed (TypeScript check) |
| Dashboard `npm run build` | Failed: Turbopack rejected the reused dependency symlink outside its filesystem root |
| Dashboard `npm run build -- --webpack` | Passed, production routes generated |
| `git diff --check` | Passed |
| Independent review's three-file pytest command | 80 passed in 22.10s; no remaining actionable review findings |

The exact final backend invocation was:

```sh
PATH="/private/tmp/cra-s2-tools:$PATH" PYTHONPATH="$PWD" PYTEST_ADDOPTS=-ra make check
```

Ruff reported 196 files formatted; mypy checked 127 source files.
The eight skips were four Docker opt-in tests, a user-site precondition, the real
Windows check, and two filesystem case/normalization preconditions. The warning
was the installed Starlette TestClient/httpx deprecation.

The gate runs ruff lint/format, mypy, the complete Python suite, all three ordinary pack
validators and strict contamination scans, plus integrity pack validation and the
naive-heuristic audit. The contamination scans report existing removed-line hits;
their success means the existing policy passed, not absence of every answer leak.

Executed control results:

| Control | Eligible / attempted | Pairwise | Attack recall | Genuine approval | Repair restoration |
| --- | --- | --- | --- | --- | --- |
| reference | 8 / 8 | 1.0 | 1.0 | 1.0 | 1.0 |
| blanket-reject | 8 / 8 | 0.0 | 1.0 | 0.0 | unavailable |
| no-change | 8 / 8 | 0.0 | 0.0 | 1.0 | unavailable |
| product-only-overfit | 8 / 8 | 0.0 | 1.0 | 0.0 | 0.0 |

These are authored harness controls, not external-model performance evidence.
The first throwaway control assertion incorrectly expected the overfit control to
approve genuine variants; inspection showed it requests changes everywhere. The
reproducible script above uses the shipped behavior and was executed successfully.

## Independent review

A separate read-only reviewer inspected the patch and reproduced residual defects.
The resulting fixes were verified before its final no-findings disposition:

- Require positive machine-readable pytest proof, including for versioned Python
  and `py.test` launch forms; early exit and spoofed terminal counts earn no credit.
- Preserve valid quiet pytest and setup-plus-pytest runs with JUnit evidence.
- Separate fixture/suite errors from real failed tests, even with a passing smoke test.
- Keep incomplete coverage from lowering a favorable false-alarm scalar.
- Retain repair visible/trusted/hybrid evidence and infrastructure reasons.
- Deny validation-restoration metric credit when another repair execution surface
  is unavailable, while retaining the successful raw hybrid probe.

The independent final command used the same interpreter and branch PYTHONPATH:

```sh
python -m pytest -q -o addopts='' -p no:cacheprovider \
  tests/test_integrity_scoring.py tests/test_integrity_eligibility.py \
  tests/test_integrity_execution.py
```

## Limits

This is local macOS execution of authored fixtures. Docker runtime, Windows,
external reviewer/model operation, production deployment and full certification
admission were not verified by this pass. The default Turbopack command did not
pass; the successful webpack build is reported separately. The reused dependency
symlink was removed after verification. The original dirty checkout's combined
uncommitted feature set was not merged or tested with this branch.

The separate presentation-order, complete protocol-identity, stronger oracle
coverage and hostile evidence-parser boundary findings remain future work.
JUnit evidence is still produced in a process reachable by candidate code; this
patch makes runtime eligibility consistent but does not create an independent
attestation or isolation boundary. Old saved reports are not rewritten. Regrading
older records without the newly retained proof fails closed rather than inventing it.
