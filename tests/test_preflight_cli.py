import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from arena.cli.commands import preflight as diagnostic
from arena.cli.main import app
from arena.core.errors import ValidationError


@pytest.fixture
def setup(monkeypatch):
    execution = SimpleNamespace(run_tests=True, test_command="pytest -q", docker_image=None)
    snapshot = SimpleNamespace(
        checksum="a" * 64,
        stored_checksum=None,
        load_and_validate=lambda: [SimpleNamespace(execution=execution)],
    )

    @contextmanager
    def capture(path):
        yield snapshot

    monkeypatch.setattr(diagnostic, "snapshot_pack", capture)
    daemon = Mock(return_value=True)
    image = Mock(return_value=True)
    monkeypatch.setattr(diagnostic.TestExecutor, "_docker_available", daemon)
    monkeypatch.setattr(diagnostic.TestExecutor, "_image_present", image)
    monkeypatch.delenv("ARENA_TRUSTED_PACK_HASHES", raising=False)
    return execution, snapshot, daemon, image


def invoke(*args):
    result = CliRunner().invoke(app, ["preflight", "--json", *args])
    return result, json.loads(result.stdout)


def test_full_local_requires_opt_in_in_json_and_text(setup):
    result, data = invoke()
    assert result.exit_code == 1
    assert data["ok"] is False
    assert any(c["name"] == "local-execution" and c["status"] == "fail" for c in data["checks"])
    text = CliRunner().invoke(app, ["preflight"])
    assert text.exit_code == 1
    assert "local-execution" in text.stdout


def test_review_mode_does_not_probe_test_backend(setup):
    _, _, daemon, image = setup
    result, data = invoke("--mode", "review")
    assert result.exit_code == 0, result.output
    assert data["ok"]
    daemon.assert_not_called()
    image.assert_not_called()
    assert not any(c["name"] == "pytest" for c in data["checks"])


def test_local_opt_in_and_missing_pytest(setup, monkeypatch):
    result, data = invoke("--allow-local-execution")
    assert result.exit_code == 0
    assert data["ok"]
    monkeypatch.setattr(diagnostic.importlib.util, "find_spec", lambda name: None)
    result, data = invoke("--allow-local-execution")
    assert result.exit_code == 1
    assert any(c["name"] == "pytest" and c["status"] == "fail" for c in data["checks"])


def test_trusted_hash_admission(setup, monkeypatch):
    monkeypatch.setenv("ARENA_TRUSTED_PACK_HASHES", "b" * 64)
    assert invoke("--allow-local-execution")[0].exit_code == 1
    monkeypatch.setenv("ARENA_TRUSTED_PACK_HASHES", "a" * 64)
    assert invoke("--allow-local-execution")[0].exit_code == 0


def test_required_images_deduplicated_and_no_local_fallback(setup):
    execution, _, daemon, image = setup
    execution.docker_image = "example:local"
    args = (
        "--reviewer",
        "docker-command",
        "--command",
        "python /review.py {case_json}",
        "--docker-image",
        "example:local",
        "--allow-local-execution",
    )
    assert invoke(*args)[0].exit_code == 0
    daemon.assert_called_once_with()
    image.assert_called_once_with("example:local")
    image.return_value = False
    assert invoke(*args)[0].exit_code == 1
    daemon.return_value = False
    image.reset_mock()
    assert invoke(*args)[0].exit_code == 1
    image.assert_not_called()


@pytest.mark.parametrize(
    "spec,command,image",
    [
        ("unknown", None, None),
        ("custom-command", None, None),
        ("custom-command", " ", None),
        ("custom-command", "'", None),
        ("custom-command", "'' arg", None),
        ("custom-command", "arena-no-such-executable-123", None),
        ("docker-command", "python reviewer.py", None),
        ("http:invalid", None, None),
        ("http:http://host:invalid", None, None),
    ],
)
def test_invalid_reviewer(spec, command, image):
    assert diagnostic.check_reviewer(spec, command, image)[0].status == "fail"


def test_command_never_executed_or_echoed(tmp_path):
    marker = tmp_path / "must-not-exist"
    command = f"{sys.executable} -c \"open({str(marker)!r}, 'w').close()\" --token=secret"
    checks = diagnostic.check_reviewer("custom-command", command, None)
    assert checks[0].status == "warn"
    assert not marker.exists()
    assert "secret" not in repr(checks)


def test_url_credentials_not_echoed():
    checks = diagnostic.check_reviewer("http:https://user:secret@localhost/review", None, None)
    assert checks[0].status == "warn"
    assert "secret" not in repr(checks)


@pytest.mark.parametrize(
    "args",
    [
        ("--command", "python"),
        ("--docker-image", "example:local"),
        ("--reviewer", "reference-patch", "--command", "python"),
        ("--reviewer", "reference-patch", "--docker-image", "example:local"),
    ],
)
def test_unused_options_fail(setup, args):
    assert invoke("--mode", "review", *args)[0].exit_code == 1


def test_invalid_pack_is_structured_failure(setup, monkeypatch):
    def fail(path):
        raise ValidationError("broken pack")

    monkeypatch.setattr(diagnostic, "snapshot_pack", fail)
    result, data = invoke("--mode", "review")
    assert result.exit_code == 1
    assert any(c["name"] == "pack" and c["status"] == "fail" for c in data["checks"])


def test_real_pack_and_missing_path():
    checks = diagnostic.collect_checks(Path("benchmark_sets/audit_v1"), mode="review")
    assert not any(c.status == "fail" for c in checks)
    checks = diagnostic.collect_checks(Path("/nonexistent-arena-pack-123"), mode="review")
    assert any(c.name == "pack" and c.status == "fail" for c in checks)


def test_review_mode_still_checks_reviewer_image(setup):
    execution, _, daemon, image = setup
    execution.docker_image = "tests:not-needed"
    result, _ = invoke(
        "--mode",
        "review",
        "--reviewer",
        "docker-command",
        "--command",
        "python /review.py",
        "--docker-image",
        "reviewer:local",
    )
    assert result.exit_code == 0
    daemon.assert_called_once_with()
    image.assert_called_once_with("reviewer:local")


def test_module_entrypoint():
    import os
    import subprocess

    # GitHub Actions sets GITHUB_ACTIONS, which makes Typer force colored help.
    # The color codes split option names, so check the plain-text help instead.
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"GITHUB_ACTIONS", "FORCE_COLOR", "PY_COLORS"}
    }
    env.update(NO_COLOR="1", TERM="dumb", COLUMNS="200")
    result = subprocess.run(
        [sys.executable, "-m", "arena", "preflight", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "--reviewer" in result.stdout
    assert "--json" in result.stdout
