"""Containerized reviewer: hardened docker-command reviewer, plus live gating."""

import base64
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from arena.benchmark.case_loader import build_context, load_cases
from arena.core.errors import ReviewerError
from arena.core.registry import create_reviewer
from arena.reviewers.docker_command import (
    CONTAINER_PAYLOAD_ROOT,
    DockerCommandReviewer,
    expand_container_command,
)


@pytest.fixture
def audit_benchmark_dir() -> Path:
    return Path("benchmark_sets/audit_v1")


def test_expand_container_command_parses_template_quoting_before_substitution():
    args = expand_container_command(
        'python "my reviewer.py" --case "{case_json}" --workspace={workspace} {case_id}',
        case_id="example",
    )
    assert args == [
        "python",
        "my reviewer.py",
        "--case",
        f"{CONTAINER_PAYLOAD_ROOT}/case.json",
        f"--workspace={CONTAINER_PAYLOAD_ROOT}/workspace",
        "example",
    ]


def test_expand_container_command_leaves_unknown_placeholders_literal():
    args = expand_container_command("python run.py --id={unknown} --id={case_id}", case_id="c1")
    assert args == ["python", "run.py", "--id={unknown}", "--id=c1"]


def test_registry_requires_command_and_image():
    with pytest.raises(ReviewerError, match="--command is required"):
        create_reviewer("docker-command", docker_image="python:3.12-slim")
    with pytest.raises(ReviewerError, match="--docker-image is required"):
        create_reviewer("docker-command", command="python x.py")
    reviewer = create_reviewer("docker-command", command="python x.py", docker_image="img:1")
    assert isinstance(reviewer, DockerCommandReviewer)
    assert reviewer.oracle_reachable is False


def test_safe_config_records_image_and_redacts_secrets():
    reviewer = DockerCommandReviewer("python x.py --api-token=supersecret9", "img:1")
    config = reviewer.safe_config()
    assert config["docker_image"] == "img:1"
    assert "supersecret9" not in str(config["command_template"])


def test_docker_command_is_gated_on_the_api_server():
    # The reviewer spawns a caller-supplied container command: the same
    # server-side class as custom-command, behind the same opt-in.
    from arena.server.auth import reviewer_needs_execution_optin

    assert reviewer_needs_execution_optin("docker-command", command="python x.py") is True


def test_reviewer_fails_closed_without_a_docker_daemon(monkeypatch, audit_benchmark_dir):
    from arena.reviewers import docker_command as module

    monkeypatch.setattr(module.TestExecutor, "_docker_available", staticmethod(lambda: False))
    case = load_cases(audit_benchmark_dir)[0]
    reviewer = DockerCommandReviewer("python x.py", "python:3.12-slim", timeout_seconds=10)
    response = reviewer.review(build_context(case))
    assert response.invalid_output is True
    assert json.loads(response.raw_response)["error"] == "docker_unavailable"


def test_reviewer_fails_closed_when_the_image_is_absent(monkeypatch, audit_benchmark_dir):
    from arena.reviewers import docker_command as module

    monkeypatch.setattr(module.TestExecutor, "_docker_available", staticmethod(lambda: True))
    monkeypatch.setattr(module.TestExecutor, "_image_present", staticmethod(lambda image: False))
    case = load_cases(audit_benchmark_dir)[0]
    reviewer = DockerCommandReviewer("python x.py", "arena-absent:1", timeout_seconds=10)
    response = reviewer.review(build_context(case))
    assert response.invalid_output is True
    assert json.loads(response.raw_response)["error"] == "docker_image_not_present"


def test_reviewer_fails_closed_when_the_docker_cli_cannot_run(monkeypatch, audit_benchmark_dir):
    from arena.core.errors import ExecutionError
    from arena.reviewers import docker_command as module

    monkeypatch.setattr(module.TestExecutor, "_docker_available", staticmethod(lambda: True))
    monkeypatch.setattr(module.TestExecutor, "_image_present", staticmethod(lambda image: True))

    def raise_execution_error(*args, **kwargs):
        raise ExecutionError("docker binary missing")

    monkeypatch.setattr(module, "run_supervised", raise_execution_error)
    case = load_cases(audit_benchmark_dir)[0]
    reviewer = DockerCommandReviewer("python x.py", "img:1", timeout_seconds=10)
    response = reviewer.review(build_context(case))
    assert response.invalid_output is True
    assert "reviewer container error" in response.parse_error_summary


