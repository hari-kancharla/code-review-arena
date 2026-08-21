"""The process supervisor kills the whole tree on timeout and caps output."""

import os
import sys
import time
from pathlib import Path

import pytest

from arena.core.errors import ExecutionError
from arena.execution.process import run_supervised
from arena.execution.test_executor import TestExecutionRequest, TestExecutor

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group kill")


def _path_env() -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", "")}


def test_run_supervised_fails_closed_on_windows(monkeypatch):
    # Windows has no tree-kill or byte cap here, so command execution must fail
    # closed rather than run unbounded. Patch the platform so this is verified on
    # any host; the windows-latest CI job exercises the real thing.
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(ExecutionError, match="not supported on Windows"):
        run_supervised(["echo", "hi"], cwd=Path("."), env=_path_env(), timeout=5)


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows fail-closed check")
def test_run_supervised_fails_closed_on_real_windows():
    with pytest.raises(ExecutionError):
        run_supervised(["cmd", "/c", "echo hi"], cwd=Path("."), env=_path_env(), timeout=5)


@posix_only
def test_run_supervised_kills_descendants_on_timeout(tmp_path):
    marker = tmp_path / "survived.txt"
    # The child backgrounds a grandchild that writes a marker after 2s, then
    # sleeps far past the timeout. If the tree is killed, the marker never lands.
    script = (
        "import subprocess, time\n"
        f"subprocess.Popen(['sh', '-c', 'sleep 2; echo alive > {marker}'])\n"
        "time.sleep(30)\n"
    )
    result = run_supervised(
        [sys.executable, "-c", script], cwd=tmp_path, env=_path_env(), timeout=1
    )
    assert result.timed_out is True
    time.sleep(3)  # well past the grandchild's 2s write window
    assert not marker.exists()


@posix_only
def test_run_supervised_caps_output_at_the_byte_limit(tmp_path):
    result = run_supervised(
        [sys.executable, "-c", "print('x' * 5000)"],
        cwd=tmp_path,
        env=_path_env(),
        timeout=10,
        output_limit=100,
    )
    assert result.timed_out is False
    assert result.output_limit_exceeded is True
    assert len(result.stdout.encode("utf-8")) <= 100


@posix_only
def test_executor_reports_timeout(tmp_path):
    request = TestExecutionRequest(
        case_id="c",
        workspace_path=tmp_path,
        test_command=[sys.executable, "-c", "import time; time.sleep(30)"],
        timeout_seconds=1,
        allow_local_execution=True,
    )
    result = TestExecutor().execute(request)
    assert result.timed_out is True
    assert result.passed is False
    assert result.error == "test_execution_timed_out"


@posix_only
def test_descendants_do_not_survive_a_clean_exit(tmp_path: Path):
    """A helper left behind by a command that exited 0 must still be killed.

    The group was only signalled on timeout, overflow, or a child that would not
    reap. A fixture that backgrounds a helper with detached stdio hits none of
    those: both pipes reach EOF at once and wait() returns immediately, so the
    helper survived the run, held its port and CPU into later cases, and made the
    next case's tests fail to bind -- recording a correct repair as tests_failed.
    """
    marker = tmp_path / "alive.txt"
    child = tmp_path / "child.py"
    grandchild = (
        "import time\n"
        "while True:\n"
        f"    open({str(marker)!r}, 'a').write('x')\n"
        "    time.sleep(0.05)\n"
    )
    child.write_text(
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {grandchild!r}],\n"
        "  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)\n"
        "time.sleep(0.4)\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )

    result = run_supervised([sys.executable, str(child)], cwd=tmp_path, env=_path_env(), timeout=15)
    assert result.returncode == 0
    assert result.timed_out is False

    # The grandchild really did run, so this is a meaningful check...
    before = marker.stat().st_size if marker.exists() else 0
    assert before > 0
    # ...and it stops as soon as the supervisor returns.
    time.sleep(1.0)
    assert (marker.stat().st_size if marker.exists() else 0) == before
