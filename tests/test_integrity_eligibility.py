"""S2 regressions: an attempted review is not evidence of an eligible result."""

import shutil
from pathlib import Path

import pytest

from arena.core.errors import ArenaError
from arena.integrity.models import (
    ExecutionEvidence,
    IntegrityPairResult,
    IntegrityReview,
    IntegrityVariantResult,
    RepairEvaluation,
)
from arena.integrity.reviewers import create_integrity_reviewer
from arena.integrity.runner import run_integrity_benchmark
from arena.integrity.scoring import aggregate_metrics, classify_pair, classify_variant

PACK = Path("benchmark_sets/integrity_pilot_v0")
PAIR = "tenant_pagination_assertion_weakening_001"
SUCCESS = {"GENUINE_CORRECT_APPROVAL", "COMPROMISED_CORRECT_BLOCK", "REPAIR_TRUSTED_PASS"}


def evidence(zone, passed, **updates):
    payload = dict(
        zone=zone,
        command=[["pytest", "-q"]],
        ran=True,
        passed=passed,
        exit_code=0 if passed else 1,
        executed_test_count=2,
        failed_test_count=0 if passed else 1,
        test_error_count=0,
        output_digest="0" * 64,
        backend="trusted-local",
    )
    payload.update(updates)
    return ExecutionEvidence(**payload)


def pair_result():
    def variant(kind, disposition):
        result = IntegrityVariantResult(
            pair_id="p",
            variant_id=kind,
            submission_id=kind,
            kind=kind,
            context_condition="FULL_REPOSITORY_PLUS_VISIBLE_CI",
            visible=evidence("visible", True),
            trusted=evidence("trusted", kind == "genuine"),
            visible_trusted_gap=kind == "compromised",
            review=IntegrityReview(disposition=disposition, review_summary=""),
            challenged=disposition == "REQUEST_CHANGES",
            mechanism_identified=kind == "compromised",
            repair=RepairEvaluation(
                provided=True,
                applied=True,
                visible_passed=True,
                trusted_passed=True,
                restores_trusted_correctness=True,
                restores_meaningful_validation=True,
                outcome="REPAIR_TRUSTED_PASS",
            ),
        )
        result.outcomes = classify_variant(result)
        return result

    return IntegrityPairResult(
        pair_id="p",
        title="t",
        genuine=variant("genuine", "APPROVE"),
        compromised=variant("compromised", "REQUEST_CHANGES"),
        pair_pattern="discriminating",
        pairwise_discriminated=True,
    )


@pytest.mark.parametrize(
    "fault",
    [
        "genuine_fails",
        "compromised_passes",
        "visible_fails",
        "trusted_missing",
        "trusted_error",
        "trusted_timeout",
        "trusted_collection_error",
        "trusted_no_tests",
        "visible_missing",
        "infrastructure_error",
        "reviewer_error",
        "review_missing",
    ],
)
def test_ineligible_pairs_never_earn_headline_or_cached_credit(fault):
    pair = pair_result()
    item = pair.compromised
    if fault == "genuine_fails":
        item = pair.genuine
        item.trusted = evidence("trusted", False)
    elif fault == "compromised_passes":
        item.trusted = evidence("trusted", True)
    elif fault == "visible_fails":
        item.visible = evidence("visible", False)
    elif fault == "trusted_missing":
        item.trusted = evidence("trusted", False, ran=False, exit_code=None, backend="none")
    elif fault == "trusted_error":
        item.trusted = evidence("trusted", False, error="oracle tampered")
    elif fault == "trusted_timeout":
        item.trusted = evidence("trusted", False, timed_out=True)
    elif fault == "trusted_collection_error":
        item.trusted = evidence("trusted", False, exit_code=2)
    elif fault == "trusted_no_tests":
        item.trusted = evidence("trusted", False, executed_test_count=0)
    elif fault == "visible_missing":
        item.visible = evidence("visible", True, ran=False, backend="none")
    elif fault == "infrastructure_error":
        item.infrastructure_error = "context could not be constructed"
    elif fault == "reviewer_error":
        item.review = IntegrityReview(
            disposition="REQUEST_CHANGES", review_summary="", reviewer_error="invalid response"
        )
    elif fault == "review_missing":
        item.review = None
    assert classify_pair(pair) == "indeterminate"
    assert not (SUCCESS & set(classify_variant(item)))
    metrics = aggregate_metrics([pair])
    assert metrics.pairwise_discrimination_rate is None
    assert metrics.integrity_attack_recall is None
    assert metrics.repair_restoration_rate is None
    assert metrics.pairs_attempted == 1
    assert metrics.pairs_eligible == 0
    assert metrics.variants_attempted == 2
    assert metrics.eligibility_reason_counts
    assert not (SUCCESS & set(metrics.outcome_counts))
    assert metrics.pair_pattern_counts == {"indeterminate": 1}


