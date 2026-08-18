"""Phase 1B: strict, bounded external/reviewer/API schema contract.

Commit 1 added the strict base and bounds; the corrective commit completes the
inventory (every external model), the runtime-field rejection, identity/collection
uniqueness, real argv-collection limits, and the dedicated numeric limits.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from arena.core import limits
from arena.core.models import (
    AcceptableFinding,
    BenchmarkCase,
    CaseInput,
    CaseManifest,
    CaseOrigin,
    ExecutionConfig,
    Finding,
    GroundTruth,
    GroundTruthBug,
    GroundTruthFile,
    LineRange,
    MetricsConfig,
    ReviewResult,
    ScoreWeights,
    ScoringConfig,
    ValidationConfig,
    _StrictExternal,
)
from arena.server.schemas import CreateRunRequest


def _finding(**overrides):
    base = {
        "title": "t",
        "summary": "s",
        "category": "correctness",
        "severity": "high",
        "file": "app.py",
        "line_start": 1,
        "line_end": 2,
        "evidence": "e",
        "confidence": 0.9,
    }
    base.update(overrides)
    return base


_BUG = {
    "summary": "s",
    "files": [{"path": "a.py", "line_ranges": [{"start": 1, "end": 1}]}],
    "concepts": ["c"],
}
_GROUND_TRUTH = {"bugs": [dict(_BUG)]}
_CASE = {
    "id": "case1",
    "title": "t",
    "category": "correctness",
    "severity": "high",
    "stack": ["python"],
    "description": "d",
    "input": {},
    "ground_truth": {"bugs": [dict(_BUG)]},
}
_MANIFEST = {"version": "1", "name": "n", "cases": ["c1"]}


# Valid minimal kwargs per external/reviewer/API model, for the unknown-field sweep.
VALID_KWARGS = {
    LineRange: {"start": 1, "end": 2},
    GroundTruthFile: {"path": "a.py", "line_ranges": [{"start": 1, "end": 1}]},
    GroundTruthBug: dict(_BUG),
    AcceptableFinding: {"concepts": ["c"]},
    GroundTruth: _GROUND_TRUTH,
    CaseInput: {},
    ScoreWeights: {},
    ScoringConfig: {},
    ExecutionConfig: {},
    ValidationConfig: {},
    MetricsConfig: {},
    CaseOrigin: {},
    BenchmarkCase: _CASE,
    CaseManifest: _MANIFEST,
    Finding: _finding(),
    ReviewResult: {"findings": [], "overall_risk": "none", "review_summary": "x"},
    CreateRunRequest: {},
}


def test_unknown_field_sweep_covers_every_external_model():
    # The sweep below must include every strict external model defined in
    # arena.core.models; this guard fails if a new core _StrictExternal subclass is
    # added without a fixture here. Strict models defined elsewhere (for example the
    # importer's ImportSpec) are covered by their own module's tests.
    missing = [
        s.__name__
        for s in _StrictExternal.__subclasses__()
        if s.__module__ == "arena.core.models" and s not in VALID_KWARGS
    ]
    assert not missing, f"external models missing from VALID_KWARGS: {missing}"


@pytest.mark.parametrize("model", list(VALID_KWARGS), ids=lambda m: m.__name__)
def test_every_strict_model_rejects_unknown_fields(model):
    model(**VALID_KWARGS[model])  # valid baseline constructs
    with pytest.raises(ValidationError):
        model(**{**VALID_KWARGS[model], "definitely_not_a_field": 1})


def test_strict_rejects_type_coercion():
    with pytest.raises(ValidationError):
        ScoreWeights(concept_match="35")  # str -> float
    with pytest.raises(ValidationError):
        LineRange(start="1", end="2")  # str -> int
    with pytest.raises(ValidationError):
        ExecutionConfig(run_tests="true")  # str -> bool
    with pytest.raises(ValidationError):
        MetricsConfig(beta="1.0")


def test_defaults_are_validated_and_valid():
    assert ScoreWeights().total() == 100.0
    assert CaseInput().after_dir == "after"
    assert ScoringConfig().false_positive_penalty == 5
    assert MetricsConfig().beta == 1.0


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")])
def test_non_finite_numbers_rejected(bad):
    with pytest.raises(ValidationError):
        MetricsConfig(beta=bad)
    with pytest.raises(ValidationError):
        ScoringConfig(false_positive_penalty=bad)
    with pytest.raises(ValidationError):
        Finding(**_finding(confidence=bad))


def test_numeric_safety_bounds():
    with pytest.raises(ValidationError):
        MetricsConfig(beta=0)  # gt=0
    with pytest.raises(ValidationError):
        LineRange(start=5, end=3)  # end before start
    with pytest.raises(ValidationError):
        LineRange(start=1, end=limits.LINE_NUMBER_MAX + 1)  # magnitude cap
    with pytest.raises(ValidationError):
        Finding(**_finding(confidence=1.5))  # le=1
    with pytest.raises(ValidationError):
        ValidationConfig(max_false_positives=-1)


def test_collection_bounds_at_and_over_limit():
    files = [{"path": "a.py", "line_ranges": [{"start": 1, "end": 1}]}]
    # concepts exactly at the cap is accepted; one over is rejected. Distinct values
    # so the count cap (not the duplicate-rejection rule) is what is exercised.
    unique = [f"c{i}" for i in range(limits.CONCEPTS_PER_BUG)]
    GroundTruthBug(summary="s", files=files, concepts=unique)
    with pytest.raises(ValidationError):
        GroundTruthBug(summary="s", files=files, concepts=[*unique, "c-extra"])
    # findings list cap on reviewer output (duplicates allowed in reviewer output)
    ReviewResult(
        findings=[Finding(**_finding())] * limits.FINDINGS_PER_RESPONSE,
        overall_risk="low",
        review_summary="x",
    )
    with pytest.raises(ValidationError):
        ReviewResult(
            findings=[Finding(**_finding())] * (limits.FINDINGS_PER_RESPONSE + 1),
            overall_risk="low",
            review_summary="x",
        )


def test_string_length_at_and_over_limit():
    files = [{"path": "a.py", "line_ranges": [{"start": 1, "end": 1}]}]
    GroundTruthBug(summary="x" * limits.SUMMARY_LEN, files=files, concepts=["c"])
    with pytest.raises(ValidationError):
        GroundTruthBug(summary="x" * (limits.SUMMARY_LEN + 1), files=files, concepts=["c"])


def test_empty_required_strings_and_collections_rejected():
    files = [{"path": "a.py", "line_ranges": [{"start": 1, "end": 1}]}]
    with pytest.raises(ValidationError):
        GroundTruthBug(summary="", files=files, concepts=["c"])  # empty summary
    with pytest.raises(ValidationError):
        GroundTruthBug(summary="s", files=files, concepts=[])  # empty concepts
    with pytest.raises(ValidationError):
        GroundTruthBug(summary="s", files=[], concepts=["c"])  # empty files


def test_validation_config_rejects_duplicate_collections():
    ValidationConfig(protected_paths=["tests", "conftest.py"])  # distinct ok
    with pytest.raises(ValidationError):
        ValidationConfig(protected_paths=["tests", "tests"])
    with pytest.raises(ValidationError):
        ValidationConfig(structural_validators=["v", "v"])


def test_create_run_request_strict_and_bounded():
    CreateRunRequest()  # defaults valid
    with pytest.raises(ValidationError):
        CreateRunRequest(allow_local_execution="true")  # str -> bool
    with pytest.raises(ValidationError):
        CreateRunRequest(beta="1.0")  # str -> float
    with pytest.raises(ValidationError):
        CreateRunRequest(beta=float("nan"))
    with pytest.raises(ValidationError):
        CreateRunRequest(max_cost=-1)  # negative cost
    with pytest.raises(ValidationError):
        CreateRunRequest(max_wall_seconds=0)  # must be positive when supplied
    with pytest.raises(ValidationError):
        CreateRunRequest(unknown_field=1)  # forbid extra


# --- Corrective commit: completing the contract ----------------------------- #


def test_case_dir_cannot_be_set_from_input():
    BenchmarkCase(**_CASE)  # omitted case_dir is fine
    for bad in (None, "some/path"):
        with pytest.raises(ValidationError):
            BenchmarkCase(**{**_CASE, "case_dir": bad})


def test_case_dir_internal_assignment_and_serialization():
    case = BenchmarkCase(**_CASE)
    assert case.case_dir is None
    case.case_dir = Path("/tmp/x")  # loader assigns after validation (not validated)
    assert case.case_dir == Path("/tmp/x")
    assert "case_dir" not in case.model_dump()  # excluded from serialization
    assert "case_dir" not in case.model_dump(mode="json")


def test_duplicate_bug_ids_rejected_after_autoassignment():
    files = [{"path": "a.py", "line_ranges": [{"start": 1, "end": 1}]}]
    # first bug auto-assigned bug-1; second explicitly bug-1 -> collision
    with pytest.raises(ValidationError):
        GroundTruth(
            bugs=[
                {"summary": "s", "files": files, "concepts": ["c"]},
                {"id": "bug-1", "summary": "s2", "files": files, "concepts": ["d"]},
            ]
        )
    # case-fold collision is also rejected
    with pytest.raises(ValidationError):
        GroundTruth(
            bugs=[
                {"id": "Bug-1", "summary": "s", "files": files, "concepts": ["c"]},
                {"id": "bug-1", "summary": "s2", "files": files, "concepts": ["d"]},
            ]
        )
    # distinct ids are fine
    GroundTruth(
        bugs=[
            {"id": "a", "summary": "s", "files": files, "concepts": ["c"]},
            {"id": "b", "summary": "s2", "files": files, "concepts": ["d"]},
        ]
    )


def test_duplicate_semantic_collections_rejected():
    files = [{"path": "a.py", "line_ranges": [{"start": 1, "end": 1}]}]
    base = {"summary": "s", "files": files, "concepts": ["c"]}
    with pytest.raises(ValidationError):
        GroundTruthBug(**{**base, "concepts": ["c", "c"]})
    with pytest.raises(ValidationError):
        GroundTruthBug(**{**base, "must_mention": ["m", "m"]})
    with pytest.raises(ValidationError):
        GroundTruthBug(**{**base, "acceptable_fix_keywords": ["k", "k"]})
    # exact-duplicate line ranges in one file rejected
    with pytest.raises(ValidationError):
        GroundTruthFile(path="a.py", line_ranges=[{"start": 1, "end": 2}, {"start": 1, "end": 2}])
    # overlapping but non-identical ranges are allowed
    GroundTruthFile(path="a.py", line_ranges=[{"start": 1, "end": 3}, {"start": 2, "end": 4}])
    # duplicate acceptable_findings rejected
    with pytest.raises(ValidationError):
        GroundTruth(bugs=[base], acceptable_findings=[{"concepts": ["x"]}, {"concepts": ["x"]}])
    # duplicate stack entries on the case rejected
    with pytest.raises(ValidationError):
        BenchmarkCase(**{**_CASE, "stack": ["python", "python"]})


def test_manifest_requires_at_least_one_case_and_caps_maximum():
    CaseManifest(version="1", name="n", cases=["c1"])  # exact minimum
    with pytest.raises(ValidationError):
        CaseManifest(version="1", name="n", cases=[])  # empty rejected
    at_max = [f"case{i}" for i in range(limits.CASES_PER_MANIFEST)]
    CaseManifest(version="1", name="n", cases=at_max)  # exact maximum
    with pytest.raises(ValidationError):
        CaseManifest(version="1", name="n", cases=[*at_max, "caseextra"])  # one over


def test_argv_command_collection_limits_enforced():
    # string form still supported and bounded
    ExecutionConfig(test_command="pytest -q")
    # one argv command: >=1 non-empty token, <= ARGV_TOKENS
    ExecutionConfig(test_command=["pytest", "-q"])
    ExecutionConfig(test_command=["x"] * limits.ARGV_TOKENS)  # exact token cap
    with pytest.raises(ValidationError):
        ExecutionConfig(test_command=[])  # empty outer list
    with pytest.raises(ValidationError):
        ExecutionConfig(test_command=[""])  # empty token
    with pytest.raises(ValidationError):
        ExecutionConfig(test_command=["x"] * (limits.ARGV_TOKENS + 1))  # too many tokens
    # sequence form: >=1 command, <= ARGV_COMMANDS, each inner non-empty
    ExecutionConfig(test_command=[["pytest"], ["ruff", "check"]])
    ExecutionConfig(test_command=[["x"]] * limits.ARGV_COMMANDS)  # exact command cap
    with pytest.raises(ValidationError):
        ExecutionConfig(test_command=[[]])  # empty inner argv
    with pytest.raises(ValidationError):
        ExecutionConfig(test_command=[["x"]] * (limits.ARGV_COMMANDS + 1))  # too many commands


def test_dedicated_numeric_limits_boundaries():
    # test timeout: 1..TEST_TIMEOUT_SECONDS_MAX
    ExecutionConfig(timeout_seconds=limits.TEST_TIMEOUT_SECONDS_MAX)
    with pytest.raises(ValidationError):
        ExecutionConfig(timeout_seconds=limits.TEST_TIMEOUT_SECONDS_MAX + 1)
    with pytest.raises(ValidationError):
        ExecutionConfig(timeout_seconds=0)
    # beta: >0..BETA_MAX
    MetricsConfig(beta=limits.BETA_MAX)
    with pytest.raises(ValidationError):
        MetricsConfig(beta=limits.BETA_MAX + 1)
    # one score weight: 0..SCORE_WEIGHT_MAX
    ScoreWeights(concept_match=limits.SCORE_WEIGHT_MAX)
    with pytest.raises(ValidationError):
        ScoreWeights(concept_match=limits.SCORE_WEIGHT_MAX + 1)
    # penalties and the FP penalty cap: 0..PENALTY_MAX
    ScoringConfig(
        false_positive_penalty=limits.PENALTY_MAX,
        false_positive_penalty_cap=limits.PENALTY_MAX,
        invalid_json_penalty=limits.PENALTY_MAX,
    )
    with pytest.raises(ValidationError):
        ScoringConfig(false_positive_penalty=limits.PENALTY_MAX + 1)
    with pytest.raises(ValidationError):
        ScoringConfig(false_positive_penalty_cap=limits.PENALTY_MAX + 1)
    # API budgets: cost 0..API_COST_MAX, wall >0..API_WALL_SECONDS_MAX, beta >0..BETA_MAX
    CreateRunRequest(
        max_cost=limits.API_COST_MAX,
        max_wall_seconds=limits.API_WALL_SECONDS_MAX,
        beta=limits.BETA_MAX,
    )
    with pytest.raises(ValidationError):
        CreateRunRequest(max_cost=limits.API_COST_MAX + 1)
    with pytest.raises(ValidationError):
        CreateRunRequest(max_wall_seconds=limits.API_WALL_SECONDS_MAX + 1)
    with pytest.raises(ValidationError):
        CreateRunRequest(beta=limits.BETA_MAX + 1)


def test_case_origin_accepts_both_yaml_date_spellings_and_rejects_a_clock():
    """PyYAML resolves the two legal spellings of a date to different types.

    An unquoted `2026-03-14` becomes `datetime.date`; a quoted `"2026-03-14"`
    stays `str`. Under strict=True a `str` field rejects the first and a `date`
    field rejects the second, so a pack author's choice of quoting would decide
    whether their case loads. The before-validator normalizes the native date
    while still refusing a `datetime`, which carries a clock reading that a pack
    has no business pinning.
    """
    import datetime

    quoted = CaseOrigin(
        kind="derived_public", public_fix_date="2026-03-14", public_fix_date_basis="declared"
    )
    unquoted = CaseOrigin(
        kind="derived_public",
        public_fix_date=datetime.date(2026, 3, 14),
        public_fix_date_basis="declared",
    )

    assert quoted.public_fix_date == unquoted.public_fix_date == "2026-03-14"

    with pytest.raises(ValidationError):
        CaseOrigin(
            kind="derived_public",
            public_fix_date=datetime.datetime(2026, 3, 14, 1, 2, 3),
            public_fix_date_basis="declared",
        )
    with pytest.raises(ValidationError):
        CaseOrigin(
            kind="derived_public", public_fix_date="2026-3-14", public_fix_date_basis="declared"
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        # An authored case cannot also carry an upstream publication date.
        {"kind": "authored", "public_fix_date": "2026-03-14", "public_fix_date_basis": "declared"},
        # A case derived from public history must say when it became public.
        {"kind": "derived_public"},
        # A date with no stated basis is an unattributable claim.
        {"kind": "derived_public", "public_fix_date": "2026-03-14"},
    ],
)
def test_case_origin_rejects_incoherent_declarations(kwargs):
    with pytest.raises(ValidationError):
        CaseOrigin(**kwargs)
