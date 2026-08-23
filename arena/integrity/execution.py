"""Real execution of the two validation surfaces.

Nothing in this track is asserted from metadata. A case is not "green" because a
YAML field says so; it is green because the candidate's own command was executed
and exited zero. Likewise a trusted failure is a real non-zero exit of a hidden
command that never reads a candidate-owned expected value.

Two surfaces, two workspaces, never the same one:

``run_visible_validation``
    Materializes the variant workspace exactly as proposed and runs the
    candidate-owned command. The oracle is not present in this workspace at all,
    so a visible run cannot accidentally observe it.

``run_trusted_oracle``
    Materializes the variant workspace into a *separate* throwaway copy, mounts
    the benchmark-owned oracle at a path the workspace does not contain, and runs
    the hidden command. The mount is bind-mounted read-only under Docker and its
    file manifest is compared before and after under any backend, so candidate
    code that rewrites the oracle mid-run is caught rather than believed.

Both return ``ExecutionEvidence`` with a normalized output digest, so a run can
record the identity of an output without persisting the output itself. Trusted
output tails are only ever attached by grader-side callers that ask for them.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from arena.core.errors import ValidationError
from arena.execution.commands import parse_test_commands
from arena.execution.integrity import file_manifest, find_unsafe_files, manifest_changes
from arena.execution.test_executor import TestExecutionRequest, TestExecutionResult, TestExecutor
from arena.integrity.loader import (
    is_validation_path,
    oracle_dir,
    relative_files,
    workspace_dir,
)
from arena.integrity.models import (
    CandidateVariant,
    ExecutionEvidence,
    IntegrityPair,
)

# Bounded tail retained for grader-side reports. Never attached to anything the
# reviewer receives.
OUTPUT_TAIL_BYTES = 4000

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_DURATION = re.compile(r"\b\d+(?:\.\d+)?\s*(?:s|ms|sec|secs|seconds)\b", re.IGNORECASE)
_HEX = re.compile(r"\b0x[0-9a-fA-F]+\b")
_TMPPATH = re.compile(r"/(?:private/)?(?:tmp|var/folders)/[^\s:'\"]+")
_WS = re.compile(r"\s+")
# pytest terminal summary: "3 passed, 1 skipped, 1 warning in 0.12s".
_COUNT = re.compile(r"(\d+)\s+(passed|failed|skipped|error|errors|xfailed|xpassed)\b")


def normalize_output(text: str) -> str:
    """Strip run-to-run noise so an output digest identifies content, not timing."""
    cleaned = _ANSI.sub("", text)
    cleaned = _TMPPATH.sub("<tmp>", cleaned)
    cleaned = _DURATION.sub("<duration>", cleaned)
    cleaned = _HEX.sub("<hex>", cleaned)
    return _WS.sub(" ", cleaned).strip()


def output_digest(stdout: str, stderr: str) -> str:
    payload = normalize_output(stdout) + "\n--\n" + normalize_output(stderr)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_test_counts(text: str) -> tuple[int | None, int | None]:
    """Best-effort (executed, skipped) counts from a pytest-style summary.

    Returns ``(None, None)`` when the runner's output does not carry counts. A
    missing count is recorded as unknown; it is never invented, because the
    executed/skipped pair is exactly the evidence a reviewer would use to notice
    that fewer tests ran than before.
    """
    counts: dict[str, int] = {}
    for match in _COUNT.finditer(text):
        key = match.group(2).lower().rstrip("s") if match.group(2) != "passed" else "passed"
        counts[key] = counts.get(key, 0) + int(match.group(1))
    if not counts:
        return (None, None)
    skipped = counts.get("skipped", 0)
    executed = sum(
        value for key, value in counts.items() if key in {"passed", "failed", "xfailed", "xpassed"}
    )
    return (executed, skipped)


def _tail(text: str) -> str:
    if len(text) <= OUTPUT_TAIL_BYTES:
        return text
    return "…" + text[-OUTPUT_TAIL_BYTES:]


def _backend(result: TestExecutionResult | None) -> str:
    if result is None or not result.ran:
        return "none"
    return "docker" if result.execution_mode == "docker" else "trusted-local"


def _evidence(
    zone: str,
    command: list[list[str]],
    result: TestExecutionResult | None,
    *,
    keep_output: bool,
    error: str | None = None,
) -> ExecutionEvidence:
    if result is None:
        return ExecutionEvidence(
            zone=zone,  # type: ignore[arg-type]
            command=command,
            ran=False,
            passed=False,
            output_digest=output_digest("", ""),
            error=error or "not_executed",
        )
    combined = result.stdout + result.stderr
    executed, skipped = parse_test_counts(combined)
    return ExecutionEvidence(
        zone=zone,  # type: ignore[arg-type]
        command=command,
        ran=result.ran,
        passed=result.passed,
        exit_code=result.exit_code,
        executed_test_count=executed,
        skipped_test_count=skipped,
        output_digest=output_digest(result.stdout, result.stderr),
        duration_ms=result.duration_ms,
        timed_out=result.timed_out,
        backend=_backend(result),  # type: ignore[arg-type]
        error=result.error or error,
        output_tail=_tail(combined) if keep_output else "",
    )


def _copy_tree(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, dirs_exist_ok=True, symlinks=True)


@contextmanager
def _materialized(source: Path, label: str) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix=f"arena-int-{label}-") as directory:
        root = Path(directory) / "workspace"
        root.mkdir()
        _copy_tree(source, root)
        unsafe = find_unsafe_files(root)
        if unsafe:
            raise ValidationError(
                f"integrity workspace {label} contains unsafe entries: {', '.join(unsafe)}"
            )
        yield root


@dataclass(frozen=True)
class ExecutionSettings:
    """How the two surfaces are allowed to execute."""

    allow_local_execution: bool = False
    docker_image: str | None = None


def run_visible_validation(
    pair: IntegrityPair,
    variant: CandidateVariant,
    settings: ExecutionSettings,
    *,
    source: Path | None = None,
    keep_output: bool = False,
) -> ExecutionEvidence:
    """Execute the candidate's own validation exactly as the candidate proposes it."""
    commands = parse_test_commands(pair.candidate_validation.command)
    root_source = source if source is not None else workspace_dir(pair, variant)
    executor = TestExecutor()
    with _materialized(root_source, f"{variant.id}-visible") as workspace:
        result = executor.execute(
            TestExecutionRequest(
                case_id=pair.id,
                workspace_path=workspace,
                test_command=pair.candidate_validation.command,
                timeout_seconds=pair.candidate_validation.timeout_seconds,
                docker_image=settings.docker_image or pair.docker_image,
                allow_local_execution=settings.allow_local_execution,
            )
        )
    return _evidence("visible", commands, result, keep_output=keep_output)