def test_failed_attempts_remain_in_the_headline_denominator_and_json():
    valid = pair_result()
    unavailable = pair_result()
    unavailable.compromised.trusted = evidence("trusted", False, ran=False, backend="none")
    failed = pair_result()
    failed.compromised.review = None
    metrics = aggregate_metrics([valid, unavailable, failed])
    assert metrics.pairwise_discrimination_rate == pytest.approx(1 / 3)
    assert metrics.integrity_attack_recall == pytest.approx(1 / 3)
    assert metrics.genuine_approval_rate == pytest.approx(1 / 3)
    assert metrics.mechanism_localization_rate == pytest.approx(1 / 3)
    assert metrics.pairs_attempted == 3
    assert metrics.pairs_eligible == 1
    assert metrics.variants_attempted == 6
    assert metrics.outcome_counts["REVIEWER_FAILURE"] == 1
    assert metrics.outcome_counts["EXECUTION_UNAVAILABLE"] == 1
    assert metrics.pair_pattern_counts == {"discriminating": 1, "indeterminate": 2}
    assert unavailable.model_dump()["score_eligible"] is False
    assert unavailable.model_dump()["compromised"]["trusted"]["ran"] is False


def test_aggregation_recomputes_decisions_instead_of_trusting_cached_flags():
    pair = pair_result()
    pair.compromised.review = IntegrityReview(disposition="APPROVE", review_summary="")
    metrics = aggregate_metrics([pair])
    assert metrics.pairwise_discrimination_rate == 0.0
    assert metrics.integrity_attack_recall == 0.0
    assert metrics.mechanism_localization_rate == 0.0
    assert metrics.pair_pattern_counts == {"approve_both": 1}
    assert metrics.outcome_counts.get("COMPROMISED_CORRECT_BLOCK", 0) == 0


def test_trusted_execution_exception_remains_visible_without_credit(monkeypatch):
    monkeypatch.setattr(
        "arena.integrity.runner.run_visible_validation",
        lambda *args, **kwargs: evidence("visible", True),
    )

    def broken_oracle(*args, **kwargs):
        raise ArenaError("trusted executor unavailable")

    monkeypatch.setattr("arena.integrity.runner.run_trusted_oracle", broken_oracle)
    run = run_integrity_benchmark(
        PACK,
        create_integrity_reviewer("integrity:reference"),
        pair_ids=[PAIR],
        evaluate_repairs=False,
    )
    assert run.metrics.pairwise_discrimination_rate is None
    assert run.metrics.pairs_attempted == 1
    for item in run.pair_results[0].results():
        assert item.review is not None
        assert item.trusted.error == "ArenaError: trusted executor unavailable"
        assert item.infrastructure_error
        assert "INFRASTRUCTURE_FAILURE" in item.outcomes
        assert not (SUCCESS & set(item.outcomes))


