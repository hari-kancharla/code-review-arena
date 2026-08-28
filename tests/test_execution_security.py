"""Execution security: env allowlist, resource limits, pack checksums, path validation."""

import os
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from arena.benchmark.benchmark_runner import run_benchmark
from arena.benchmark.case_loader import load_cases
from arena.benchmark.pack_hash import pack_checksum, stored_checksum, write_checksum
from arena.core.config import resolve_benchmark_set
from arena.core.errors import ExecutionError
from arena.core.registry import create_reviewer
from arena.execution.hardening import harness_user_site, sandbox_env, sandboxed_home_env
from arena.execution.subprocess_runner import run_command
from arena.execution.test_executor import TestExecutionRequest, TestExecutor

AUDIT_V1 = Path("benchmark_sets/audit_v1")


def _pack_with_static_command(tmp_path: Path, marker: Path) -> Path:
    """Copy audit_v1 and add a pack-controlled static-analysis command to one case."""
    pack = tmp_path / "pack"
    shutil.copytree(AUDIT_V1, pack)
    case_id = load_cases(pack)[0].id
    case_yaml = pack / case_id / "case.yaml"
    data = yaml.safe_load(case_yaml.read_text(encoding="utf-8"))
    data.setdefault("execution", {})
    data["execution"]["run_static_analysis"] = True
    data["execution"]["static_analysis_command"] = (
        f"{sys.executable} -c \"import pathlib; pathlib.Path(r'{marker}').write_text('ran')\""
    )
    case_yaml.write_text(yaml.safe_dump(data), encoding="utf-8")
    write_checksum(pack)
    return pack


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX local execution")
def test_static_analysis_does_not_run_without_local_execution(tmp_path):
    # A pack-controlled static command is host code execution; it must not run
    # unless local execution is explicitly enabled.
    marker = tmp_path / "static_ran.txt"
    pack = _pack_with_static_command(tmp_path, marker)
    run_benchmark(
        pack,
        create_reviewer("reference-patch"),
        output_dir=tmp_path / "off",
        persist=False,
        mode="review",
        allow_local_execution=False,
    )
    assert not marker.exists()  # gated off: the command never ran
    run_benchmark(
        pack,
        create_reviewer("reference-patch"),
        output_dir=tmp_path / "on",
        persist=False,
        mode="review",
        allow_local_execution=True,
    )
    assert marker.exists()  # opted in: now it runs (proving the gate controls it)


def test_run_command_fails_closed_on_windows(monkeypatch):
    # Static analysis runs through run_command, so it must fail closed on Windows.
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(ExecutionError):
        run_command("echo hi", cwd=Path("."), timeout_seconds=5)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX bounded reader")
def test_run_command_bounds_output(tmp_path):
    result = run_command(
        f"{sys.executable} -c \"print('x' * 2000000)\"", cwd=tmp_path, timeout_seconds=15
    )
    assert len(result.output.encode("utf-8")) <= 512_000 + 65536  # cap + one read chunk


def test_sandbox_env_does_not_inherit_parent_environment(monkeypatch):
    monkeypatch.setenv("SUPER_SECRET_API_KEY", "leak-me")
    env = sandbox_env()
    assert "SUPER_SECRET_API_KEY" not in env
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["PYTHONNOUSERSITE"] == "1"
    assert "PATH" in env


