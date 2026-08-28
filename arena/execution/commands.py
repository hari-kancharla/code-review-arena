"""Parsing and normalization for fixture test commands.

Fixture commands always run without a shell. A case may declare
``test_command`` as a single command string, one argv list, or a list of argv
lists (each executed in order, all must pass). Shell operators have no meaning
without a shell, so commands relying on them are rejected up front, at
``arena validate`` time rather than mid-run.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import PurePosixPath

from arena.core.errors import ValidationError

_SHELL_OPERATORS = {"&&", "||", ";", "|", ">", ">>", "<", "<<", "&"}


def _bare_program(argv: list[str]) -> str | None:
    """The command name, but only when it is a bare name resolved through PATH.

    A path-qualified command (``.venv/bin/python``, ``/opt/model-env/bin/pytest``)
    is a deliberate choice of interpreter: the caller wants that environment's
    packages, not ours. Rewriting it to a different interpreter silently strips
    every dependency it was installed with, so qualified commands are returned as
    None and left alone. Only a bare name, which would otherwise be resolved from
    an ambiguous PATH, is eligible for pinning.
    """
    if not argv:
        return None
    program = argv[0]
    if "/" in program or "\\" in program:
        return None
    return PurePosixPath(program).name


def pin_interpreter(argv: list[str]) -> list[str]:
    """Pin bare python/pytest invocations to the harness interpreter.

    Only valid for local execution; inside a container the image's own
    interpreter must be used, so use pin_container_interpreter there instead.
    A path-qualified interpreter is always left as written.
    """
    program = _bare_program(argv)
    if program == "pytest":
        return [sys.executable, "-m", "pytest", *argv[1:]]
    if program in {"python", "python3"}:
        return [sys.executable, *argv[1:]]
    return argv


def pin_container_interpreter(argv: list[str]) -> list[str]:
    """Route python/pytest through the container's own ``python -m``.

    The ``pytest`` console script does not put the working directory on
    ``sys.path``, so a case whose tests import a top-level module from the
    workspace root fails to collect inside the container. ``python -m pytest``
    does add the workspace root, and every benchmark image ships python, so we
    normalize to it. Unlike pin_interpreter this keeps the bare ``python`` name
    (the image's interpreter), never the harness's sys.executable. A
    path-qualified command is left as written, for the same reason as there.
    """
    program = _bare_program(argv)
    if program == "pytest":
        return ["python", "-m", "pytest", *argv[1:]]
    if program in {"python", "python3"}:
        return ["python", *argv[1:]]
    return argv


def is_pytest_command(value: str | list | None) -> bool:
    """True if a command runs its tests through pytest (directly or ``python -m``).

    Callers use this to decide whether pytest's exit-code vocabulary applies: exit
    1 is a genuine test failure, while 2-5 mean the suite could not be collected or
    run at all. Other runners have their own codes, so the distinction must never
    be applied to them.
    """
    try:
        commands = parse_test_commands(value)
    except ValidationError:
        return False
    for argv in commands:
        if not argv:
            continue
        program = PurePosixPath(argv[0]).name
        if program == "pytest":
            return True
        if program in {"python", "python3"} and "-m" in argv:
            index = argv.index("-m")
            if index + 1 < len(argv) and argv[index + 1] == "pytest":
                return True
    return False


def _validate_argv(argv: list[str], original: object) -> list[str]:
    if not argv:
        raise ValidationError(f"test_command contains an empty command: {original!r}")
    for token in argv:
        if not isinstance(token, str):
            raise ValidationError(f"test_command tokens must be strings: {original!r}")
        if token in _SHELL_OPERATORS:
            raise ValidationError(
                f"test_command uses shell operator {token!r}; commands run without a "
                f"shell; declare a list of argv commands instead: {original!r}"
            )
    head = argv[0]
    if any(operator in head for operator in (";", "|", "&")):
        raise ValidationError(f"test_command program name looks like a shell snippet: {head!r}")
    return argv


def parse_test_commands(value: str | list | None) -> list[list[str]]:
    """Normalize a case test_command into an ordered list of argv commands."""
    if value is None:
        return []
    if isinstance(value, str):
        if not value.strip():
            return []
        try:
            argv = shlex.split(value)
        except ValueError as exc:
            raise ValidationError(f"test_command is not parseable: {value!r} ({exc})") from exc
        return [_validate_argv(argv, value)]
    if isinstance(value, list):
        if all(isinstance(item, str) for item in value):
            return [_validate_argv(list(value), value)] if value else []
        if all(isinstance(item, list) for item in value):
            return [_validate_argv(list(item), item) for item in value]
        raise ValidationError(
            f"test_command list must be one argv list or a list of argv lists: {value!r}"
        )
    raise ValidationError(f"test_command has unsupported type {type(value).__name__}")