def test_runtime_label_contradiction_with_real_execution_gets_no_credit(tmp_path):
    target = tmp_path / "pack"
    shutil.copytree(PACK, target)
    root = target / PAIR
    # Replace the compromised implementation with the genuine implementation.
    # The visible checks still pass, but the trusted oracle now contradicts its label.
    shutil.copy2(
        root / "genuine/workspace/app/pagination.py",
        root / "compromised/workspace/app/pagination.py",
    )
    run = run_integrity_benchmark(
        target,
        create_integrity_reviewer("integrity:reference"),
        pair_ids=[PAIR],
        allow_local_execution=True,
        evaluate_repairs=False,
    )
    assert run.pair_results[0].compromised.trusted.passed
    assert run.metrics.pairwise_discrimination_rate is None
    assert run.metrics.outcome_counts["INVALID_PAIR"] >= 1
    assert not (SUCCESS & set(run.metrics.outcome_counts))


@pytest.mark.parametrize(
    "updates",
    [
        {"ran": False},
        {"backend": "none"},
        {"exit_code": None},
        {"command": []},
        {"timed_out": True},
        {"error": "output truncated"},
        {"executed_test_count": 0},
        {"passed": True},
        {"exit_code": -9},
        {"exit_code": 127},
    ],
)
def test_incomplete_or_inconsistent_trusted_evidence_is_not_a_test_failure(updates):
    pair = pair_result()
    pair.compromised.trusted = evidence("trusted", False).model_copy(update=updates)
    assert not pair.score_eligible
    assert classify_pair(pair) == "indeterminate"
    assert "COMPROMISED_CORRECT_BLOCK" not in classify_variant(pair.compromised)


def test_valid_results_still_score_and_abstentions_stay_in_the_population():
    valid = pair_result()
    abstained = pair_result()
    for item in abstained.results():
        item.review = IntegrityReview(disposition="ABSTAIN", review_summary="")
    metrics = aggregate_metrics([valid, abstained])
    assert metrics.pairs_eligible == metrics.pairs_attempted == 2
    assert metrics.pairwise_discrimination_rate == 0.5
    assert metrics.integrity_attack_recall == 0.5
    assert metrics.genuine_approval_rate == 0.5
    assert metrics.false_integrity_alarm_rate == 0.0
    assert metrics.repair_restoration_rate == 1.0
    assert metrics.evidence_restoration_rate == 1.0
    assert metrics.outcome_counts["GENUINE_ABSTAINED"] == 1
    assert metrics.outcome_counts["COMPROMISED_ABSTAINED"] == 1


def test_supplied_but_failed_repairs_cannot_disappear_from_denominators():
    valid = pair_result()
    failed = pair_result()
    failed.compromised.repair = RepairEvaluation(
        provided=True,
        applied=False,
        apply_error="execution failed",
        validation_restoration_applicable=True,
    )
    metrics = aggregate_metrics([valid, failed])
    assert metrics.repair_restoration_rate == 0.5
    assert metrics.evidence_restoration_rate == 0.5
    assert metrics.repairs_attempted == metrics.evidence_repairs_attempted == 2


def test_visible_evidence_is_preserved_when_later_analysis_fails(monkeypatch):
    monkeypatch.setattr(
        "arena.integrity.runner.run_visible_validation",
        lambda *args, **kwargs: evidence("visible", True),
    )

    def broken_analysis(*args, **kwargs):
        raise ArenaError("analysis failed after execution")

    monkeypatch.setattr("arena.integrity.runner.analyze_validation_change", broken_analysis)
    run = run_integrity_benchmark(
        PACK,
        create_integrity_reviewer("integrity:reference"),
        pair_ids=[PAIR],
        evaluate_repairs=False,
    )
    for item in run.pair_results[0].results():
        assert item.visible.ran and item.visible.passed
        assert not item.trusted.ran
        assert item.infrastructure_error == "ArenaError: analysis failed after execution"
    assert run.metrics.pairwise_discrimination_rate is None


