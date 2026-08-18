import inspect
import json
import sys
from pathlib import Path

import pytest

from arena.benchmark.case_loader import build_context, load_cases
from arena.reviewers.custom_command import (
    CustomCommandReviewer,
    expand_command_template,
    serialize_reviewer_case,
)

FIXTURES = Path("tests/fixtures/fake_reviewers")
VALID_REVIEWER = FIXTURES / "valid_reviewer.py"


@pytest.fixture
def audit_benchmark_dir() -> Path:
    return Path("benchmark_sets/audit_v1")


def test_serialize_reviewer_case_excludes_ground_truth(audit_benchmark_dir):
    case = load_cases(audit_benchmark_dir)[0]
    context = build_context(case)
    payload = serialize_reviewer_case(context)
    serialized = json.dumps(payload)
    forbidden = [
        "ground_truth",
        "case_dir",
        "must_mention",
        "acceptable_fix_keywords",
        "structural_validators",
        "false_positive_penalty",
        "line_ranges",
        "primary_bug",
        "validation",
        "scoring",
    ]
    for token in forbidden:
        assert token not in serialized


def test_valid_fake_reviewer_command(audit_benchmark_dir):
    case = load_cases(audit_benchmark_dir)[0]
    context = build_context(case)
    reviewer = CustomCommandReviewer(
        f"python {VALID_REVIEWER} --case {{case_json}}",
        timeout_seconds=30,
    )
    response = reviewer.review(context)
    assert response.invalid_output is False
    assert response.parsed_response is not None
    assert response.parsed_response.findings


def test_invalid_json_command_fails_safely(audit_benchmark_dir):
    case = load_cases(audit_benchmark_dir)[0]
    context = build_context(case)
    reviewer = CustomCommandReviewer(
        f"python {FIXTURES / 'invalid_json_reviewer.py'} {{case_json}}",
        timeout_seconds=30,
    )
    response = reviewer.review(context)
    assert response.invalid_output is True


def test_nonzero_exit_command_fails_safely(audit_benchmark_dir):
    case = load_cases(audit_benchmark_dir)[0]
    context = build_context(case)
    reviewer = CustomCommandReviewer(
        f"python {FIXTURES / 'nonzero_exit_reviewer.py'} {{case_json}}",
        timeout_seconds=30,
    )
    response = reviewer.review(context)
    assert response.invalid_output is True


def test_timeout_command_fails_safely(audit_benchmark_dir):
    case = load_cases(audit_benchmark_dir)[0]
    context = build_context(case)
    reviewer = CustomCommandReviewer(
        f"python {FIXTURES / 'slow_reviewer.py'} {{case_json}}",
        timeout_seconds=1,
    )
    response = reviewer.review(context)
    assert response.invalid_output is True
    assert "timed out" in response.raw_response


def test_custom_command_pins_python_to_harness_interpreter(audit_benchmark_dir, monkeypatch):
    from arena.execution.process import SupervisedResult

    captured: list[list[str]] = []

    def fake_run_supervised(args, **kwargs):
        captured.append(list(args))
        payload = '{"findings": [], "overall_risk": "none", "review_summary": "ok"}'
        return SupervisedResult(returncode=0, stdout=payload, stderr="", timed_out=False)

    monkeypatch.setattr("arena.reviewers.custom_command.run_supervised", fake_run_supervised)
    case = load_cases(audit_benchmark_dir)[0]
    context = build_context(case)
    reviewer = CustomCommandReviewer(
        f"python {VALID_REVIEWER} --case {{case_json}}",
        timeout_seconds=30,
    )
    response = reviewer.review(context)
    assert response.invalid_output is False
    assert captured
    assert captured[0][0] == sys.executable


def test_custom_command_does_not_use_shell_true():
    source = inspect.getsource(CustomCommandReviewer.review)
    assert "shell=True" not in source


def test_expand_command_template_uses_shlex():
    args = expand_command_template(
        "python script.py --case {case_json}",
        case_json=Path("/tmp/case.json"),
        diff_file=Path("/tmp/pr.diff"),
        case_id="demo",
        workspace=Path("/tmp/workspace"),
    )
    assert args[0] == "python"
    assert args[-1] == "/tmp/case.json"
