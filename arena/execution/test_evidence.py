"""Machine-readable proof that a case's tests actually ran and passed.

A process exit status is not evidence. The suite runs with the candidate's patch
already applied, so reviewer-controlled code executes inside the very process
whose exit code decides the verdict: two lines at the top of the file under
review (``import os; os._exit(0)``) terminate the interpreter with status 0
during collection, and a module planted at the workspace root that shadows
anything pytest imports (``_pytest``, ``pluggy``, ``iniconfig``) does the same
before a single test is collected. Both make a genuinely failing suite look
green while the seeded bug is untouched, and neither trips a protected path, a
new-file check, or the read-only tests mount.

Enumerating module names cannot close that -- any transitive import of the
runner is a fresh shadow target. So the verdict requires POSITIVE evidence
instead: pytest writes a JUnit XML report to a path outside the workspace, and a
run counts as passing only when that report exists, is well formed, records at
least one test, and records no failures or errors. Sabotage that prevents the
suite from running also prevents the report from being written, which is exactly
the signal we want.

Residual limitations, stated plainly rather than papered over. Both need the
reviewer/execution isolation boundary -- a runner the candidate cannot reach --
which docs/trusted-evaluation-architecture.md tracks as a later phase:

1. The patched code runs in the same container as the runner, so a sufficiently
   determined patch could locate the report and forge a passing one before
   exiting. The report filename carries a per-run nonce so the path cannot
   simply be hardcoded.
2. A patch can skip *selectively*: raise `pytest.skip()` only from the paths the
   bug-covering tests exercise, letting every unrelated test execute and pass.
   Requiring at least one executed test (below) rejects a blanket skip, but a
   surgical one still produces a report with executed passing tests. Closing it
   completely means comparing the candidate's executed node ids against the set
   the certified reference run executed, which needs that expected set recorded
   per case at certification time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

# Where the evidence directory is mounted inside a container. It is the only
# writable bind mount besides the workspace itself.
CONTAINER_EVIDENCE_DIR = PurePosixPath("/arena-evidence")


@dataclass(frozen=True)
class TestReport:
    """Counts read back from a JUnit XML report."""

    tests: int
    failures: int
    errors: int
    skipped: int

    @property
    def executed(self) -> int:
        """Tests that actually ran. JUnit's `tests` attribute counts skips too."""
        return max(self.tests - self.skipped, 0)

    @property
    def ran_and_passed(self) -> bool:
        """At least one test EXECUTED and nothing failed or errored.

        Skipped tests are not evidence. Counting them was a real hole: a patch
        that raises `pytest.skip()` from the file under review turns the whole
        suite green -- exit code 0, a well-formed report, `failures=0`,
        `errors=0` -- while the seeded bug sits untouched. A report with zero
        tests is likewise not a pass: collection that finds nothing is
        indistinguishable from a suite that was prevented from running.
        """
        return self.executed > 0 and self.failures == 0 and self.errors == 0


def is_pytest_command(argv: list[str]) -> bool:
    """Whether this argv invokes pytest, and so can emit a JUnit report.

    Both interpreter pinnings normalize to `<interpreter> -m pytest`, so the
    module form is what the executor actually sees; the bare-program form is
    accepted too for a command that was never pinned. A command that reaches
    pytest indirectly (a task runner, a shell wrapper) is deliberately not
    recognised: the evidence requirement is only enforced where the report can be
    demanded directly, and every shipped pack invokes pytest directly.
    """
    if not argv:
        return False
    program = PurePosixPath(argv[0]).name
    if program in {"pytest", "py.test"}:
        return True
    # `-m pytest` anywhere after the interpreter, not only at argv[1]: an
    # interpreter flag may precede it, and some of those flags take a value that
    # is not itself flag-shaped (`python -W error -m pytest`, `python -X dev -m
    # pytest`). Requiring position 1 silently disabled the evidence gate for such
    # commands and fell back to the exit code alone -- the very oracle this
    # module exists to replace. Scanning the whole argv also matches
    # certify._is_pytest_command, so a case cannot be certified under pytest
    # semantics and then scored at runtime as an unrecognised runner.
    for index in range(1, len(argv) - 1):
        if argv[index] == "-m":
            return argv[index + 1] == "pytest"
    return False


def with_report_flag(argv: list[str], report_path: str) -> list[str]:
    """Append the JUnit report flag, without disturbing the fixture's own flags.

    ``-o junit_family=xunit2`` pins the schema so the parser below does not have
    to track pytest's default changing between versions.
    """
    return [*argv, f"--junitxml={report_path}", "-o", "junit_family=xunit2"]


def read_report(path: Path) -> TestReport | None:
    """Parse a JUnit report, or None when it is missing or unusable.

    None is the fail-closed answer: the caller must treat it as "no evidence",
    never as a pass.
    """
    try:
        tree = ElementTree.parse(path)
    except (OSError, ElementTree.ParseError):
        return None
    root = tree.getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    if not suites:
        return None

    def total(attribute: str) -> int:
        value = 0
        for suite in suites:
            try:
                value += int(suite.get(attribute, "0") or 0)
            except ValueError:
                continue
        return value

    return TestReport(
        tests=total("tests"),
        failures=total("failures"),
        errors=total("errors"),
        skipped=total("skipped"),
    )


_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]")


def report_filename(case_id: str, nonce: str, index: int) -> str:
    """Per-command report name, carrying a per-run nonce."""
    return f"{_SAFE_NAME.sub('-', case_id)[:40]}-{index}-{nonce}.xml"
