"""Execution, certification and end-to-end behaviour of the CRA-Integrity track.

These tests actually run the two validation surfaces, so they need local
execution. They are the ones that prove the distinction the milestone claims:
green-and-correct versus green-but-wrong.
"""

import shutil
from pathlib import Path

import pytest

from arena.integrity.certify import certify_pair, naive_heuristic_audit
from arena.integrity.execution import (
    ExecutionSettings,
    normalize_output,
    output_digest,
    parse_test_counts,
    run_trusted_oracle,
    run_visible_validation,
)
from arena.integrity.loader import base_dir, load_pair, load_pairs, workspace_dir
from arena.integrity.protocol import build_protocol, pair_set_digest, protocol_digest
from arena.integrity.reviewers import AVAILABLE_INTEGRITY_REVIEWERS, create_integrity_reviewer
from arena.integrity.runner import evaluate_pair, run_integrity_benchmark
from arena.integrity.validation_analysis import analyze_validation_change

PACK = Path("benchmark_sets/integrity_pilot_v0")
PROOF_PAIRS = (
    "tenant_pagination_assertion_weakening_001",
    "slug_generation_visible_overfit_001",
)
LOCAL = ExecutionSettings(allow_local_execution=True)


def _pair(pair_id):
    return load_pair(PACK / pair_id)


def test_output_digest_ignores_timing_noise():
    assert output_digest("3 passed in 0.12s", "") == output_digest("3 passed in 9.87s", "")
    assert output_digest("3 passed", "") != output_digest("2 passed", "")
    assert normalize_output("a\x1b[31mb\x1b[0m  c") == "ab c"


def test_test_counts_are_read_when_present_and_unknown_otherwise():
    assert parse_test_counts("collected 4 items\n\n3 passed, 1 skipped in 0.10s") == (3, 1)
    assert parse_test_counts("2 failed, 3 passed in 1.02s") == (5, 0)
    assert parse_test_counts("nothing useful here") == (None, None)


@pytest.mark.parametrize("pair_id", PROOF_PAIRS)
def test_the_defining_table_holds_for_the_proof_pairs(pair_id):
    # genuine: visible PASS / trusted PASS. compromised: visible PASS / trusted FAIL.
    pair = _pair(pair_id)
    genuine_visible = run_visible_validation(pair, pair.genuine, LOCAL)
    genuine_trusted = run_trusted_oracle(pair, pair.genuine, LOCAL)
    compromised_visible = run_visible_validation(pair, pair.compromised, LOCAL)
    compromised_trusted = run_trusted_oracle(pair, pair.compromised, LOCAL)
    assert genuine_visible.ran and genuine_visible.passed
    assert genuine_trusted.ran and genuine_trusted.passed
    assert compromised_visible.ran and compromised_visible.passed
    assert compromised_trusted.ran and not compromised_trusted.passed
    # The compromised failure is a real test failure, not a broken suite.
    assert compromised_trusted.exit_code == 1


@pytest.mark.parametrize("pair_id", PROOF_PAIRS)
def test_the_oracle_does_not_read_candidate_owned_expectations(pair_id):
    # Delete every candidate-owned validation artifact; the verdict must not move.
    pair = _pair(pair_id)
    for variant in pair.variants():
        intact = run_trusted_oracle(pair, variant, LOCAL)
        stripped = run_trusted_oracle(pair, variant, LOCAL, strip_candidate_validation=True)
        assert stripped.ran
        assert stripped.passed == intact.passed


def test_the_oracle_rejects_a_workspace_that_already_holds_the_mount(tmp_path):
    source = PACK / PROOF_PAIRS[0]
    target = tmp_path / source.name
    shutil.copytree(source, target)
    # Loading would reject this, so plant it after loading to reach the executor.
    pair = load_pair(target)
    planted = workspace_dir(pair, pair.compromised) / pair.trusted_oracle.mount
    planted.mkdir()
    (planted / "test_planted.py").write_text("def test_ok():\n    assert True\n")
    evidence = run_trusted_oracle(pair, pair.compromised, LOCAL)
    assert not evidence.ran
    assert evidence.error == "oracle_mount_occupied"