def test_sandbox_env_does_not_forward_parent_pythonpath(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/tmp/attacker-path")
    env = sandbox_env()
    pythonpath = env.get("PYTHONPATH", "")
    assert "/tmp/attacker-path" not in pythonpath.split(os.pathsep)
    user_site = harness_user_site()
    if user_site:
        assert user_site in pythonpath.split(os.pathsep)


def test_sandbox_env_keeps_harness_user_site_importable():
    user_site = harness_user_site()
    if not user_site:
        pytest.skip("interpreter is not using a user-site directory")
    env = sandbox_env()
    assert user_site in env.get("PYTHONPATH", "").split(os.pathsep)
    assert env["PYTHONNOUSERSITE"] == "1"


def test_sandbox_env_passthrough_is_explicit(monkeypatch):
    monkeypatch.setenv("SUPER_SECRET_API_KEY", "leak-me")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:3128")
    monkeypatch.setenv("ARENA_PASSTHROUGH_ENV", "HTTPS_PROXY")
    env = sandbox_env()
    assert env["HTTPS_PROXY"] == "http://proxy:3128"
    assert "SUPER_SECRET_API_KEY" not in env


def test_sandbox_env_does_not_forward_parent_home(monkeypatch):
    monkeypatch.setenv("HOME", "/Users/secret")
    # HOME must be supplied as an isolated dir, never inherited from the host.
    assert "HOME" not in sandbox_env()


def test_sandboxed_home_env_is_isolated_and_cleaned_up():
    real_home = os.path.expanduser("~")
    with sandboxed_home_env() as env:
        home = env["HOME"]
        assert home != real_home
        assert "arena-home-" in home
        assert os.path.isdir(home)
        assert env["TMPDIR"] == home
    assert not os.path.exists(home)  # removed when the command finishes


def test_fixture_runs_with_isolated_home(tmp_path):
    seen = tmp_path / "seen_home.txt"
    script = f"import os; open(r'{seen}', 'w').write(os.environ.get('HOME', 'MISSING'))"
    TestExecutor().execute(
        TestExecutionRequest(
            case_id="case",
            workspace_path=tmp_path,
            test_command=[sys.executable, "-c", script],
            timeout_seconds=10,
            allow_local_execution=True,
        )
    )
    home_seen = seen.read_text()
    assert home_seen != os.path.expanduser("~")
    assert "arena-home-" in home_seen


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX local execution")
def test_sandboxed_pytest_is_importable(tmp_path):
    # Isolated HOME must not hide a --user install of pytest: pin_interpreter
    # rewrites ``pytest`` to ``sys.executable -m pytest``, which needs the
    # module on the child's sys.path.
    result = TestExecutor().execute(
        TestExecutionRequest(
            case_id="case",
            workspace_path=tmp_path,
            test_command=[sys.executable, "-c", "import pytest; print(pytest.__file__)"],
            timeout_seconds=10,
            allow_local_execution=True,
        )
    )
    assert result.ran is True
    assert result.passed is True
    assert "pytest" in result.stdout


def test_fixture_commands_cannot_read_host_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPER_SECRET_API_KEY", "leak-me")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = TestExecutor().execute(
        TestExecutionRequest(
            case_id="case",
            workspace_path=workspace,
            test_command=[
                sys.executable,
                "-c",
                "import os; print(os.environ.get('SUPER_SECRET_API_KEY', 'BLOCKED'))",
            ],
            timeout_seconds=10,
            allow_local_execution=True,
        )
    )
    assert result.ran is True
    assert "BLOCKED" in result.stdout
    assert "leak-me" not in result.stdout


def test_file_size_rlimit_stops_disk_filling(tmp_path, monkeypatch):
    if sys.platform == "win32":
        return
    monkeypatch.setenv("ARENA_RLIMIT_FSIZE_BYTES", str(64 * 1024))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = TestExecutor().execute(
        TestExecutionRequest(
            case_id="case",
            workspace_path=workspace,
            test_command=[
                sys.executable,
                "-c",
                "open('big.bin','wb').write(b'x' * (1024 * 1024)); print('WROTE')",
            ],
            timeout_seconds=10,
            allow_local_execution=True,
        )
    )
    assert result.ran is True
    assert result.passed is False
    assert "WROTE" not in result.stdout


def _make_pack(tmp_path: Path) -> Path:
    pack = tmp_path / "pack"
    (pack / "case_a").mkdir(parents=True)
    (pack / "manifest.yaml").write_text("version: x\nname: x\ncases: [case_a]\n", "utf-8")
    (pack / "case_a" / "case.yaml").write_text("id: case_a\n", "utf-8")
    return pack


def test_pack_checksum_pins_content(tmp_path):
    pack = _make_pack(tmp_path)
    first = pack_checksum(pack)
    assert first == pack_checksum(pack)
    (pack / "case_a" / "case.yaml").write_text("id: case_a_tampered\n", "utf-8")
    assert pack_checksum(pack) != first


def test_pack_checksum_covers_hidden_files_but_not_the_root_artifact(tmp_path):
    pack = _make_pack(tmp_path)
    baseline = pack_checksum(pack)
    checksum = write_checksum(pack)
    # Writing the root pack.sha256 does not change the digest (it excludes itself).
    assert checksum == baseline
    assert pack_checksum(pack) == baseline
    assert stored_checksum(pack) == checksum
    # Phase 1C: a hidden regular file is COVERED by the digest, so it changes it.
    (pack / ".DS_Store").write_bytes(b"junk")
    assert pack_checksum(pack) != baseline


def test_resolve_benchmark_set_rejects_path_tricks(monkeypatch, tmp_path):
    root = tmp_path / "packs"
    (root / "v1").mkdir(parents=True)
    monkeypatch.setenv("ARENA_BENCHMARK_ROOT", str(root))
    assert resolve_benchmark_set("v1") == root / "v1"
    assert resolve_benchmark_set("../packs/v1") is None
    assert resolve_benchmark_set("a/b") is None
    assert resolve_benchmark_set("..") is None
    assert resolve_benchmark_set("") is None
    assert resolve_benchmark_set("missing") is None
