"""Invoke an external command to produce structured review JSON."""

from __future__ import annotations

import json
import re
import shlex
import shutil
import tempfile
import time
from pathlib import Path

from arena.core import limits
from arena.core.errors import ExecutionError
from arena.core.models import CaseContext, ReviewerResponse
from arena.execution.hardening import sandboxed_home_env
from arena.execution.process import run_supervised
from arena.reviewers.base import BaseReviewer
from arena.reviewers.response_parser import (
    known_paths_from_context,
    parse_reviewer_output,
    response_from_outcome,
)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _invalid_response(raw: str, summary: str, latency_ms: int) -> ReviewerResponse:
    """An invalid custom-command response: parse_status=invalid, raw preserved.

    Counts are None: no valid findings list was ever established.
    """
    return ReviewerResponse(
        raw_response=raw,
        parsed_response=None,
        invalid_output=True,
        parse_attempts=1,
        parse_status="invalid",
        input_finding_count=None,
        retained_finding_count=None,
        parse_error_summary=summary[: limits.PARSE_ERROR_SUMMARY_LEN],
        latency_ms=latency_ms,
    )


def serialize_reviewer_case(
    context: CaseContext,
    *,
    reveal_metadata: bool = False,
    reveal_test_output: bool = False,
) -> dict[str, object]:
    """Blind reviewer payload: case id, stack, diff, and relevant files only.

    Nothing derived from ground truth is included by default. Case
    title/description/category/severity paraphrase the seeded bug, and the
    pre-patch test/static-analysis output reveals the failing assertion's
    expected values (which localize the bug and disclose correct behavior), so
    both are gated behind explicit opt-in flags. A scored blind run leaves them
    off; reveal_metadata is for debugging and reveal_test_output is an openly
    test-assisted mode that the run records and reports separately.
    """
    payload: dict[str, object] = {
        "case_id": context.case.id,
        "stack": context.case.stack,
        "pr_diff": context.diff,
        "relevant_files": context.relevant_files,
    }
    if context.context_truncated:
        payload["context_truncated"] = True
    if reveal_metadata:
        payload["title"] = context.case.title
        payload["category"] = context.case.category
        payload["severity"] = context.case.severity
        payload["description"] = context.case.description
    if reveal_test_output:
        if context.test_output:
            payload["test_output"] = context.test_output
        if context.static_analysis_output:
            payload["static_analysis_output"] = context.static_analysis_output
    return payload


def expand_command_template(
    template: str,
    *,
    case_json: Path,
    diff_file: Path,
    case_id: str,
    workspace: Path,
) -> list[str]:
    expanded = (
        template.replace("{case_json}", str(case_json))
        .replace("{diff_file}", str(diff_file))
        .replace("{case_id}", case_id)
        .replace("{workspace}", str(workspace))
    )
    return shlex.split(expanded)


_SECRET_VALUE = re.compile(r"(?i)(\b\w*(?:key|token|secret|password)\w*\s*[=:]\s*)(\S+)")
_BEARER_VALUE = re.compile(r"(?i)\b(bearer\s+)(\S+)")


def redact_secrets(text: str) -> str:
    """Mask credential-looking values so command templates can be persisted."""
    return _BEARER_VALUE.sub(r"\1***", _SECRET_VALUE.sub(r"\1***", text))


def _absolutize(args: list[str]) -> list[str]:
    """Resolve argv tokens that name a path in the current directory.

    The reviewer is started outside the repository, so any relative path a
    wrapper command carries (its own script, a config it loads) has to be pinned
    to the caller's directory first or the command would simply not be found.
    Only tokens that actually exist are rewritten, so ordinary flags and values
    pass through untouched.
    """
    resolved: list[str] = []
    for token in args:
        candidate = Path(token)
        if not candidate.is_absolute() and candidate.exists():
            resolved.append(str(candidate.resolve()))
        else:
            resolved.append(token)
    return resolved


