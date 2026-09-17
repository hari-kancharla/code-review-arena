"""Setup diagnostics: never invoke reviewers, tests, or containers."""

from __future__ import annotations

import importlib.util
import json
import shlex
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from arena.benchmark.snapshot import snapshot_pack
from arena.core import limits
from arena.core.config import trusted_pack_hashes
from arena.core.errors import ArenaError
from arena.core.registry import create_reviewer
from arena.execution.commands import parse_test_commands, pin_interpreter
from arena.execution.test_executor import TestExecutor


@dataclass(frozen=True)
class Check:
    name: str
    status: Literal["pass", "warn", "fail"]
    detail: str


def check_reviewer(spec: str, command: str | None, image: str | None) -> list[Check]:
    # Never echo commands or endpoint URLs: they can contain credentials.
    for value, cap in (
        (spec, limits.REVIEWER_ID_LEN),
        (command, limits.COMMAND_LEN),
        (image, limits.DOCKER_IMAGE_REF_LEN),
    ):
        if value is not None and len(value) > cap:
            return [Check("reviewer", "fail", "Reviewer option exceeds run limits.")]
    try:
        create_reviewer(spec, command=command, docker_image=image)
    except ArenaError:
        return [Check("reviewer", "fail", "Unknown reviewer or missing --command/--docker-image.")]
    if spec.startswith(("http:", "openai:")):
        try:
            url = urlsplit(spec.partition(":")[2])
            valid = url.scheme in {"http", "https"} and bool(url.hostname)
            _ = url.port
        except ValueError:
            valid = False
        return [
            Check(
                "reviewer",
                "warn" if valid else "fail",
                "Endpoint syntax accepted; connectivity, credentials and model NOT checked."
                if valid
                else "Reviewer endpoint requires a valid HTTP(S) URL.",
            )
        ]
    if spec not in {"custom-command", "docker-command"}:
        return [
            Check("reviewer", "warn", "Built-in configuration accepted; behavior not executed.")
        ]
    try:
        args = shlex.split(command or "")
    except ValueError:
        args = []
    if not args or not args[0]:
        return [Check("reviewer", "fail", "Command is empty or has unmatched quotes.")]
    if spec == "custom-command" and shutil.which(args[0]) is None:
        return [Check("reviewer", "fail", "Host reviewer executable not found or not executable.")]
    return [
        Check(
            "reviewer",
            "warn",
            "Command syntax accepted; payload consumption and output contract NOT checked. "
            + (
                "Host execution is not isolated."
                if spec == "custom-command"
                else "In-image executable and image contents remain operator-trusted."
            ),
        )
    ]