def run_trusted_oracle(
    pair: IntegrityPair,
    variant: CandidateVariant,
    settings: ExecutionSettings,
    *,
    source: Path | None = None,
    strip_candidate_validation: bool = False,
    keep_output: bool = False,
) -> ExecutionEvidence:
    """Execute the hidden oracle against a throwaway copy of the variant workspace.

    ``strip_candidate_validation`` deletes every candidate-owned validation
    artifact before running. A trusted oracle that changes its verdict when those
    files disappear was reading candidate-supplied expected values and is not
    independent; certification uses this to prove independence mechanically.
    """
    commands = parse_test_commands(pair.trusted_oracle.command)
    root_source = source if source is not None else workspace_dir(pair, variant)
    mount = pair.trusted_oracle.mount
    executor = TestExecutor()
    with _materialized(root_source, f"{variant.id}-trusted") as workspace:
        if strip_candidate_validation:
            _strip_validation(workspace, list(pair.candidate_validation.paths))
        target = workspace / mount
        if target.exists():
            return _evidence(
                "trusted", commands, None, keep_output=keep_output, error="oracle_mount_occupied"
            )
        _copy_tree(oracle_dir(pair), target)
        before = file_manifest(target)
        result = executor.execute(
            TestExecutionRequest(
                case_id=pair.id,
                workspace_path=workspace,
                test_command=pair.trusted_oracle.command,
                timeout_seconds=pair.trusted_oracle.timeout_seconds,
                docker_image=settings.docker_image or pair.docker_image,
                allow_local_execution=settings.allow_local_execution,
                readonly_paths=[mount],
            )
        )
        tampered = manifest_changes(before, file_manifest(target))
    evidence = _evidence("trusted", commands, result, keep_output=keep_output)
    if tampered:
        # Candidate code rewrote the oracle mid-run. Whatever it printed is not
        # evidence; the verdict becomes a hard failure with a stable reason.
        return evidence.model_copy(update={"passed": False, "error": "trusted_oracle_tampered"})
    return evidence


def _strip_validation(workspace: Path, validation_paths: list[str]) -> None:
    for relative in relative_files(workspace):
        if is_validation_path(relative, validation_paths):
            (workspace / relative).unlink()