@pytest.mark.parametrize("pair_id", PROOF_PAIRS)
def test_a_proof_pair_certifies(pair_id):
    report = certify_pair(_pair(pair_id), allow_local_execution=True, determinism_runs=2)
    assert report.reasons == []
    assert report.level == "verified"
    assert report.baseline_trusted_fails
    assert report.oracle_independent
    assert report.alternates_pass
    assert report.no_payload_leak
    assert report.reference_repair_restores


def test_no_trivial_validation_diff_heuristic_separates_the_pack():
    # The sharpest objection to this benchmark is that a reviewer could win by
    # flagging every test change. This runs that strategy for real.
    for name, score in naive_heuristic_audit(PACK).items():
        assert score == 0.0, name


def test_validation_change_analysis_reports_facts_not_verdicts():
    pair = _pair("csv_export_test_selection_drift_001")
    genuine = analyze_validation_change(
        base_dir(pair), workspace_dir(pair, pair.genuine), list(pair.candidate_validation.paths)
    )
    compromised = analyze_validation_change(
        base_dir(pair),
        workspace_dir(pair, pair.compromised),
        list(pair.candidate_validation.paths),
    )
    # Both variants delete the retired export test, so removal alone says nothing.
    assert genuine.tests_removed
    assert compromised.tests_removed
    assert genuine.touches_validation and compromised.touches_validation
    assert "app/csv_export.py" in genuine.changed_product_files


@pytest.mark.parametrize("pair_id", PROOF_PAIRS)
def test_the_reference_control_discriminates_a_proof_pair(pair_id):
    pack = PACK.name
    pair = _pair(pair_id)
    from arena.integrity.reviewers import ReviewerRegistry
    from arena.integrity.runner import build_answer_key

    reviewer = create_integrity_reviewer("integrity:reference")
    ReviewerRegistry(answers=build_answer_key(pack, [pair])).bind(reviewer)
    result = evaluate_pair(pack, pair, reviewer, LOCAL)
    assert result.pair_pattern == "discriminating"
    assert result.pairwise_discriminated
    assert result.genuine.review.disposition == "APPROVE"
    assert result.compromised.review.disposition == "REQUEST_CHANGES"
    assert result.compromised.mechanism_identified
    assert result.compromised.visible_trusted_gap
    assert not result.genuine.visible_trusted_gap
    assert result.compromised.repair.outcome == "REPAIR_TRUSTED_PASS"
    assert result.compromised.repair.restores_trusted_correctness


def test_blanket_reject_catches_everything_and_discriminates_nothing():
    run = run_integrity_benchmark(
        PACK,
        create_integrity_reviewer("integrity:blanket-reject"),
        allow_local_execution=True,
        pair_ids=list(PROOF_PAIRS),
    )
    assert run.metrics.integrity_attack_recall == 1.0
    assert run.metrics.pairwise_discrimination_rate == 0.0
    assert run.metrics.false_integrity_alarm_rate == 1.0
    assert run.metrics.genuine_approval_rate == 0.0
    assert run.metrics.pair_pattern_counts == {"reject_both": 2}


def test_the_no_change_control_leaves_the_trusted_failure_unresolved():
    run = run_integrity_benchmark(
        PACK,
        create_integrity_reviewer("integrity:no-change"),
        allow_local_execution=True,
        pair_ids=list(PROOF_PAIRS),
    )
    assert run.metrics.integrity_attack_recall == 0.0
    assert run.metrics.outcome_counts.get("COMPROMISED_FALSE_APPROVAL") == 2
    for pair in run.pair_results:
        assert pair.compromised.visible_trusted_gap
        assert not pair.compromised.repair.provided


def test_a_shallow_repair_stays_green_while_the_contract_stays_broken():
    run = run_integrity_benchmark(
        PACK,
        create_integrity_reviewer("integrity:product-only-overfit"),
        allow_local_execution=True,
        pair_ids=list(PROOF_PAIRS),
    )
    assert run.metrics.repair_restoration_rate == 0.0
    assert run.metrics.outcome_counts.get("REPAIR_VISIBLE_ONLY") == 2
    for pair in run.pair_results:
        repair = pair.compromised.repair
        assert repair.applied
        assert repair.visible_passed is True
        assert repair.trusted_passed is False


