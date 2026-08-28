"""Test-command parsing/normalization and run-level budgets."""

import sys
from pathlib import Path

import pytest

from arena.benchmark.benchmark_runner import run_benchmark
from arena.core.errors import ValidationError
from arena.core.models import CaseContext, ReviewerResponse, ReviewResult
from arena.execution.commands import (
    parse_test_commands,
    pin_container_interpreter,
    pin_interpreter,
)
from arena.execution.test_executor import TestExecutionRequest, TestExecutor
from arena.reviewers.base import BaseReviewer
from tests.conftest import pack_case_count


def test_parse_accepts_string_argv_and_command_lists():
    assert parse_test_commands("pytest -q tests") == [["pytest", "-q", "tests"]]
    assert parse_test_commands(["pytest", "-q"]) == [["pytest", "-q"]]
    assert parse_test_commands([["pytest", "-q"], ["ruff", "check", "."]]) == [
        ["pytest", "-q"],
        ["ruff", "check", "."],
    ]
    assert parse_test_commands(None) == []
    assert parse_test_commands("   ") == []


def test_parse_rejects_shell_operators_and_garbage():
    with pytest.raises(ValidationError):
        parse_test_commands("pytest -q && rm -rf /")
    with pytest.raises(ValidationError):
        parse_test_commands("a | b")
    with pytest.raises(ValidationError):
        parse_test_commands("echo;rm x")
    with pytest.raises(ValidationError):
        parse_test_commands('unterminated "quote')
    with pytest.raises(ValidationError):
        parse_test_commands([["ok"], "mixed"])
    with pytest.raises(ValidationError):
        parse_test_commands([[]])


def test_pin_interpreter_normalizes_python_and_pytest():
    assert pin_interpreter(["pytest", "-q"]) == [sys.executable, "-m", "pytest", "-q"]
    assert pin_interpreter(["python", "-m", "pytest"]) == [sys.executable, "-m", "pytest"]
    assert pin_interpreter(["python3", "script.py"]) == [sys.executable, "script.py"]
    assert pin_interpreter(["npm", "test"]) == ["npm", "test"]


def test_pin_interpreter_leaves_path_qualified_interpreters_alone():
    """An explicit interpreter path is a choice of environment, not a name to fix.

    A reviewer that runs `.venv/bin/python` wants that virtualenv's packages.
    Rewriting it to the harness interpreter used to strip every dependency the
    wrapper was installed with, and it failed on missing imports rather than
    saying why.
    """
    for argv in (
        [".venv/bin/python", "script.py"],
        ["/opt/model-env/bin/python3", "run.py"],
        ["/opt/model-env/bin/pytest", "-q"],
        ["./tools/pytest", "-q"],
    ):
        assert pin_interpreter(list(argv)) == argv


def test_pin_interpreter_tolerates_an_empty_command():
    assert pin_interpreter([]) == []


def test_pin_container_interpreter_routes_pytest_through_python_m():
    # `python -m pytest` puts the workspace root on sys.path so a case that
    # imports a top-level module collects; the bare `pytest` script does not.
    assert pin_container_interpreter(["pytest", "-q", "tests"]) == [
        "python",
        "-m",
        "pytest",
        "-q",
        "tests",
    ]
    # The container's own python is used, never the harness sys.executable.
    assert pin_container_interpreter(["python3", "-m", "pytest"]) == ["python", "-m", "pytest"]
    assert pin_container_interpreter(["npm", "test"]) == ["npm", "test"]
    # Same rule as pin_interpreter: an explicit path is left as written.
    assert pin_container_interpreter(["/opt/venv/bin/pytest", "-q"]) == [
        "/opt/venv/bin/pytest",
        "-q",
    ]
    assert pin_container_interpreter([]) == []


def test_executor_runs_command_sequences_and_stops_on_failure(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sequence = [
        [sys.executable, "-c", "open('first.txt','w').write('ok'); print('first')"],
        [sys.executable, "-c", "raise SystemExit(3)"],
        [sys.executable, "-c", "open('third.txt','w').write('never')"],
    ]
    result = TestExecutor().execute(
        TestExecutionRequest(
            case_id="case",
            workspace_path=workspace,
            test_command=sequence,
            timeout_seconds=10,
            allow_local_execution=True,
        )
    )
    assert result.ran is True
    assert result.passed is False
    assert result.exit_code == 3
    assert (workspace / "first.txt").exists()
    assert not (workspace / "third.txt").exists()


def test_executor_reports_unparseable_command(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = TestExecutor().execute(
        TestExecutionRequest(
            case_id="case",
            workspace_path=workspace,
            test_command="pytest && true",
            timeout_seconds=5,
            allow_local_execution=True,
        )
    )
    assert result.ran is False
    assert "invalid_test_command" in (result.error or "")


class _CostlyReviewer(BaseReviewer):
    name = "stub-costly"
    model = "stub"

    def review(self, context: CaseContext) -> ReviewerResponse:
        result = ReviewResult(findings=[], overall_risk="none", review_summary="stub")
        return ReviewerResponse(
            raw_response=result.model_dump_json(),
            parsed_response=result,
            estimated_cost=1.0,
        )


def test_wall_clock_budget_stops_scheduling(tmp_path):
    run = run_benchmark(
        Path("benchmark_sets/v1"),
        _CostlyReviewer(),
        output_dir=tmp_path / "runs",
        persist=False,
        max_wall_seconds=0.0,
    )
    assert run.case_results == []
    assert len(run.skipped_case_ids) == pack_case_count()
    assert "max_wall_seconds" in (run.budget_stopped_reason or "")
    assert run.total_score == 0.0


def test_cost_budget_stops_scheduling_after_threshold(tmp_path):
    run = run_benchmark(
        Path("benchmark_sets/v1"),
        _CostlyReviewer(),
        output_dir=tmp_path / "runs",
        persist=False,
        max_cost=2.0,
    )
    # 1.0 per case: two cases run before the budget trips.
    assert len(run.case_results) == 2
    assert len(run.skipped_case_ids) == pack_case_count() - 2
    assert "max_cost" in (run.budget_stopped_reason or "")