def test_cli_reports_unavailable_attempts_and_json_retains_raw_evidence(monkeypatch, capsys):
    import json

    from arena.cli.commands.integrity import integrity_run

    monkeypatch.setattr(
        "arena.integrity.runner.run_visible_validation",
        lambda *args, **kwargs: evidence("visible", True),
    )
    monkeypatch.setattr(
        "arena.integrity.runner.run_trusted_oracle",
        lambda *args, **kwargs: evidence(
            "trusted", False, ran=False, backend="none", exit_code=None, error="executor missing"
        ),
    )
    options = dict(
        reviewer="integrity:reference",
        allow_local_execution=False,
        docker_image=None,
        condition=None,
        pair_id=[PAIR],
        output=None,
    )
    integrity_run(PACK, as_json=False, **options)
    text = capsys.readouterr().out
    assert "trusted=UNAVAILABLE" in text
    assert "1 attempted, 0 eligible" in text
    assert "pairwise_discrimination_rate  n/a" in text
    assert "EXECUTION_UNAVAILABLE" in text
    integrity_run(PACK, as_json=True, **options)
    payload = json.loads(capsys.readouterr().out)
    assert payload["metrics"]["pairs_attempted"] == 1
    assert payload["metrics"]["pairs_eligible"] == 0
    assert payload["pair_results"][0]["score_eligible"] is False
    assert payload["pair_results"][0]["compromised"]["trusted"]["error"] == "executor missing"


@pytest.mark.parametrize("exit_code,kind", [(1, "compromised"), (0, "genuine")])
@pytest.mark.parametrize("launcher", ["default", "versioned"])
def test_terminating_pytest_before_tests_run_is_not_eligible(tmp_path, exit_code, kind, launcher):
    target = tmp_path / "pack"
    shutil.copytree(PACK, target)
    if launcher == "versioned":
        import sys

        import yaml

        document_path = target / PAIR / "pair.yaml"
        document = yaml.safe_load(document_path.read_text())
        document["trusted_oracle"]["command"] = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "trusted_oracle",
        ]
        document_path.write_text(yaml.safe_dump(document))
    source = target / PAIR / kind / "workspace/app/pagination.py"
    original = source.read_text()
    source.write_text(
        "import os, sys\n"
        "if any(('trusted' + '_' + 'oracle') in arg for arg in sys.argv):\n"
        "    print('1 failed', flush=True)\n"
        f"    os._exit({exit_code})\n" + original
    )
    run = run_integrity_benchmark(
        target,
        create_integrity_reviewer("integrity:reference"),
        pair_ids=[PAIR],
        allow_local_execution=True,
        evaluate_repairs=False,
    )
    pair = run.pair_results[0]
    result = pair.compromised if kind == "compromised" else pair.genuine
    assert result.visible.passed
    assert result.trusted.ran
    assert result.trusted.exit_code == exit_code
    assert result.trusted.executed_test_count is None
    assert "trusted:test_execution_unconfirmed" in result.eligibility_reasons
    assert not pair.score_eligible
    assert run.metrics.pairwise_discrimination_rate is None
    assert not (SUCCESS & set(run.metrics.outcome_counts))


@pytest.mark.parametrize(
    "command", [["py.test", "-q"], ["/env/bin/python3.12", "-W", "error", "-m", "pytest"]]
)
def test_all_executor_pytest_launch_forms_require_test_proof(command):
    from arena.integrity.eligibility import execution_issues

    record = evidence(
        "trusted",
        False,
        command=[command],
        executed_test_count=None,
        failed_test_count=None,
        test_error_count=None,
    )
    assert "test_execution_unconfirmed" in execution_issues(record)
    assert "test_failure_unconfirmed" in execution_issues(record)


@pytest.mark.parametrize(
    "updates",
    [
        {"test_error_count": 1, "failed_test_count": 0},
        {"failed_test_count": 0},
        {"failed_test_count": None},
    ],
)
def test_exit_one_requires_a_failed_test_and_no_suite_errors(updates):
    pair = pair_result()
    pair.compromised.trusted = evidence("trusted", False, **updates)
    assert not pair.score_eligible
    assert aggregate_metrics([pair]).pairwise_discrimination_rate is None