def test_review_runs_inside_the_hardened_executor_invocation(monkeypatch, audit_benchmark_dir):
    from arena.execution.process import SupervisedResult
    from arena.reviewers import docker_command as module

    captured: dict[str, object] = {}

    def fake_run_supervised(args, **kwargs):
        captured["args"] = list(args)
        payload = '{"findings": [], "overall_risk": "none", "review_summary": "ok"}'
        return SupervisedResult(returncode=0, stdout=payload, stderr="", timed_out=False)

    monkeypatch.setattr(module.TestExecutor, "_docker_available", staticmethod(lambda: True))
    monkeypatch.setattr(module.TestExecutor, "_image_present", staticmethod(lambda image: True))
    monkeypatch.setattr(module, "run_supervised", fake_run_supervised)
    case = load_cases(audit_benchmark_dir)[0]
    reviewer = DockerCommandReviewer("python reviewer.py {case_id}", "img:1", timeout_seconds=10)

    response = reviewer.review(build_context(case))

    assert response.invalid_output is False
    joined = " ".join(captured["args"])  # type: ignore[arg-type]
    for flag in (
        "--network none",
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
        "--read-only",
        "--pull never",
    ):
        assert flag in joined, flag
    assert ":/workspace:ro" in joined
    # The command ends with the image and the container-side argv, and the
    # placeholder carried the case's real id (never a host path).
    assert joined.endswith(f"img:1 python reviewer.py {case.id}")


def test_workspace_mount_is_read_only_for_reviewer_payloads(tmp_path):
    from arena.execution.test_executor import TestExecutionRequest, TestExecutor

    request = TestExecutionRequest(
        case_id="c",
        workspace_path=tmp_path,
        test_command="python -c pass",
        timeout_seconds=30,
        docker_image="img:1",
        readonly_workspace=True,
    )
    args = TestExecutor._docker_args(request, ["python", "-c", "pass"], container_name="arena-x")
    assert f"{tmp_path.resolve()}:/workspace:ro" in args


# ---------------------------------------------------------------------------
# Live Docker verification (gated like the rest of the Docker suite).
# ---------------------------------------------------------------------------


def _review_context(audit_benchmark_dir):
    return build_context(load_cases(audit_benchmark_dir)[0])


def test_review_records_resolved_image_digest(audit_benchmark_dir, monkeypatch):
    """A successful container run records the digest the tag resolved to."""
    from arena.reviewers import docker_command as module

    reviewer = DockerCommandReviewer("python x.py", "img:1")
    assert reviewer.safe_config()["docker_image_digest"] is None
    monkeypatch.setattr(module.TestExecutor, "_docker_available", staticmethod(lambda: True))
    monkeypatch.setattr(module.TestExecutor, "_image_present", staticmethod(lambda image: True))
    digest = "img@sha256:" + "a" * 64
    seen: list[str] = []
    monkeypatch.setattr(
        module.TestExecutor,
        "_image_digest",
        staticmethod(lambda image: seen.append(image) or digest),
    )
    result = SimpleNamespace(
        returncode=0,
        stdout=json.dumps(
            {"findings": [], "proposed_patch": None, "overall_risk": "low", "review_summary": "ok"}
        ),
        stderr="",
        timed_out=False,
        output_limit_exceeded=False,
    )
    monkeypatch.setattr(module, "run_supervised", lambda *a, **k: result)
    response = reviewer.review(_review_context(audit_benchmark_dir))
    assert response.parse_status == "exact"
    assert seen == ["img:1"]
    assert reviewer.safe_config()["docker_image_digest"] == digest


def test_failed_review_leaves_digest_unresolved(audit_benchmark_dir, monkeypatch):
    """Fail-closed paths never probe or record a digest."""
    from arena.reviewers import docker_command as module

    reviewer = DockerCommandReviewer("python x.py", "img:1")
    probed = Mock()
    monkeypatch.setattr(module.TestExecutor, "_image_digest", probed)
    monkeypatch.setattr(module.TestExecutor, "_docker_available", staticmethod(lambda: True))
    monkeypatch.setattr(module.TestExecutor, "_image_present", staticmethod(lambda image: False))
    assert reviewer.review(_review_context(audit_benchmark_dir)).parse_status == "invalid"
    assert reviewer.safe_config()["docker_image_digest"] is None
    probed.assert_not_called()


docker_tests = pytest.mark.skipif(
    not os.getenv("ARENA_DOCKER_TESTS"),
    reason="set ARENA_DOCKER_TESTS=1 to exercise a real Docker run",
)


@docker_tests
def test_live_container_review_reads_the_blind_payload(audit_benchmark_dir):
    case = load_cases(audit_benchmark_dir)[0]
    context = build_context(case)
    # Self-contained: the container sees only the payload mount, so the command
    # cannot reference a host-side wrapper. Reading the payload proves the mount;
    # emitting valid JSON proves the output contract survives the boundary.
    code = (
        "import json\n"
        "d=json.load(open('/workspace/case.json'))\n"
        "print(json.dumps({'findings': [], 'overall_risk': 'none',"
        "'review_summary': 'blind payload: ' + d['case_id']}))\n"
    )
    b64 = base64.b64encode(code.encode()).decode()
    template = f'python -c "import base64;exec(base64.b64decode({b64!r}.encode()))"'
    reviewer = DockerCommandReviewer(template, "python:3.12-slim", timeout_seconds=120)

    response = reviewer.review(context)

    assert response.invalid_output is False, response.parse_error_summary
    assert response.parse_status == "exact"
    assert response.parsed_response is not None
    assert response.parsed_response.review_summary.startswith("blind payload: ")