def collect_checks(
    benchmark_set: Path,
    reviewer: str | None = None,
    command: str | None = None,
    docker_image: str | None = None,
    mode: Literal["review", "patch", "full"] = "full",
    allow_local_execution: bool = False,
) -> list[Check]:
    checks = [
        Check(
            "python",
            "pass" if sys.version_info >= (3, 11) else "fail",
            f"Python {sys.version_info.major}.{sys.version_info.minor}; requires 3.11+.",
        )
    ]
    images: set[str] = set()
    if reviewer is not None:
        reviewer_checks = check_reviewer(reviewer, command, docker_image)
        checks.extend(reviewer_checks)
        if reviewer == "docker-command" and docker_image:
            if not any(check.status == "fail" for check in reviewer_checks):
                images.add(docker_image)
        elif docker_image:
            checks.append(
                Check("options", "fail", "--docker-image requires --reviewer docker-command.")
            )
        if command is not None and reviewer not in {"custom-command", "docker-command"}:
            checks.append(Check("options", "fail", "--command requires a command reviewer."))
    elif command is not None or docker_image is not None:
        checks.append(Check("reviewer", "fail", "Specify --reviewer with command/image options."))
    else:
        checks.append(
            Check("reviewer", "warn", "No reviewer selected; reviewer setup not checked.")
        )
    try:
        with snapshot_pack(benchmark_set) as snapshot:
            cases = snapshot.load_and_validate()
            checks.append(
                Check("pack", "pass", f"Validated {len(cases)} cases; sha256={snapshot.checksum}")
            )
            if snapshot.stored_checksum and snapshot.stored_checksum != snapshot.checksum:
                checks.append(
                    Check("pack-checksum", "warn", "Stored pack checksum differs from content.")
                )
            local_commands: list[list[str]] = []
            if mode != "review":
                for case in cases:
                    execution = case.execution
                    if not execution.run_tests or not execution.test_command:
                        continue
                    if execution.docker_image:
                        images.add(execution.docker_image)
                    else:
                        local_commands.extend(parse_test_commands(execution.test_command))
                if local_commands:
                    allowed_hashes = trusted_pack_hashes()
                    permitted = allow_local_execution and (
                        not allowed_hashes or snapshot.checksum in allowed_hashes
                    )
                    checks.append(
                        Check(
                            "local-execution",
                            "warn" if permitted else "fail",
                            "Trusted-local execution opted in; not isolated."
                            if permitted
                            else "Image-less tests require --allow-local-execution "
                            "and admission by ARENA_TRUSTED_PACK_HASHES when configured.",
                        )
                    )
                    if any(
                        pin_interpreter(argv)[0:3] == [sys.executable, "-m", "pytest"]
                        for argv in local_commands
                    ):
                        found = importlib.util.find_spec("pytest") is not None
                        checks.append(
                            Check(
                                "pytest",
                                "pass" if found else "fail",
                                "pytest available in harness interpreter."
                                if found
                                else 'Install: python -m pip install "codereview-arena[run]"',
                            )
                        )
                    checks.append(
                        Check(
                            "dependencies",
                            "warn",
                            "Other local runners and case dependencies are not checked.",
                        )
                    )
    except (ArenaError, OSError) as exc:
        checks.append(
            Check(
                "pack",
                "fail",
                f"Cannot validate pack ({type(exc).__name__}); run arena validate for details.",
            )
        )
    if images:
        available = TestExecutor._docker_available()
        checks.append(
            Check(
                "docker",
                "pass" if available else "fail",
                "Docker daemon reachable."
                if available
                else "Docker CLI/daemon unavailable or probe timed out.",
            )
        )
        for image in sorted(images):
            present = available and TestExecutor._image_present(image)
            checks.append(
                Check(
                    "docker-image",
                    "pass" if present else "fail",
                    f"{image}: "
                    + (
                        "present locally; contents not certified."
                        if present
                        else "unavailable; build/vet locally (never pulled)."
                    ),
                )
            )
    else:
        checks.append(
            Check(
                "docker", "pass", "No Docker image required by selected configuration; not probed."
            )
        )
    return checks


def preflight(
    benchmark_set: Path,
    reviewer: str | None,
    command: str | None,
    docker_image: str | None,
    mode: Literal["review", "patch", "full"],
    allow_local_execution: bool,
    as_json: bool,
) -> None:
    checks = collect_checks(
        benchmark_set, reviewer, command, docker_image, mode, allow_local_execution
    )
    ok = all(check.status != "fail" for check in checks)
    scope = (
        "Setup checks only; reviewer behavior, image contents "
        "and benchmark outcomes are not certified."
    )
    if as_json:
        print(
            json.dumps(
                {
                    "schema_version": "1",
                    "ok": ok,
                    "scope": scope,
                    "checks": [asdict(check) for check in checks],
                }
            )
        )
    else:
        table = Table(title="Arena preflight", caption=scope)
        for column in ("Check", "Status", "Detail"):
            table.add_column(column)
        for check in checks:
            table.add_row(check.name, check.status, Text(check.detail))
        Console().print(table)
    if not ok:
        raise typer.Exit(code=1)
