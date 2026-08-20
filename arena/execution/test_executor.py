"""Safely execute post-patch tests inside an isolated workspace."""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import ClassVar, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from arena.core.errors import ValidationError
from arena.execution.commands import (
    parse_test_commands,
    pin_container_interpreter,
    pin_interpreter,
)
from arena.execution.hardening import resource_limiter, sandboxed_home_env
from arena.execution.process import run_supervised
from arena.execution.test_evidence import (
    CONTAINER_EVIDENCE_DIR,
    is_pytest_command,
    read_report,
    report_filename,
    with_report_flag,
)

# Cap captured stdout/stderr so a noisy or malicious fixture cannot exhaust memory.
OUTPUT_LIMIT_BYTES = 512_000
# Bound every docker CLI query. These run outside run_supervised, so nothing else
# can preempt them: an unresponsive daemon would hang the whole run. Matches the
# timeout already used when force-removing a container.
DOCKER_PROBE_TIMEOUT_SECONDS = 15


def _missing_runner(commands: list[list[str]]) -> str | None:
    """Name of a `-m` test-runner module this interpreter cannot import.

    Local execution pins `pytest`/`python` to the harness's own interpreter, so
    importability here is exactly what the run will see. Returns None when every
    command's runner is available (or when the command is not a `-m` invocation).
    """
    for argv in commands:
        pinned = pin_interpreter(argv)
        if len(pinned) >= 3 and pinned[0] == sys.executable and pinned[1] == "-m":
            module = pinned[2]
            try:
                found = importlib.util.find_spec(module)
            except (ImportError, ValueError):
                # find_spec returns None only for a missing TOP-LEVEL name; for a
                # dotted runner whose parent package is absent it raises
                # ModuleNotFoundError, and ImportError/ValueError for a malformed
                # name. Unguarded, the helper whose whole job is to turn "cannot
                # import the runner" into a clean skip threw out of execute()
                # instead -- charging the reviewer with a hard error, and aborting
                # certify-pack and mutation runs, which do not wrap the call.
                return module
            if found is None:
                return module
    return None


class TestExecutionRequest(BaseModel):
    __test__: ClassVar[bool] = False

    case_id: str
    workspace_path: Path
    test_command: str | list[str] | list[list[str]]
    timeout_seconds: int = Field(ge=1)
    docker_image: str | None = None
    allow_local_execution: bool = False
    # Workspace-relative paths (the hidden tests/oracle) to bind read-only inside
    # the container, so candidate code cannot rewrite them mid-run. Docker only;
    # local execution still relies on tamper detection.
    readonly_paths: list[str] = Field(default_factory=list)


class TestExecutionResult(BaseModel):
    __test__: ClassVar[bool] = False

    case_id: str
    ran: bool
    passed: bool
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    timed_out: bool = False
    execution_mode: Literal["docker", "local", "skipped"]
    error: str | None = None
    # Resolved image digest (docker mode only) for reproducibility.
    image_digest: str | None = None


