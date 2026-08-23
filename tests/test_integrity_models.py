"""Schema and loader invariants for the CRA-Integrity track.

These are the structural guarantees the track rests on: the three trust zones
stay separated on disk, a pair cannot describe itself inconsistently, and the
ordinary BenchmarkCase family is untouched by any of it.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError as SchemaError

from arena.core.errors import ValidationError
from arena.core.models import BenchmarkCase
from arena.integrity.loader import is_validation_path, load_pair, load_pairs
from arena.integrity.models import (
    INTEGRITY_FAILURE_CLASSES,
    INTEGRITY_TAXONOMY_VERSION,
    CandidateValidationConfig,
    CandidateVariant,
    IntegrityManifest,
    IntegrityPair,
    TaskContract,
    TrustedOracleConfig,
)

PACK = Path("benchmark_sets/integrity_pilot_v0")


def _variant(**overrides):
    base = {"id": "genuine", "kind": "genuine"}
    base.update(overrides)
    return base


def _pair_document(**overrides):
    document = {
        "id": "example_001",
        "title": "Example",
        "stack": ["python"],
        "task_contract": {"title": "t", "statement": "s"},
        "candidate_validation": {"command": "pytest -q tests", "paths": ["tests"]},
        "trusted_oracle": {"command": "pytest -q trusted_oracle"},
        "genuine": _variant(),
        "compromised": _variant(
            id="compromised",
            kind="compromised",
            mechanisms=["ASSERTION_WEAKENING"],
            intent="INTENTIONAL_GAMING",
            mechanism_keywords=["weaker"],
        ),
    }
    document.update(overrides)
    return document


def test_the_shipped_pilot_pack_loads():
    pairs = load_pairs(PACK)
    assert len(pairs) == 8
    assert {pair.id for pair in pairs} == {path.name for path in PACK.iterdir() if path.is_dir()}


def test_every_pair_declares_a_taxonomy_mechanism_and_an_intent():
    for pair in load_pairs(PACK):
        assert pair.compromised.mechanisms
        for mechanism in pair.compromised.mechanisms:
            assert mechanism in INTEGRITY_FAILURE_CLASSES
        assert pair.compromised.intent is not None
        assert pair.genuine.mechanisms == []
        assert pair.genuine.intent is None


def test_the_pilot_covers_at_least_four_distinct_mechanisms():
    mechanisms = {m for pair in load_pairs(PACK) for m in pair.compromised.mechanisms}
    assert len(mechanisms) >= 4


def test_a_genuine_variant_may_not_declare_a_mechanism():
    document = _pair_document()
    document["genuine"] = _variant(mechanisms=["ASSERTION_WEAKENING"])
    with pytest.raises(SchemaError, match="must not declare a failure mechanism"):
        IntegrityPair.model_validate(document)


def test_a_compromised_variant_must_declare_mechanism_intent_and_keywords():
    document = _pair_document()
    document["compromised"] = _variant(id="compromised", kind="compromised")
    with pytest.raises(SchemaError, match="must declare at least one mechanism"):
        IntegrityPair.model_validate(document)


def test_the_two_variants_must_be_distinct():
    document = _pair_document()
    document["compromised"] = _variant(
        id="genuine",
        kind="compromised",
        mechanisms=["ASSERTION_WEAKENING"],
        intent="INTENTIONAL_GAMING",
        mechanism_keywords=["weaker"],
    )
    with pytest.raises(SchemaError, match="distinct ids"):
        IntegrityPair.model_validate(document)


def test_the_oracle_mount_may_not_be_the_oracle_source():
    document = _pair_document()
    document["trusted_oracle"] = {"command": "pytest", "dir": "oracle", "mount": "oracle"}
    with pytest.raises(SchemaError, match="mount must differ"):
        IntegrityPair.model_validate(document)


def test_pair_dir_cannot_be_supplied_by_pack_input():
    for value in (None, "somewhere"):
        with pytest.raises(SchemaError, match="internal runtime state"):
            IntegrityPair.model_validate(_pair_document(pair_dir=value))


def test_unknown_fields_are_rejected_everywhere():
    for model, document in (
        (TaskContract, {"title": "t", "statement": "s", "extra": 1}),
        (CandidateValidationConfig, {"command": "pytest", "paths": ["tests"], "extra": 1}),
        (TrustedOracleConfig, {"command": "pytest", "extra": 1}),
        (CandidateVariant, {"id": "a", "kind": "genuine", "extra": 1}),
        (IntegrityManifest, {"version": "v", "name": "n", "pairs": ["a"], "extra": 1}),
    ):
        with pytest.raises(SchemaError):
            model.model_validate(document)


def test_a_manifest_rejects_case_insensitive_duplicate_pairs():
    with pytest.raises(SchemaError, match="duplicate pair id"):
        IntegrityManifest.model_validate({"version": "v", "name": "n", "pairs": ["a_1", "A_1"]})


def test_validation_path_matching_is_prefix_aware():
    assert is_validation_path("tests/test_x.py", ["tests"])
    assert is_validation_path("tests", ["tests"])
    assert not is_validation_path("testsuite/x.py", ["tests"])
    assert not is_validation_path("app/tests.py", ["tests"])
    assert is_validation_path("fixtures/golden.json", ["fixtures/golden.json"])


def test_a_pair_that_ships_bytecode_is_rejected(tmp_path):
    source = PACK / "tenant_pagination_assertion_weakening_001"
    target = tmp_path / source.name
    import shutil

    shutil.copytree(source, target)
    cache = target / "genuine" / "workspace" / "app" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "pagination.cpython-312.pyc").write_bytes(b"\x00")
    with pytest.raises(ValidationError, match="build artifacts"):
        load_pair(target)


def test_a_pair_whose_workspace_contains_the_oracle_mount_is_rejected(tmp_path):
    source = PACK / "tenant_pagination_assertion_weakening_001"
    target = tmp_path / source.name
    import shutil

    shutil.copytree(source, target)
    planted = target / "compromised" / "workspace" / "trusted_oracle"
    planted.mkdir()
    (planted / "test_fake_contract.py").write_text("def test_ok():\n    assert True\n")
    with pytest.raises(ValidationError, match="trusted-oracle mount path"):
        load_pair(target)


def test_the_ordinary_benchmark_case_schema_is_untouched():
    # CRA-Integrity is additive. No integrity field may appear on BenchmarkCase.
    fields = set(BenchmarkCase.model_fields)
    assert not {name for name in fields if "integrity" in name or "oracle" in name}
    assert INTEGRITY_TAXONOMY_VERSION == "1.0"
