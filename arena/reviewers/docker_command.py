"""Run an external reviewer command inside a hardened Docker container.

``custom-command`` reviews honestly, but it runs as a host process: the
filesystem -- and with it every pack's ``reference.patch``, hidden tests and
ground truth -- stays reachable, so its runs are recorded with
``oracle_reachable=True`` and are never ranked as blind measurements.

This reviewer closes that gap with an isolation boundary instead of a label.
The command runs in a container built from the SAME hardened invocation the
test executor uses: no network, all capabilities dropped, no-new-privileges,
read-only rootfs, non-root user, CPU/memory/PID limits, and no image pulls.
The reviewer receives a read-only mount of a payload directory holding exactly
the blind review surface -- ``case.json``, ``pr.diff`` and the relevant files --
and nothing else: not the repository, not the pack, not the host environment.

The boundary is real but narrow, and this is what it does NOT claim:

- The image is trusted. It must be built or vetted by the operator (the
  executor never pulls; a missing image fails closed). An image may still
  contain pre-computed answers for known case ids; pinning the image and
  reviewing its contents is the operator's responsibility, as with the
  test-sandbox image.
- The reviewer needs no host credentials; if a wrapper genuinely needs an API
  endpoint, that belongs in the image, not in a passed-through secret.

The command template keeps the ``custom-command`` placeholders, but their
values are CONTAINER paths under the mounted payload root (``/workspace``),
and any reviewer script it names must exist inside the image. Template
quoting is parsed before substitution, so paths with spaces or quotes stay
single arguments.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import tempfile
import time
from pathlib import Path, PurePosixPath

from arena.core import limits
from arena.core.errors import ExecutionError
from arena.core.models import CaseContext, ReviewerResponse
from arena.execution.process import run_supervised
from arena.execution.test_executor import TestExecutionRequest, TestExecutor
from arena.reviewers.base import BaseReviewer
from arena.reviewers.custom_command import (
    _elapsed_ms,
    _invalid_response,
    redact_secrets,
    serialize_reviewer_case,
)
from arena.reviewers.response_parser import (
    known_paths_from_context,
    parse_reviewer_output,
    response_from_outcome,
)

# Where the payload directory is mounted inside the container. The reviewer
# receives nothing else: the mount is the whole review surface.
CONTAINER_PAYLOAD_ROOT = PurePosixPath("/workspace")


def expand_container_command(template: str, *, case_id: str) -> list[str]:
    """Parse template quoting, then substitute container-side payload paths.

    Parsing happens before substitution so a value can never contribute shell
    or argument syntax; values are container paths (and the case id), never
    host paths. Placeholder-like text inside a value stays literal, and
    unknown placeholders are left unchanged.
    """
    values = {
        "case_json": str(CONTAINER_PAYLOAD_ROOT / "case.json"),
        "diff_file": str(CONTAINER_PAYLOAD_ROOT / "pr.diff"),
        "case_id": case_id,
        "workspace": str(CONTAINER_PAYLOAD_ROOT / "workspace"),
    }
    return [
        re.sub(
            r"\{(case_json|diff_file|case_id|workspace)\}",
            lambda m: values[m.group(1)],
            argument,
        )
        for argument in shlex.split(template)
    ]


class DockerCommandReviewer(BaseReviewer):
    name = "docker-command"
    model = "custom"
    # The container sees a read-only payload and nothing else: no pack tree,
    # no host filesystem, no network. This is the boundary custom-command
    # lacks, which is why those runs are recorded as oracle-reachable and
    # this one is not. It rests on the image being trusted, not on wishful
    # thinking.
    oracle_reachable = False

    def __init__(
        self,
        command_template: str,
        docker_image: str,
        timeout_seconds: int = 120,
        reveal_metadata: bool = False,
        enable_repair: bool = False,
        reveal_test_output: bool = False,
    ) -> None:
        self.command_template = command_template
        self.docker_image = docker_image
        self.timeout_seconds = timeout_seconds
        self.reveal_metadata = reveal_metadata
        self.enable_repair = enable_repair
        self.reveal_test_output = reveal_test_output

    def safe_config(self) -> dict[str, object]:
        return {
            "command_template": redact_secrets(self.command_template),
            "docker_image": self.docker_image,
            "timeout_seconds": self.timeout_seconds,
            "reveal_metadata": self.reveal_metadata,
            "enable_repair": self.enable_repair,
            "reveal_test_output": self.reveal_test_output,
        }

    def review(self, context: CaseContext) -> ReviewerResponse:
        started = time.perf_counter()
        temp_dir = Path(tempfile.mkdtemp(prefix="arena-docker-command-"))
        try:
            workspace = temp_dir / "workspace"
            workspace.mkdir()
            (temp_dir / "case.json").write_text(
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
            (temp_dir / "pr.diff").write_text(context.diff, encoding="utf-8")
            for relative_path, contents in context.relevant_files.items():
                target = workspace / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(contents, encoding="utf-8")

            # Fail closed BEFORE running anything: no daemon, or an image this
            # host does not have (the executor never pulls, and neither do we).
            if not TestExecutor._docker_available():
                return _invalid_response(
                    json.dumps({"error": "docker_unavailable"}),
                    "docker daemon unavailable",
                    _elapsed_ms(started),
                )
            if not TestExecutor._image_present(self.docker_image):
                return _invalid_response(
                    json.dumps({"error": "docker_image_not_present"}),
                    f"docker image not present: {self.docker_image}",
                    _elapsed_ms(started),
                )

            container_argv = expand_container_command(
                self.command_template, case_id=context.case.id
            )
            container_name = TestExecutor._container_name(context.case.id)
            args = TestExecutor._docker_args(
                TestExecutionRequest(
                    case_id=context.case.id,
                    workspace_path=temp_dir,
                    test_command="python -c pass",
                    timeout_seconds=self.timeout_seconds,
                    docker_image=self.docker_image,
                    # The payload is immutable evidence: the container may read
                    # it, never write it (this includes the reviewer's own
                    # process, so the review cannot alter what was reviewed).
                    readonly_workspace=True,
                ),
                container_argv,
                container_name=container_name,
            )
            try:
                # The docker CLI needs the caller's docker config; isolation
                # comes from the container itself (mirrors the test executor).
                completed = run_supervised(
                    args,
                    cwd=temp_dir,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                    timeout=self.timeout_seconds,
                    output_limit=limits.RAW_RESPONSE_BYTES,
                )
            except ExecutionError as exc:
                # Fail closed as an invalid response, never a harness crash:
                # a missing docker binary or the Windows fail-closed boundary
                # is an execution-environment problem, not a review result.
                return _invalid_response(
                    json.dumps({"error": str(exc)}),
                    f"reviewer container error: {type(exc).__name__}",
                    _elapsed_ms(started),
                )
            except BaseException:
                # Ctrl-C or any error while the container runs: the docker CLI
                # dying does not stop the container it started.
                TestExecutor._force_remove_container(container_name)
                raise
            if (completed.timed_out or completed.output_limit_exceeded) and container_name:
                # A killed CLI leaves the container running; sweep it on both
                # kill paths, exactly like the test executor.
                TestExecutor._force_remove_container(container_name)
            if completed.timed_out:
                raw = json.dumps({"error": f"command timed out after {self.timeout_seconds}s"})
                return _invalid_response(raw, "reviewer timed out", _elapsed_ms(started))
            if completed.output_limit_exceeded:
                raw = json.dumps({"error": "reviewer_output_too_large"})
                return _invalid_response(raw, "reviewer_output_too_large", _elapsed_ms(started))
            if completed.returncode != 0:
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