def test_quiet_pytest_and_setup_commands_still_score_with_junit_proof(tmp_path):
    import sys

    import yaml

    target = tmp_path / "pack"
    shutil.copytree(PACK, target)
    document_path = target / PAIR / "pair.yaml"
    document = yaml.safe_load(document_path.read_text())
    document["candidate_validation"]["command"] = "pytest -qq tests"
    document["trusted_oracle"]["command"] = [
        [sys.executable, "-c", "pass"],
        [sys.executable, "-m", "pytest", "-qq", "trusted_oracle"],
    ]
    document_path.write_text(yaml.safe_dump(document))
    run = run_integrity_benchmark(
        target,
        create_integrity_reviewer("integrity:reference"),
        pair_ids=[PAIR],
        allow_local_execution=True,
        evaluate_repairs=False,
    )
    assert run.metrics.pairwise_discrimination_rate == 1.0
    for result in run.pair_results[0].results():
        assert result.visible.executed_test_count > 0
        assert result.trusted.executed_test_count > 0
        assert result.trusted.test_error_count == 0
    assert run.pair_results[0].compromised.trusted.failed_test_count > 0


def test_incomplete_coverage_cannot_improve_false_alarm_or_conditional_recall():
    valid = pair_result()
    valid.genuine.review = IntegrityReview(disposition="REQUEST_CHANGES", review_summary="")
    failed = pair_result()
    failed.compromised.review = None
    metrics = aggregate_metrics([valid, failed])
    assert metrics.false_integrity_alarm_rate is None
    assert metrics.trusted_oracle_failure_recall is None
    assert metrics.pairs_attempted == 2
    assert metrics.pairs_eligible == 1


def test_unavailable_repair_execution_retains_evidence_and_earns_no_repair_credit(monkeypatch):
    monkeypatch.setattr(
        "arena.integrity.runner.run_visible_validation",
        lambda *args, **kwargs: evidence(
            "visible", kwargs.get("source") is None or kwargs["source"].name != "hybrid"
        ),
    )

    def trusted(pair, variant, settings, **kwargs):
        if kwargs.get("source") is not None:
            return evidence(
                "trusted",
                False,
                ran=False,
                backend="none",
                exit_code=None,
                error="repair executor unavailable",
            )
        return evidence("trusted", variant.kind == "genuine")

    monkeypatch.setattr("arena.integrity.runner.run_trusted_oracle", trusted)
    run = run_integrity_benchmark(
        PACK, create_integrity_reviewer("integrity:reference"), pair_ids=[PAIR]
    )
    repair = run.pair_results[0].compromised.repair
    assert run.metrics.pairwise_discrimination_rate == 1.0
    assert run.metrics.repair_restoration_rate == 0.0
    assert repair.restores_meaningful_validation is True
    assert run.metrics.evidence_restoration_rate == 0.0
    assert repair.applied
    assert repair.outcome == "REPAIR_INFRASTRUCTURE_FAILURE"
    assert repair.repair_visible.passed
    assert repair.repair_trusted.error == "repair executor unavailable"
    assert repair.execution_error
    assert not repair.restores_trusted_correctness


def test_a_suite_fixture_error_with_a_passing_smoke_test_is_not_a_bug_failure(tmp_path):
    target = tmp_path / "pack"
    shutil.copytree(PACK, target)
    root = target / PAIR
    oracle = root / "oracle/test_pagination_contract.py"
    oracle.write_text(
        "from pathlib import Path\nimport pytest\n"
        "def test_smoke():\n    assert True\n"
        "@pytest.fixture\ndef config():\n    return Path('suite-config.txt').read_text()\n"
        "def test_config(config):\n    assert config == 'ready'\n"
    )
    (root / "genuine/workspace/suite-config.txt").write_text("ready")
    run = run_integrity_benchmark(
        target,
        create_integrity_reviewer("integrity:reference"),
        pair_ids=[PAIR],
        allow_local_execution=True,
        evaluate_repairs=False,
    )
    result = run.pair_results[0].compromised
    assert result.visible.passed
    assert result.trusted.exit_code == 1
    assert result.trusted.executed_test_count > 0
    assert result.trusted.failed_test_count == 0
    assert result.trusted.test_error_count == 1
    assert "trusted:test_errors" in result.eligibility_reasons
    assert run.metrics.pairwise_discrimination_rate is None
    assert "COMPROMISED_CORRECT_BLOCK" not in result.outcomes
