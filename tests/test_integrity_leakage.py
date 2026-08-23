"""Adversarial checks that the trusted oracle never reaches the reviewer.

This track is unusually sensitive to oracle leakage: a reviewer that can read the
hidden contract does not have to reason about evidence at all. Every check here
tries to get oracle content into a reviewer payload and asserts that it fails.
"""

import shutil
from pathlib import Path

import pytest

from arena.core.errors import ValidationError
from arena.integrity.context import (
    FORBIDDEN_PAYLOAD_TOKENS,
    assert_no_oracle_leak,
    build_review_context,
    oracle_tokens,
    payload_text,
    submission_id,
    visible_ci_summary,
)
from arena.integrity.execution import ExecutionSettings, run_trusted_oracle
from arena.integrity.loader import base_dir, load_pair, load_pairs, oracle_dir, workspace_dir
from arena.integrity.models import ExecutionEvidence
from arena.integrity.validation_analysis import analyze_validation_change

PACK = Path("benchmark_sets/integrity_pilot_v0")
MARKER = "ZZQX-ORACLE-CANARY-9f3a1c-DO-NOT-LEAK"


def _pairs():
    return load_pairs(PACK)


def _context(pair, variant, condition="FULL_REPOSITORY_PLUS_VISIBLE_CI"):
    change = analyze_validation_change(
        base_dir(pair), workspace_dir(pair, variant), list(pair.candidate_validation.paths)
    )
    return build_review_context(
        PACK.name, pair, variant, condition=condition, validation_change=change
    )


@pytest.mark.parametrize(
    "condition",
    [
        "DIFF_ONLY",
        "DIFF_PLUS_TASK",
        "DIFF_PLUS_TASK_PLUS_TESTS",
        "FULL_REPOSITORY",
        "FULL_REPOSITORY_PLUS_VISIBLE_CI",
    ],
)
def test_no_payload_in_any_condition_carries_oracle_content(condition):
    for pair in _pairs():
        for variant in pair.variants():
            context = _context(pair, variant, condition)
            haystack = payload_text(context)
            for token in oracle_tokens(pair):
                assert token not in haystack, (pair.id, variant.id, token[:60])


def test_no_payload_names_the_variant_kind_or_the_mechanism():
    for pair in _pairs():
        for variant in pair.variants():
            lowered = payload_text(_context(pair, variant)).casefold()
            for token in FORBIDDEN_PAYLOAD_TOKENS:
                assert token not in lowered
            assert variant.id.casefold() not in lowered
            for mechanism in variant.mechanisms:
                assert mechanism.casefold() not in lowered


def test_no_payload_echoes_a_grading_keyword():
    # A reviewer that could read the grader's own phrasing would score for
    # echoing it rather than for reasoning to it.
    for pair in _pairs():
        for variant in pair.variants():
            lowered = payload_text(_context(pair, variant)).casefold()
            for keyword in variant.mechanism_keywords:
                assert keyword.casefold() not in lowered


def test_the_submission_id_does_not_encode_the_variant():
    for pair in _pairs():
        identifiers = [submission_id(PACK.name, pair, v) for v in pair.variants()]
        assert len(set(identifiers)) == 2
        for identifier in identifiers:
            assert "genuine" not in identifier
            assert "compromised" not in identifier
            assert pair.id not in identifier


def test_a_planted_oracle_canary_is_caught(tmp_path):
    source = PACK / "tenant_pagination_assertion_weakening_001"
    target = tmp_path / source.name
    shutil.copytree(source, target)
    pair = load_pair(target)
    # Put a distinctive oracle line into a reviewer-visible file and prove the
    # guard notices. This is the failure mode the guard exists for.
    canary = f"# {MARKER} this line is a long distinctive oracle string for the guard"
    (oracle_dir(pair) / "test_pagination_contract.py").write_text(
        canary + "\n" + (oracle_dir(pair) / "test_pagination_contract.py").read_text(),
        encoding="utf-8",
    )
    visible_file = workspace_dir(pair, pair.compromised) / "app" / "pagination.py"
    visible_file.write_text(canary + "\n" + visible_file.read_text(), encoding="utf-8")
    with pytest.raises(ValidationError, match="leaks trusted-oracle content"):
        _context(pair, pair.compromised)


def test_the_guard_ignores_import_lines_and_baseline_lines(tmp_path):
    # A shared import names the product surface the reviewer is already shown, and
    # a line that predates the pull request cannot be oracle-derived. Neither is
    # a leak, and treating them as one would push authors to weaken the guard.
    source = PACK / "config_type_check_validation_bypass_001"
    target = tmp_path / source.name
    shutil.copytree(source, target)
    pair = load_pair(target)
    tokens = oracle_tokens(pair)
    assert not any(token.startswith(("import ", "from ")) for token in tokens)
    assert_no_oracle_leak(_context(pair, pair.genuine), pair, pair.genuine)


def test_the_visible_ci_summary_never_mentions_the_trusted_surface():
    # The summary is the green badge a pull-request page shows. It is built from
    # visible evidence only, so nothing about the hidden surface can reach it.
    evidence = ExecutionEvidence(
        zone="visible",
        command=[["pytest", "-q", "tests"]],
        ran=True,
        passed=True,
        exit_code=0,
        executed_test_count=4,
        skipped_test_count=0,
        output_digest="0" * 64,
        duration_ms=12,
        backend="trusted-local",
        output_tail="4 passed",
    )
    summary = visible_ci_summary(evidence)
    rendered = summary.model_dump_json().casefold()
    assert "trusted" not in rendered
    assert "oracle" not in rendered
    assert "4 passed" not in rendered
    assert summary.command == "pytest -q tests"
    assert summary.passed is True


def test_the_reviewer_payload_never_carries_the_trusted_verdict():
    for pair in _pairs():
        for variant in pair.variants():
            context = _context(pair, variant)
            dumped = context.model_dump_json().casefold()
            assert "visible_trusted_gap" not in dumped
            assert "trusted" not in dumped


def test_trusted_output_is_not_retained_unless_the_grader_asks(tmp_path):
    pair = load_pair(PACK / "tenant_pagination_assertion_weakening_001")
    settings = ExecutionSettings(allow_local_execution=True)
    quiet = run_trusted_oracle(pair, pair.compromised, settings)
    loud = run_trusted_oracle(pair, pair.compromised, settings, keep_output=True)
    assert quiet.output_tail == ""
    assert loud.output_tail != ""
    # The digest identifies the output without carrying it, so a run record can
    # pin what the oracle printed with no leak risk.
    assert quiet.output_digest == loud.output_digest
    assert len(quiet.output_digest) == 64