class CustomCommandReviewer(BaseReviewer):
    name = "custom-command"
    model = "custom"
    # Runs as a host process, so the filesystem -- and with it every pack's
    # reference.patch, hidden tests and ground truth -- is reachable no matter
    # what directory it starts in. Recorded on the run so a score produced this
    # way is never presented as a comparable measurement (see RunMetadata).
    oracle_reachable = True

    def __init__(
        self,
        command_template: str,
        timeout_seconds: int = 120,
        reveal_metadata: bool = False,
        enable_repair: bool = False,
        reveal_test_output: bool = False,
    ) -> None:
        self.command_template = command_template
        self.timeout_seconds = timeout_seconds
        self.reveal_metadata = reveal_metadata
        self.enable_repair = enable_repair
        self.reveal_test_output = reveal_test_output

    def safe_config(self) -> dict[str, object]:
        return {
            "command_template": redact_secrets(self.command_template),
            "timeout_seconds": self.timeout_seconds,
            "reveal_metadata": self.reveal_metadata,
            "enable_repair": self.enable_repair,
            "reveal_test_output": self.reveal_test_output,
        }

    def review(self, context: CaseContext) -> ReviewerResponse:
        started = time.perf_counter()
        temp_dir = Path(tempfile.mkdtemp(prefix="arena-custom-command-"))
        try:
            workspace = temp_dir / "workspace"
            workspace.mkdir()
            case_json = temp_dir / "case.json"
            diff_file = temp_dir / "pr.diff"
            case_json.write_text(
                json.dumps(
                    serialize_reviewer_case(
                        context,
                        reveal_metadata=self.reveal_metadata,
                        reveal_test_output=self.reveal_test_output,
                    ),
                    indent=2,
                ),
                encoding="utf-8",
            )
            diff_file.write_text(context.diff, encoding="utf-8")
            for relative_path, contents in context.relevant_files.items():
                target = workspace / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(contents, encoding="utf-8")
            args = expand_command_template(
                self.command_template,
                case_json=case_json,
                diff_file=diff_file,
                case_id=context.case.id,
                workspace=workspace,
            )
            # Resolve wrapper paths against the caller's directory BEFORE moving
            # the child out of it, so `python scripts/my_reviewer.py` keeps working
            # while the process itself no longer starts inside the repository.
            args = _absolutize(args)
            # Through the supervisor: process-tree cleanup, a byte-bounded output
            # cap, and the Windows-fail-closed boundary, not a bare subprocess.run.
            try:
                with sandboxed_home_env() as env:
                    completed = run_supervised(
                        args,
                        # Start in the payload directory, not the repository. A
                        # reviewer has no legitimate need for the pack on disk --
                        # everything it may see is already in case.json -- and the
                        # repo cwd put reference.patch, the hidden tests and the
                        # ground truth one relative path away.
                        cwd=temp_dir,
                        # Allowlisted env, like fixture commands. Previously the
                        # child inherited the full host environment, so a
                        # third-party wrapper was handed every API key and
                        # credential in the operator's shell. Name what a wrapper
                        # legitimately needs in ARENA_PASSTHROUGH_ENV.
                        env=env,
                        timeout=self.timeout_seconds,
                        # Share the centralized reviewer-output byte cap with the HTTP reviewer.
                        output_limit=limits.RAW_RESPONSE_BYTES,
                    )
            except ExecutionError as exc:
                return _invalid_response(
                    json.dumps({"error": str(exc)}),
                    f"reviewer process error: {type(exc).__name__}",
                    _elapsed_ms(started),
                )
            if completed.timed_out:
                raw = json.dumps({"error": f"command timed out after {self.timeout_seconds}s"})
                return _invalid_response(raw, "reviewer timed out", _elapsed_ms(started))
            if completed.output_limit_exceeded:
                # Flooded past the cap and reaped, so the captured output is truncated.
                # A valid JSON prefix followed by a flood must NOT pass: report a stable
                # too-large error and never parse the truncated output.
                raw = json.dumps({"error": "reviewer_output_too_large"})
                return _invalid_response(raw, "reviewer_output_too_large", _elapsed_ms(started))
            if completed.returncode != 0:
                # A process failure is invalid regardless of any valid-looking stdout;
                # nonzero exit and stderr are not hidden by a parseable prefix.
                notes = f"command exited with code {completed.returncode}"
                stderr_tail = completed.stderr.strip()[-500:]
                if stderr_tail:
                    notes = f"{notes}; {stderr_tail}"
                raw = completed.stdout.strip() or completed.stderr.strip() or notes
                return _invalid_response(raw, notes, _elapsed_ms(started))
            raw = completed.stdout.strip()
            if not raw:
                return _invalid_response(
                    json.dumps({"error": "no output"}), "no reviewer output", _elapsed_ms(started)
                )
            outcome = parse_reviewer_output(
                raw,
                enable_repair=self.enable_repair,
                known_paths=known_paths_from_context(context),
            )
            return response_from_outcome(outcome, raw=raw, latency_ms=_elapsed_ms(started))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