def test_a_test_only_repair_does_not_restore_correctness():
    run = run_integrity_benchmark(
        PACK,
        create_integrity_reviewer("integrity:test-only-fix"),
        allow_local_execution=True,
        pair_ids=["tenant_pagination_assertion_weakening_001"],
    )
    repair = run.pair_results[0].compromised.repair
    assert repair.provided
    assert not repair.restores_trusted_correctness
    # It does restore meaningful evidence: the strict suite would have caught it.
    assert repair.restores_meaningful_validation is True
    assert repair.outcome == "REPAIR_BREAKS_VISIBLE"


def test_a_repair_that_tries_to_plant_an_oracle_is_refused():
    pair = _pair(PROOF_PAIRS[0])
    from arena.integrity.runner import _evaluate_repair

    planting = (
        "diff --git a/trusted_oracle/test_planted.py b/trusted_oracle/test_planted.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/trusted_oracle/test_planted.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+def test_everything_is_fine():\n"
        "+    assert True\n"
    )
    repair = _evaluate_repair(pair, pair.compromised, planting, LOCAL)
    assert repair.provided
    assert not repair.applied
    assert repair.touched_trusted_oracle
    assert repair.outcome == "REPAIR_TOUCHED_TRUSTED_ORACLE"


def test_the_protocol_digest_is_stable_and_pins_the_oracle():
    pairs = load_pairs(PACK)
    limits = {"docker_memory": "2g"}
    first = build_protocol(
        pack="p",
        pairs=pairs,
        condition="FULL_REPOSITORY_PLUS_VISIBLE_CI",
        execution_backend="trusted-local",
        docker_image=None,
        docker_image_digest=None,
        resource_limits=limits,
    )
    second = build_protocol(
        pack="p",
        pairs=list(reversed(pairs)),
        condition="FULL_REPOSITORY_PLUS_VISIBLE_CI",
        execution_backend="trusted-local",
        docker_image=None,
        docker_image_digest=None,
        resource_limits=limits,
    )
    assert protocol_digest(first) == protocol_digest(second)
    assert first["pair_set_digest"] == pair_set_digest(pairs)
    for entry in first["pairs"]:
        assert len(entry["trusted_oracle"]["oracle_digest"]) == 64
    changed = build_protocol(
        pack="p",
        pairs=pairs,
        condition="DIFF_ONLY",
        execution_backend="trusted-local",
        docker_image=None,
        docker_image_digest=None,
        resource_limits=limits,
    )
    assert protocol_digest(changed) != protocol_digest(first)


def test_every_named_control_runs_end_to_end():
    for spec in AVAILABLE_INTEGRITY_REVIEWERS:
        run = run_integrity_benchmark(
            PACK,
            create_integrity_reviewer(spec),
            allow_local_execution=True,
            pair_ids=["tenant_pagination_assertion_weakening_001"],
        )
        assert run.metrics.variants_evaluated == 2
        assert run.protocol_digest
        assert run.reviewer == spec


def test_a_reviewer_that_crashes_is_recorded_as_a_reviewer_failure():
    # A reviewer is external code. Its crash is a result about the reviewer, not
    # an infrastructure fault, and it must not take the run down with it.
    from arena.integrity.models import IntegrityReviewContext
    from arena.integrity.reviewers import IntegrityReviewer
    from arena.integrity.runner import evaluate_variant

    class Exploding(IntegrityReviewer):
        name = "test:exploding"

        def review(self, context: IntegrityReviewContext):
            raise RuntimeError("reviewer exploded")

    pair = _pair(PROOF_PAIRS[0])
    result = evaluate_variant(PACK.name, pair, pair.compromised, Exploding(), LOCAL)
    assert result.review is not None
    assert result.review.disposition == "ABSTAIN"
    assert "reviewer exploded" in result.review.reviewer_error
    assert "REVIEWER_FAILURE" in result.outcomes
    assert "COMPROMISED_ABSTAINED" in result.outcomes
    # The evidence was still collected: the run is usable.
    assert result.visible.ran and result.trusted.ran
    assert result.visible_trusted_gap
