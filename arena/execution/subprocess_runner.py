"""Controlled local command execution for benchmark fixtures."""

from __future__ import annotations

import shlex
import time
from pathlib import Path

from pydantic import BaseModel

from arena.core.errors import ExecutionError
from arena.execution.commands import pin_interpreter
from arena.execution.hardening import resource_limiter, sandboxed_home_env
from arena.execution.process import run_supervised

# Match the test executor's cap so all pack-controlled command output is bounded.
_OUTPUT_LIMIT_BYTES = 512_000


class CommandResult(BaseModel):
    command: str
    return_code: int
    output: str
    duration_ms: int
    timed_out: bool = False


def run_command(command: str, cwd: Path, timeout_seconds: int) -> CommandResult:
    """Run a pack-controlled command through the bounded process supervisor.

    Goes through run_supervised so the command gets process-tree cleanup, a hard
    byte-bounded output cap, and the same POSIX-only / Windows-fail-closed
    boundary as test execution -- instead of a bare subprocess.run that buffers
    unbounded output and leaves descendants running. ExecutionError (Windows)
    propagates so the caller fails closed.
    """
    started = time.perf_counter()
    arguments = pin_interpreter(shlex.split(command))
    try:
        with sandboxed_home_env() as env:
            result = run_supervised(
                arguments,
                cwd=cwd,
                env=env,
                timeout=timeout_seconds,
                preexec_fn=resource_limiter(timeout_seconds),
                output_limit=_OUTPUT_LIMIT_BYTES,
            )
    except FileNotFoundError as exc:
        return CommandResult(
            command=command,
            return_code=127,
            output=f"Command unavailable: {exc}",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    except ExecutionError as exc:
        # Missing executables fail closed as unavailable. Windows fail-closed
        # ("not supported on Windows") still propagates to the caller.
        if "command not found" not in str(exc):
            raise
        return CommandResult(
            command=command,
            return_code=127,
            output=f"Command unavailable: {exc}",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    output = (result.stdout + "\n" + result.stderr).strip()
    return CommandResult(
        command=command,
        return_code=124 if result.timed_out else (result.returncode or 0),
        output=output,
        duration_ms=int((time.perf_counter() - started) * 1000),
        timed_out=result.timed_out,
    )
