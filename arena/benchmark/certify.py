"""Pack certification: is a benchmark case a real test of repair?

Each executable case is graded on a four-rung ladder:

- ``draft``: no executable tests. Useful for detection scoring only; it cannot
  be backed by execution, so it can never rise above this rung.
- ``development``: has tests but fails a gate below.
- ``certified``: the buggy baseline (after/) FAILS the tests, the reference
  solution (after/ + reference.patch) PASSES them, and mutants of the solution
  are killed at or above ``CERTIFIED_KILL_RATE`` (see arena.benchmark.mutation).
- ``verified``: certified, and the baseline-fails / reference-passes verdicts
  held across repeated runs (the determinism gate). A flaky case whose verdict
  wobbles between runs cannot be trusted to score a repair, so it stays at
  ``certified`` no matter how good its single-run gates look.

The determinism gate is opt-in (``determinism_runs >= 2``) because it re-runs
the suite; without it a case tops out at ``certified``.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from arena.benchmark.mutation import run_mutation_test
from arena.benchmark.snapshot import snapshot_pack
from arena.benchmark.solution import fixed_solution
from arena.core.models import BenchmarkCase
from arena.execution.commands import is_pytest_command
from arena.execution.test_executor import TestExecutionRequest, TestExecutionResult, TestExecutor

# A certified case's tests must kill at least this fraction of viable mutants:
# evidence the suite distinguishes the real fix from plausible-but-wrong code.
CERTIFIED_KILL_RATE = 0.5

# Weakest-to-strongest ordering for the certification ladder.
LEVELS = ("draft", "development", "certified", "verified")


@dataclass
class CaseCertification:
    case_id: str
    executable: bool
    # False when the case has runnable tests but no backend ran them (no
    # --allow-local-execution and no present docker image): the gates below are
    # then meaningless, so this is reported instead of as gate failures.
    executed: bool = True
    baseline_fails: bool | None = None
    reference_passes: bool | None = None
    mutant_total: int = 0
    mutant_kill_rate: float | None = None
    determinism_runs: int = 0
    deterministic: bool | None = None  # None: not checked.

    @property
    def mutation_adequate(self) -> bool:
        """Whether the mutation gate is satisfied.

        A case with no viable mutants (nothing the operators can flip) has no
        mutation evidence either way; the baseline-fails and reference-passes
        gates still bound it, so it is not held back on this gate alone.
        """
        if self.mutant_total == 0:
            return True
        return self.mutant_kill_rate is not None and self.mutant_kill_rate >= CERTIFIED_KILL_RATE

    @property
    def certified(self) -> bool:
        return (
            self.executable
            and self.baseline_fails is True
            and self.reference_passes is True
            and self.mutation_adequate
        )

    @property
    def level(self) -> str:
        if not self.executable:
            return "draft"
        if not self.certified:
            return "development"
        if self.deterministic is True:
            return "verified"
        return "certified"


@dataclass
class PackCertification:
    pack: str
    cases: list[CaseCertification] = field(default_factory=list)

    @property
    def level(self) -> str:
        """The weakest rung among executable cases (draft if there are none)."""
        executable = [case for case in self.cases if case.executable]
        if not executable:
            return "draft"
        return min((case.level for case in executable), key=LEVELS.index)


def _run_tests_in(
    case: BenchmarkCase,
    source_dir: Path | None,
    *,
    executor: TestExecutor,
    allow_local_execution: bool,
) -> TestExecutionResult | None:
    """Run the case's tests against a copy of ``source_dir`` plus the hidden tests."""
    if source_dir is None or not source_dir.is_dir() or not case.execution.test_command:
        return None
    assert case.case_dir is not None
    with tempfile.TemporaryDirectory(prefix=f"arena-certify-{case.id}-") as directory:
        workspace = Path(directory)
        shutil.copytree(source_dir, workspace, dirs_exist_ok=True)
        tests_dir = case.input.tests_dir
        if tests_dir and (case.case_dir / tests_dir).is_dir():
            shutil.copytree(case.case_dir / tests_dir, workspace / tests_dir, dirs_exist_ok=True)
        return executor.execute(
            TestExecutionRequest(
                case_id=case.id,
                workspace_path=workspace,
                test_command=case.execution.test_command,
                timeout_seconds=case.execution.timeout_seconds,
                docker_image=case.execution.docker_image,
                allow_local_execution=allow_local_execution,
            )
        )


# For a pytest baseline, exit codes distinguish a real test failure from an
# inability to run the tests: 0 = all passed, 1 = tests ran and at least one
# failed, 2 = collection/interrupted, 3 = internal error, 4 = usage error,
# 5 = no tests collected. Only exit 1 is the seeded bug making a test fail; the
# rest would certify a broken case (one that cannot import/collect, or has no
# tests) rather than the bug. This filter is pytest-specific and must not be
# applied to other runners, which use their own codes (cargo test exits 101 on
# failure), so a non-pytest baseline keeps generic nonzero-failure handling.
_PYTEST_GENUINE_FAILURE_EXIT_CODE = 1


