import json
import subprocess
from unittest.mock import patch

import pytest

from arena.preflight import environment_facts, main


@pytest.mark.parametrize(
    ("returncode", "stdout", "available"),
    [(0, "27.0.0\n", True), (0, " \n", False), (1, "27.0.0", False)],
)
def test_docker_probe(returncode, stdout, available):
    result = subprocess.CompletedProcess([], returncode, stdout, "")
    with patch("arena.preflight.subprocess.run", return_value=result) as run:
        facts = environment_facts()
    assert facts["docker_available"] is available
    assert isinstance(facts["python_version"], str)
    run.assert_called_once_with(
        ["docker", "version", "--format", "{{.Server.Version}}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )


@pytest.mark.parametrize(
    "error",
    [FileNotFoundError(), PermissionError(), subprocess.TimeoutExpired("docker", 5)],
)
def test_unavailable_docker_does_not_crash(error):
    with patch("arena.preflight.subprocess.run", side_effect=error):
        assert environment_facts()["docker_available"] is False


def test_main_emits_json(capsys):
    facts = {"python_version": "3.11.0", "docker_available": False}
    with patch("arena.preflight.environment_facts", return_value=facts):
        main()
    captured = capsys.readouterr()
    assert json.loads(captured.out) == facts
    assert captured.err == ""