class TestExecutor:
    def execute(self, request: TestExecutionRequest) -> TestExecutionResult:
        if not request.workspace_path.is_dir():
            return self._skipped(request.case_id, "workspace_not_found")
        try:
            commands = parse_test_commands(request.test_command)
        except ValidationError as exc:
            return self._skipped(request.case_id, f"invalid_test_command: {exc}")
        if not commands:
            return self._skipped(request.case_id, "empty_test_command")
        # Docker is the standard backend. There is no silent Docker->local
        # fallback: a case that declares an image must run in Docker or be
        # skipped. Trusted-local is only for image-less cases that explicitly
        # opt in via allow_local_execution.
        if request.docker_image:
            if not self._docker_available():
                return self._skipped(request.case_id, "docker_required_but_unavailable")
            # Never let `docker run` reach the network to pull a missing image:
            # the image name comes from the (untrusted) pack, and an implicit pull
            # would run unvetted code. The image must already be present locally.
            if not self._image_present(request.docker_image):
                return self._skipped(request.case_id, "docker_image_not_present")
            mode: Literal["docker", "local"] = "docker"
        elif request.allow_local_execution:
            mode = "local"
            # A local run is pinned to this interpreter, so a runner it cannot
            # import is an unavailable backend, not a failing test suite. Without
            # this the missing module surfaced as an ordinary non-zero exit and
            # every case scored tests_failed -- an install without the test runner
            # (the built wheel, the shipped Dockerfile) silently reported 0%
            # validated for known-good reference patches instead of an invalid run.
            missing = _missing_runner(commands)
            if missing is not None:
                return self._skipped(request.case_id, f"test_runner_unavailable:{missing}")
        else:
            return self._skipped(request.case_id, "local_execution_disabled")

        # Evidence lives outside the workspace, so a patch cannot simply overwrite
        # it, and is removed with the run.
        evidence_dir = Path(tempfile.mkdtemp(prefix="arena-evidence-"))
        nonce = uuid4().hex[:12]
        try:
            results: list[TestExecutionResult] = []
            # One budget for the whole case, spent down command by command.
            remaining = float(request.timeout_seconds)
            for index, argv in enumerate(commands):
                command_started = time.perf_counter()
                report_name = report_filename(request.case_id, nonce, index)
                host_report = evidence_dir / report_name
                expects_report = is_pytest_command(pin_interpreter(argv))
                if mode == "docker":
                    container = self._container_name(request.case_id)
                    container_report = CONTAINER_EVIDENCE_DIR / report_name
                    args = self._docker_args(
                        request,
                        argv,
                        container_name=container,
                        evidence_dir=evidence_dir,
                        report_path=str(container_report) if expects_report else None,
                    )
                else:
                    container = None
                    args = pin_interpreter(argv)
                    if expects_report:
                        args = with_report_flag(args, str(host_report))
                result = self._run(
                    request,
                    args,
                    mode,
                    container_name=container,
                    timeout_seconds=max(1, int(remaining)),
                )
                remaining -= time.perf_counter() - command_started
                if expects_report:
                    result = self._require_evidence(result, host_report)
                results.append(result)
                if not result.passed:
                    break
            combined = self._combined(request.case_id, results, mode)
        finally:
            shutil.rmtree(evidence_dir, ignore_errors=True)
        if mode == "docker":
            return combined.model_copy(
                update={"image_digest": self._image_digest(request.docker_image)}
            )
        return combined

    @staticmethod
    def _require_evidence(result: TestExecutionResult, report_path: Path) -> TestExecutionResult:
        """Downgrade a "pass" that produced no machine-readable proof.

        Exit code 0 alone is not evidence: the suite runs candidate-controlled
        code, which can terminate the interpreter with status 0 before any test
        executes. A pass therefore additionally requires a JUnit report showing
        at least one test and no failures or errors.
        """
        if not result.passed or result.timed_out:
            return result
        report = read_report(report_path)
        if report is None:
            return result.model_copy(update={"passed": False, "error": "no_test_evidence"})
        if not report.ran_and_passed:
            return result.model_copy(update={"passed": False, "error": "test_evidence_not_passing"})
        return result

    @staticmethod
    def _combined(
        case_id: str, results: list[TestExecutionResult], mode: Literal["docker", "local"]
    ) -> TestExecutionResult:
        last = results[-1]
        if len(results) == 1:
            return last
        return TestExecutionResult(
            case_id=case_id,
            ran=True,
            passed=all(result.passed for result in results),
            exit_code=last.exit_code,
            stdout="\n".join(filter(None, (result.stdout for result in results))),
            stderr="\n".join(filter(None, (result.stderr for result in results))),
            duration_ms=sum(result.duration_ms for result in results),
            timed_out=any(result.timed_out for result in results),
            execution_mode=mode,
            error=last.error,
        )

    @staticmethod
    def _probe_docker(args: list[str]) -> subprocess.CompletedProcess[str] | None:
        """Run a short docker CLI query, or return None if it did not answer.

        Every probe is bounded: an unresponsive daemon (Docker Desktop
        mid-restart, a stalled DOCKER_HOST=ssh://... or tcp:// connection) would
        otherwise block the call forever. subprocess.run cannot be preempted by
        the per-case timeout or the run's --max-wall-seconds deadline, so an
        unbounded probe hangs the entire run instead of failing closed.
        """
        try:
            return subprocess.run(
                args,
                capture_output=True,
                text=True,
                check=False,
                timeout=DOCKER_PROBE_TIMEOUT_SECONDS,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None

    @staticmethod
    def _docker_available() -> bool:
        if shutil.which("docker") is None:
            return False
        result = TestExecutor._probe_docker(
            ["docker", "version", "--format", "{{.Server.Version}}"]
        )
        # A probe that never answered is treated as "docker unavailable", so the
        # case reports docker_required_but_unavailable and the run fails closed.
        return result is not None and result.returncode == 0

    @staticmethod
    def _normalize_image_ref(image: str) -> str | None:
        """The exact reference `docker run` will resolve, or None if unusable.

        `docker image ls -q` filters by repository, so a bare `arena-bench`
        matched an existing `arena-bench:1` and passed the presence check -- then
        `docker run --pull never arena-bench` resolved `arena-bench:latest`, which
        does not exist, and the resulting exit 125 was scored as an ordinary
        failing suite rather than a missing image. Making the tag explicit means
        the probe asks about the same thing that will actually run. Glob
        metacharacters are rejected outright: they are filter syntax, never a
        real reference.
        """
        if not image or any(character in image for character in "*?["):
            return None
        last = image.rsplit("/", 1)[-1]
        if ":" in last or "@" in last:
            return image
        return f"{image}:latest"

    @staticmethod
    def _image_present(image: str) -> bool:
        # `docker image ls -q <ref>` lists the id when the image is present and
        # nothing when it is not. Unlike `docker image inspect`, it is reliable
        # under both the classic and the containerd image stores.
        reference = TestExecutor._normalize_image_ref(image)
        if reference is None:
            return False
        result = TestExecutor._probe_docker(["docker", "image", "ls", "-q", reference])
        return result is not None and result.returncode == 0 and bool(result.stdout.strip())

    @staticmethod
    def _container_name(case_id: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9_.-]", "-", case_id)[:40]
        return f"arena-{slug}-{uuid4().hex[:8]}"

    @staticmethod
    def _docker_args(
        request: TestExecutionRequest,
        args: list[str],
        *,
        container_name: str,
        evidence_dir: Path | None = None,
        report_path: str | None = None,
    ) -> list[str]:
        assert request.docker_image is not None
        memory = os.getenv("ARENA_DOCKER_MEMORY", "2g")
        cpus = os.getenv("ARENA_DOCKER_CPUS", "2")
        pids = os.getenv("ARENA_DOCKER_PIDS", "256")
        tmpfs_size = os.getenv("ARENA_DOCKER_TMPFS_SIZE", "256m")
        command = [
            "docker",
            "run",
            "--rm",
            # Presence is checked before we get here; refuse any implicit pull.
            "--pull",
            "never",
            "--name",
            container_name,
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            pids,
            "--memory",
            memory,
            "--cpus",
            cpus,
            "--read-only",
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,size={tmpfs_size}",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            "-e",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
            "-e",
            "HOME=/tmp",
            "-v",
            f"{request.workspace_path.resolve()}:/workspace",
            "-w",
            "/workspace",
        ]
        # Re-mount the hidden tests/oracle read-only on top of the writable
        # workspace so patched code cannot rewrite them to force a pass. The
        # paths are populated on the host before the container starts.
        workspace_root = request.workspace_path.resolve()
        for relative in request.readonly_paths:
            target = PurePosixPath("/workspace") / PurePosixPath(relative)
            source = (workspace_root / relative).resolve()
            if source.exists():
                command += ["-v", f"{source}:{target}:ro"]
        # The evidence directory is the only writable mount besides the workspace.
        # It sits outside /workspace so the report cannot be clobbered by the
        # patched tree, and it is a fresh host temp dir per execution.
        if evidence_dir is not None:
            command += ["-v", f"{evidence_dir.resolve()}:{CONTAINER_EVIDENCE_DIR}"]
        if sys.platform != "win32":
            # Run as the host user so the bind-mounted workspace stays writable
            # while the container process is non-root.
            command += ["--user", f"{os.getuid()}:{os.getgid()}"]
        container_argv = pin_container_interpreter(args)
        if report_path is not None:
            container_argv = with_report_flag(container_argv, report_path)
        command += [request.docker_image, *container_argv]
        return command

    @staticmethod
    def _image_digest(image: str | None) -> str | None:
        if not image:
            return None
        result = TestExecutor._probe_docker(
            ["docker", "image", "inspect", image, "--format", "{{index .RepoDigests 0}}"]
        )
        if result is None:
            return None
        digest = result.stdout.strip()
        return digest or None

    @staticmethod
    def _force_remove_container(name: str) -> None:
        # docker run leaves the container running when its CLI is killed, so on
        # timeout we remove it by name; best-effort.
        subprocess.run(
            ["docker", "rm", "-f", name],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )

    def _run(
        self,
        request: TestExecutionRequest,
        args: list[str],
        mode: Literal["docker", "local"],
        *,
        container_name: str | None = None,
        timeout_seconds: int | None = None,
    ) -> TestExecutionResult:
        """Run one command, bounded by what is left of the case's budget.

        ``timeout_seconds`` is the remaining budget, not the case total. A pack
        may declare several commands, and giving each the full ``timeout_seconds``
        let a case run for N times its declared limit -- which also defeats the
        run-level ``--max-wall-seconds`` deadline that clamps that limit.
        """
        budget = request.timeout_seconds if timeout_seconds is None else timeout_seconds
        started = time.perf_counter()
        # run_supervised starts the child in its own session and kills the whole
        # process tree on timeout, so descendants cannot outlive the case.
        if mode == "local":
            # Untrusted fixtures: allowlisted env, isolated HOME/TMPDIR, bounded
            # resources, and no autoloaded pytest plugins (a candidate cannot
            # inject one via the workspace).
            with sandboxed_home_env(extra={"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}) as env:
                result = run_supervised(
                    args,
                    cwd=request.workspace_path,
                    env=env,
                    timeout=budget,
                    preexec_fn=resource_limiter(budget),
                    output_limit=OUTPUT_LIMIT_BYTES,
                )
        else:
            # The docker CLI needs the caller's docker config; isolation comes
            # from the container itself.
            try:
                result = run_supervised(
                    args,
                    cwd=request.workspace_path,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                    timeout=budget,
                    preexec_fn=None,
                    output_limit=OUTPUT_LIMIT_BYTES,
                )
            except BaseException:
                # Ctrl-C or any error while the container is running: killing the
                # docker CLI does not stop the container it started, so without
                # this the container keeps its CPU and memory allotment after the
                # harness has exited.
                if container_name:
                    self._force_remove_container(container_name)
                raise
            # Remove the container whenever the group was killed, not only on
            # timeout: the output cap kills the docker CLI the same way, and a
            # container that ignores the proxied SIGTERM would otherwise keep
            # running with its CPU and memory allotment after the case moves on.
            if (result.timed_out or result.output_limit_exceeded) and container_name:
                self._force_remove_container(container_name)
        duration_ms = int((time.perf_counter() - started) * 1000)
        if result.timed_out:
            return TestExecutionResult(
                case_id=request.case_id,
                ran=True,
                passed=False,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_ms=duration_ms,
                timed_out=True,
                execution_mode=mode,
                error="test_execution_timed_out",
            )
        return TestExecutionResult(
            case_id=request.case_id,
            ran=True,
            passed=result.returncode == 0,
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=duration_ms,
            execution_mode=mode,
            # The harness killed the process for flooding past the output cap, so
            # its non-zero exit is Arena's doing, not a verdict from the suite.
            # Recording it keeps a truncated run distinguishable from a genuine
            # test failure instead of silently scoring it as tests_failed.
            error="test_output_too_large" if result.output_limit_exceeded else None,
        )

    @staticmethod
    def _skipped(case_id: str, error: str) -> TestExecutionResult:
        return TestExecutionResult(
            case_id=case_id,
            ran=False,
            passed=False,
            execution_mode="skipped",
            error=error,
        )
