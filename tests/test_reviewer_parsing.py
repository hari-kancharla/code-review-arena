"""Phase 1B commit 3: exact reviewer-output parsing, salvage evidence, admission.

Tests Arena's contract and eligibility behavior (not Pydantic internals): exact is
the default comparable contract, salvage is development-only and non-comparable,
invalid is a scoring reviewer failure, and reviewer paths are admitted.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from arena.cli.main import app
from arena.core import limits
from arena.core.models import ReviewerResponse
from arena.reports.leaderboard import eligibility_from_fields
from arena.reviewers.response_parser import parse_reviewer_output
from arena.reviewers.strict_json import StrictJSONError, strict_loads
from arena.security.paths import admit_reviewer_path

runner = CliRunner()


def _finding(**overrides: object) -> dict:
    base = {
        "title": "t",
        "summary": "s",
        "category": "correctness",
        "severity": "high",
        "file": "app/a.py",
        "line_start": 1,
        "line_end": 2,
        "evidence": "e",
        "confidence": 0.9,
    }
    base.update(overrides)
    return base


def _review(**overrides: object) -> dict:
    base = {"findings": [_finding()], "overall_risk": "low", "review_summary": "s"}
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# Strict JSON decoder                                                         #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("text", ['{"a": 1, "a": 2}', '{"o": {"k": 1, "k": 2}}'])
def test_strict_json_rejects_duplicate_keys(text):
    with pytest.raises(StrictJSONError):
        strict_loads(text)


@pytest.mark.parametrize("text", ["NaN", "Infinity", "-Infinity", "1e400", "[1e400]"])
def test_strict_json_rejects_non_finite_numbers(text):
    with pytest.raises(StrictJSONError):
        strict_loads(text)


def test_strict_json_depth_and_node_boundaries(monkeypatch):
    monkeypatch.setattr(limits, "JSON_MAX_NODES", 5)
    strict_loads('{"a": [1, 2, 3]}')  # root, list, 3 ints == 5 nodes
    with pytest.raises(StrictJSONError):
        strict_loads('{"a": [1, 2, 3, 4]}')  # 6 nodes
    monkeypatch.setattr(limits, "JSON_MAX_NODES", 200_000)
    monkeypatch.setattr(limits, "JSON_MAX_DEPTH", 4)
    strict_loads("[[[1]]]")  # depth 4 (3 lists + scalar)
    with pytest.raises(StrictJSONError):
        strict_loads("[[[[1]]]]")  # depth 5


def test_strict_json_deeply_nested_does_not_crash():
    with pytest.raises(StrictJSONError):
        strict_loads("[" * 50_000 + "]" * 50_000)


# --------------------------------------------------------------------------- #
# Exact / default mode                                                        #
# --------------------------------------------------------------------------- #


def test_exact_accepts_clean_object_and_surrounding_whitespace():
    assert parse_reviewer_output(json.dumps(_review())).status == "exact"
    # Leading/trailing whitespace is valid JSON, so it stays exact (not tolerant).
    outcome = parse_reviewer_output("\n\t " + json.dumps(_review()) + "  \n")
    assert outcome.status == "exact"
    assert outcome.actions == []


@pytest.mark.parametrize(
    "raw",
    [
        "```json\n" + json.dumps(_review()) + "\n```",  # fenced
        "Here is my review: " + json.dumps(_review()),  # prose before
        json.dumps(_review())[:-1] + ",}",  # trailing comma
        json.dumps([_finding()]),  # bare findings list
        json.dumps(_review(extra_top="x")),  # unknown top-level field
        json.dumps(
            {**_review(), "findings": [_finding(extra_finding="x")]}
        ),  # unknown finding field
        json.dumps(
            {**_review(), "findings": [_finding(), _finding(confidence=5)]}
        ),  # one bad finding
        '{"a": 1, "a": 2}',  # duplicate key
        json.dumps(_review())[:-1] + ', "review_summary": NaN}',  # non-finite
    ],
)
def test_exact_rejects_non_exact_inputs(raw):
    assert parse_reviewer_output(raw).status == "invalid"


def test_exact_missing_required_top_level_field_is_invalid():
    assert parse_reviewer_output(json.dumps({"findings": []})).status == "invalid"


def test_one_invalid_finding_invalidates_the_whole_response():
    raw = json.dumps({**_review(), "findings": [_finding(), _finding(file="../escape.py")]})
    assert parse_reviewer_output(raw).status == "invalid"


# --------------------------------------------------------------------------- #
# Salvage-enabled (development-only) mode                                      #
# --------------------------------------------------------------------------- #


def test_salvage_keeps_exact_exact():
    outcome = parse_reviewer_output(json.dumps(_review()), enable_repair=True)
    assert outcome.status == "exact"
    assert outcome.actions == []


@pytest.mark.parametrize(
    "raw,action",
    [
        ("```json\n" + json.dumps(_review()) + "\n```", "strip_markdown_fence"),
        ("Here is my review: " + json.dumps(_review()), "extract_json_object"),
        (json.dumps(_review())[:-1] + ",}", "remove_trailing_commas"),
    ],
)
def test_salvage_tolerant_records_the_transform(raw, action):
    outcome = parse_reviewer_output(raw, enable_repair=True)
    assert outcome.status == "tolerant"
    assert action in outcome.actions
    assert outcome.dropped_finding_count == 0


def test_salvage_repairs_bare_list_and_missing_envelope_fields():
    outcome = parse_reviewer_output(json.dumps([_finding()]), enable_repair=True)
    assert outcome.status == "repaired"
    assert "wrap_findings_list" in outcome.actions
    assert outcome.result is not None and outcome.result.overall_risk == "medium"


def test_salvage_drops_invalid_findings_only_in_repair_with_evidence():
    raw = json.dumps([_finding(), _finding(file="../escape.py"), {"title": "broken"}])
    outcome = parse_reviewer_output(raw, enable_repair=True)
    assert outcome.status == "repaired"
    assert outcome.input_finding_count == 3
    assert outcome.retained_finding_count == 1
    assert outcome.dropped_finding_count == 2
    assert "drop_invalid_findings" in outcome.actions
    assert outcome.failure_reason and "findings[" in outcome.failure_reason


def test_salvage_all_invalid_findings_yields_repaired_empty_with_dropped_count():
    raw = json.dumps([{"title": "broken"}, {"nope": 1}])
    outcome = parse_reviewer_output(raw, enable_repair=True)
    assert outcome.status == "repaired"
    assert outcome.result is not None and outcome.result.findings == []
    assert outcome.dropped_finding_count == 2


@pytest.mark.parametrize("raw", ['{"a": 1, "a": 2}', '{"x": NaN}'])
def test_salvage_still_rejects_duplicate_keys_and_non_finite(raw):
    assert parse_reviewer_output(raw, enable_repair=True).status == "invalid"


# --------------------------------------------------------------------------- #
# ReviewerResponse legacy compatibility (derive parse_status)                 #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "attempts,invalid,expected",
    [(1, False, "exact"), (2, False, "tolerant"), (3, False, "repaired"), (1, True, "invalid")],
)
def test_legacy_reviewer_response_derives_parse_status(attempts, invalid, expected):
    # Old saved responses have no parse_status field; it is derived on load.
    legacy = {"raw_response": "{}", "parse_attempts": attempts, "invalid_output": invalid}
    assert ReviewerResponse.model_validate(legacy).parse_status == expected


def test_new_reviewer_response_keeps_explicit_parse_status():
    # An explicit parse_status is preserved (not overwritten by the legacy-derive).
    response = ReviewerResponse(
        raw_response="{}", parse_attempts=1, parse_status="invalid", invalid_output=True
    )
    assert response.parse_status == "invalid"


# --------------------------------------------------------------------------- #
# Reviewer-path admission (context-aware Git-prefix resolution)                #
# --------------------------------------------------------------------------- #


def test_admit_path_without_prefix_or_with_dot_needs_no_context():
    assert admit_reviewer_path("app/a.py") == "app/a.py"
    assert admit_reviewer_path("./app/a.py") == "app/a.py"
    assert admit_reviewer_path("src/pkg/mod.py") == "src/pkg/mod.py"


def test_admit_path_never_strips_prefix_without_context():
    # No known-path context: a/ and b/ are preserved (could be real directories).
    assert admit_reviewer_path("a/service.py") == "a/service.py"
    assert admit_reviewer_path("b/config.yaml") == "b/config.yaml"


@pytest.mark.parametrize(
    "raw,known,expected",
    [
        ("a/service.py", {"service.py"}, "service.py"),  # normal Git prefix (relevant files)
        ("b/config.yaml", {"config.yaml"}, "config.yaml"),  # normal Git b/ prefix
        ("a/service.py", {"a/service.py"}, "a/service.py"),  # real top-level a/ directory
        ("b/config.yaml", {"b/config.yaml"}, "b/config.yaml"),  # real top-level b/ directory
        ("a/a/service.py", {"a/service.py"}, "a/service.py"),  # Git prefix over a real a/ path
        ("a/service.py", {"other.py"}, "a/service.py"),  # neither form known -> keep complete
    ],
)
def test_admit_path_resolves_prefix_against_known_paths(raw, known, expected):
    assert admit_reviewer_path(raw, frozenset(known)) == expected


def test_admit_path_ambiguous_when_both_forms_known():
    with pytest.raises(ValueError):
        admit_reviewer_path("a/service.py", frozenset({"a/service.py", "service.py"}))


def test_admit_path_uses_diff_only_and_relevant_only_context():
    from arena.core.models import CaseContext, ReviewerCaseMetadata
    from arena.reviewers.response_parser import known_paths_from_context

    # Known only from relevant files.
    ctx_files = CaseContext(
        case=ReviewerCaseMetadata(
            id="c", title="t", category="x", severity="high", stack=["py"], description="d"
        ),
        diff="",
        relevant_files={"service.py": "x\n"},
    )
    known = known_paths_from_context(ctx_files)
    assert admit_reviewer_path("a/service.py", known) == "service.py"

    # Known only from the diff (no relevant files).
    ctx_diff = CaseContext(
        case=ReviewerCaseMetadata(
            id="c", title="t", category="x", severity="high", stack=["py"], description="d"
        ),
        diff="--- a/service.py\n+++ b/service.py\n@@ -1 +1 @@\n-x\n+y\n",
        relevant_files={},
    )
    known = known_paths_from_context(ctx_diff)
    assert admit_reviewer_path("b/service.py", known) == "service.py"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "/etc/passwd",
        "C:\\Windows\\x",
        "\\\\host\\share\\x",
        "../escape.py",
        "./../escape.py",
        "a//b.py",
        "a\\b.py",
        "a\x00b.py",
        "/dev/null",
        "dev/null",
        ".hidden/x.py",
        "naïve.py",
        "x" * 2000,
    ],
)
def test_admit_reviewer_path_rejects_unsafe_forms(raw):
    with pytest.raises(ValueError):
        admit_reviewer_path(raw, frozenset({"anything.py"}))


# --------------------------------------------------------------------------- #
# HTTP + custom-command integration (one parser, one status semantics)         #
# --------------------------------------------------------------------------- #


def _http(style, body, *, enable_repair=False):
    from arena.core.models import CaseContext, ReviewerCaseMetadata
    from arena.reviewers.http import HttpReviewer

    context = CaseContext(
        case=ReviewerCaseMetadata(
            id="c", title="t", category="x", severity="high", stack=["python"], description="d"
        ),
        diff="--- a\n+++ b\n",
        relevant_files={"app/a.py": "x = 1\n"},
    )

    def handler(request):
        if style == "openai":
            return httpx.Response(200, json={"choices": [{"message": {"content": body}}]})
        return httpx.Response(200, content=body.encode())

    reviewer = HttpReviewer(
        "http://local/r",
        style=style,
        enable_repair=enable_repair,
        transport=httpx.MockTransport(handler),
    )
    return reviewer.review(context)


@pytest.mark.parametrize("style", ["openai", "json"])
def test_http_status_matrix(style):
    fenced = "```json\n" + json.dumps(_review()) + "\n```"
    assert _http(style, json.dumps(_review())).parse_status == "exact"
    assert _http(style, fenced).parse_status == "invalid"  # default: no salvage
    assert _http(style, fenced, enable_repair=True).parse_status == "tolerant"
    assert _http(style, json.dumps([_finding()]), enable_repair=True).parse_status == "repaired"
    assert _http(style, "not json").parse_status == "invalid"


def _custom(stdout, *, returncode=0, enable_repair=False, monkeypatch):
    from arena.core.models import CaseContext, ReviewerCaseMetadata
    from arena.execution.process import SupervisedResult
    from arena.reviewers import custom_command as cc

    monkeypatch.setattr(
        cc,
        "run_supervised",
        lambda *a, **k: SupervisedResult(
            returncode=returncode, stdout=stdout, stderr="", timed_out=False
        ),
    )
    context = CaseContext(
        case=ReviewerCaseMetadata(
            id="c", title="t", category="x", severity="high", stack=["python"], description="d"
        ),
        diff="--- a\n+++ b\n",
        relevant_files={"app/a.py": "x = 1\n"},
    )
    return cc.CustomCommandReviewer("echo {case_json}", enable_repair=enable_repair).review(context)


def test_custom_command_status_matrix(monkeypatch):
    fenced = "```json\n" + json.dumps(_review()) + "\n```"
    assert _custom(json.dumps(_review()), monkeypatch=monkeypatch).parse_status == "exact"
    assert _custom(fenced, monkeypatch=monkeypatch).parse_status == "invalid"
    assert _custom(fenced, enable_repair=True, monkeypatch=monkeypatch).parse_status == "tolerant"
    assert (
        _custom(json.dumps([_finding()]), enable_repair=True, monkeypatch=monkeypatch).parse_status
        == "repaired"
    )
    # A nonzero exit is invalid even with valid-looking stdout.
    assert (
        _custom(json.dumps(_review()), returncode=1, monkeypatch=monkeypatch).parse_status
        == "invalid"
    )


# --------------------------------------------------------------------------- #
# Built-in controls + run metadata + leaderboard policy                        #
# --------------------------------------------------------------------------- #


def test_builtin_controls_are_exact():
    from arena.benchmark.case_loader import build_context, load_cases
    from arena.reviewers.controls import ControlReviewer

    case = load_cases(Path("benchmark_sets/audit_v1"))[0]
    response = ControlReviewer("perfect").review(build_context(case))
    assert response.parse_status == "exact"
    assert response.parse_attempts == 1
    assert response.parse_actions == []
    assert response.dropped_finding_count == 0


def test_exact_run_records_comparable_metadata(tmp_path):
    from arena.benchmark.benchmark_runner import run_benchmark
    from arena.reviewers.controls import ControlReviewer

    run = run_benchmark(
        Path("benchmark_sets/audit_v1"),
        ControlReviewer("perfect"),
        output_dir=tmp_path / "runs",
        persist=False,
        mode="review",
    )
    assert run.metadata.non_exact_output_used is False
    assert run.metadata.reviewer_parse_status_counts.get("exact") == run.case_count


def test_eligibility_requires_exact_output_by_default():
    base = dict(
        schema_version=2,
        run_status="complete",
        execution_backend="docker",
        coverage_rate=1.0,
        pack_digest_externally_verified=True,
        reviewer_oracle_reachable=False,
    )
    # all exact (or exact+invalid: invalid does not set non_exact) -> eligible
    assert eligibility_from_fields(**base, non_exact_output_used=False) is True
    # one tolerant/repaired -> non-comparable by default
    assert eligibility_from_fields(**base, non_exact_output_used=True) is False
    # legacy unknown -> excluded by default
    assert eligibility_from_fields(**base, non_exact_output_used=None) is False
    # all visible with include_unverified
    for value in (False, True, None):
        assert (
            eligibility_from_fields(**base, non_exact_output_used=value, include_unverified=True)
            is True
        )


# --------------------------------------------------------------------------- #
# CLI numeric boundaries                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "arg",
    [
        f"--beta={limits.BETA_MAX + 1}",
        f"--reviewer-timeout-seconds={limits.REVIEWER_TIMEOUT_SECONDS_MAX + 1}",
        f"--max-wall-seconds={limits.API_WALL_SECONDS_MAX + 1}",
        f"--max-cost={limits.API_COST_MAX + 1}",
    ],
)
def test_cli_run_rejects_out_of_range_numeric_options(arg):
    # Rejected at option parsing, before any benchmark runs.
    result = runner.invoke(app, ["run", "benchmark_sets/audit_v1", arg])
    assert result.exit_code != 0