def _is_pytest_command(test_command: str | list[str] | list[list[str]] | None) -> bool:
    """True if the case runs its tests through pytest (directly or python -m pytest).

    Delegates to the shared helper so certification and the integrity track cannot
    disagree about which commands pytest's exit-code vocabulary applies to.
    """
    return is_pytest_command(test_command)


def _pytest_failed_to_start(result: TestExecutionResult) -> bool:
    """True when the interpreter never launched pytest (not a genuine test fail).

    ``python -m pytest`` exits 1 with ``No module named pytest`` when the
    module is missing. That is the same code pytest uses for "tests ran and
    failed", so it must not count as a seeded-bug baseline failure.
    """
    blob = f"{result.stderr}\n{result.stdout}"
    return "No module named pytest" in blob


def _baseline_fails(result: TestExecutionResult | None, *, pytest_command: bool) -> bool:
    if not (result and result.ran and not result.timed_out and not result.passed):
        return False
    # Only pytest baselines get the exit-code filter; any other runner that
    # genuinely failed (nonzero, not a timeout) is accepted, since we cannot
    # portably tell its collection error from its test failure.
    if pytest_command:
        if _pytest_failed_to_start(result):
            return False
        return result.exit_code == _PYTEST_GENUINE_FAILURE_EXIT_CODE
    return True


def _reference_passes(result: TestExecutionResult | None) -> bool:
    return bool(result and result.ran and result.passed)


def _check_determinism(
    case: BenchmarkCase,
    *,
    executor: TestExecutor,
    allow_local_execution: bool,
    runs: int,
) -> bool:
    """Re-run the baseline and reference ``runs`` times; verdicts must hold.

    A case is deterministic when the baseline fails on every run and the
    reference passes on every run. Any wobble (a baseline that sometimes passes,
    a reference that sometimes fails) means the case cannot reliably grade a
    repair, so it does not earn the ``verified`` rung.
    """
    assert case.case_dir is not None
    after_dir = case.case_dir / case.input.after_dir
    pytest_command = _is_pytest_command(case.execution.test_command)
    for _ in range(runs):
        baseline = _run_tests_in(
            case, after_dir, executor=executor, allow_local_execution=allow_local_execution
        )
        if not _baseline_fails(baseline, pytest_command=pytest_command):
            return False
    with fixed_solution(case) as fixed:
        for _ in range(runs):
            reference = _run_tests_in(
                case, fixed, executor=executor, allow_local_execution=allow_local_execution
            )
            if not _reference_passes(reference):
                return False
    return True


def certify_case(
    case: BenchmarkCase,
    *,
    executor: TestExecutor | None = None,
    allow_local_execution: bool = False,
    mutation_limit: int = 20,
    determinism_runs: int = 1,
) -> CaseCertification:
    executor = executor or TestExecutor()
    if not case.execution.run_tests or not case.execution.test_command:
        return CaseCertification(case_id=case.id, executable=False)

    assert case.case_dir is not None
    # The buggy PR state (after/) must FAIL the tests: the bug is exercised.
    baseline = _run_tests_in(
        case,
        case.case_dir / case.input.after_dir,
        executor=executor,
        allow_local_execution=allow_local_execution,
    )
    if baseline is None or not baseline.ran:
        # No backend ran the tests. Report that plainly instead of as gate
        # failures, and skip the reference and mutation runs, which would also
        # be skipped. The case is executable but unexecuted, so it cannot certify.
        return CaseCertification(case_id=case.id, executable=True, executed=False)
    # The corrected solution (after/ + reference.patch) must PASS the tests.
    with fixed_solution(case) as fixed:
        reference = _run_tests_in(
            case, fixed, executor=executor, allow_local_execution=allow_local_execution
        )
    mutation = run_mutation_test(
        case, executor=executor, allow_local_execution=allow_local_execution, limit=mutation_limit
    )
    certification = CaseCertification(
        case_id=case.id,
        executable=True,
        baseline_fails=_baseline_fails(
            baseline, pytest_command=_is_pytest_command(case.execution.test_command)
        ),
        reference_passes=_reference_passes(reference),
        mutant_total=mutation.total,
        mutant_kill_rate=mutation.kill_rate,
    )
    # The determinism gate only matters for a case that already certified, and
    # re-running is expensive, so it is opt-in and skipped otherwise.
    if certification.certified and determinism_runs >= 2:
        certification.determinism_runs = determinism_runs
        certification.deterministic = _check_determinism(
            case,
            executor=executor,
            allow_local_execution=allow_local_execution,
            runs=determinism_runs,
        )
    return certification


def certify_pack(
    benchmark_dir: Path,
    *,
    allow_local_execution: bool = False,
    mutation_limit: int = 20,
    determinism_runs: int = 1,
) -> PackCertification:
    # One snapshot for the whole certification: baseline, reference, determinism,
    # and mutant gates all read the same immutable tree, never the source.
    executor = TestExecutor()
    result = PackCertification(pack=benchmark_dir.name)
    with snapshot_pack(benchmark_dir) as snapshot:
        for case in snapshot.load_and_validate():
            result.cases.append(
                certify_case(
                    case,
                    executor=executor,
                    allow_local_execution=allow_local_execution,
                    mutation_limit=mutation_limit,
                    determinism_runs=determinism_runs,
                )
            )
        snapshot.verify()
    return result
