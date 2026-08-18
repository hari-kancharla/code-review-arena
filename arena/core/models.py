"""Pydantic models shared across benchmark, reviewers, reports and API."""

from __future__ import annotations

from datetime import date as _date
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    computed_field,
    field_validator,
    model_validator,
)

from arena.core import limits
from arena.security.paths import SafeCaseId, SafeDirPath, SafeFilePath

Severity = Literal["critical", "high", "medium", "low"]
Risk = Literal["critical", "high", "medium", "low", "none"]
# A run's trust level. Only `complete` v2 runs are leaderboard-eligible; the rest
# are preserved and inspectable but excluded from normal comparisons.
RunStatus = Literal["complete", "partial", "invalid", "failed", "cancelled", "legacy"]
ExecutionBackend = Literal["docker", "trusted-local", "none"]
# How a reviewer response was parsed. `exact` is the default comparable contract;
# `invalid` is a reviewer-contract failure that still scores; `tolerant`/`repaired`
# are development-only salvage that make a run non-comparable by default.
ParseStatus = Literal["exact", "tolerant", "repaired", "invalid"]
# The fixed salvage-action vocabulary. Newly generated reviewer responses may only
# record these; arbitrary external action strings are rejected (legacy responses
# that predate the field carry none and load through the compatibility path).
PARSE_ACTIONS: frozenset[str] = frozenset(
    {
        "strip_markdown_fence",
        "extract_json_object",
        "remove_trailing_commas",
        "wrap_findings_list",
        "default_overall_risk",
        "default_review_summary",
        "drop_invalid_findings",
    }
)
_ParseAction = Annotated[str, StringConstraints(max_length=limits.PARSE_ACTION_LEN)]
# Bumped when the run JSON shape changes in a way that breaks comparability.
RUN_SCHEMA_VERSION = 2
# Hard caps that bound finding-to-bug matching so it cannot be driven into a
# pathological size by an adversarial pack or a reviewer spamming findings. They
# are generous relative to any real case or review, and rejection is at the
# schema layer (a response/pack exceeding them is invalid, not silently truncated).
MAX_BUGS_PER_CASE = limits.BUGS_PER_CASE
MAX_FINDINGS_PER_RESPONSE = limits.FINDINGS_PER_RESPONSE


class _StrictExternal(BaseModel):
    """Base for attacker-controlled input (pack files, reviewer output, API).

    Forbids unknown fields, disables type coercion (strict), validates defaults,
    and rejects NaN/inf. Strictness does NOT propagate to nested models in
    Pydantic, so every nested external model must inherit this base explicitly.
    Internally generated and persisted models keep plain BaseModel so legacy run
    JSON, reports, and database hydration continue to load.
    """

    model_config = ConfigDict(
        extra="forbid", strict=True, validate_default=True, allow_inf_nan=False
    )


# Bounded string element types for list fields (per-element length caps).
BoundedConcept = Annotated[str, StringConstraints(min_length=1, max_length=limits.CONCEPT_LEN)]
BoundedPhrase = Annotated[str, StringConstraints(min_length=1, max_length=limits.PHRASE_LEN)]
BoundedName = Annotated[str, StringConstraints(min_length=1, max_length=limits.IDENTIFIER_LEN)]
# An ISO calendar date as a REGEX-PINNED STRING, never a datetime.date.
# _StrictExternal sets strict=True, and PyYAML resolves an unquoted 2026-03-14 to
# datetime.date but a quoted "2026-03-14" to str -- so either native type would
# reject one of the two legal YAML spellings. A before-validator on each field
# normalizes a bare date to its ISO string so both load, while a datetime (which
# carries a clock reading the pack has no business pinning) is still rejected.
IsoDate = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$")]


def _iso_date_before(value: Any) -> Any:
    """Normalize PyYAML's native date to its ISO string; leave everything else."""
    if isinstance(value, _date) and not isinstance(value, datetime):
        return value.isoformat()
    return value


class LineRange(_StrictExternal):
    start: int = Field(ge=1, le=limits.LINE_NUMBER_MAX)
    end: int = Field(ge=1, le=limits.LINE_NUMBER_MAX)

    @field_validator("end")
    @classmethod
    def end_not_before_start(cls, value: int, info: Any) -> int:
        start = info.data.get("start")
        if start is not None and value < start:
            raise ValueError("line range end cannot precede start")
        return value


class GroundTruthFile(_StrictExternal):
    path: SafeFilePath
    line_ranges: list[LineRange] = Field(min_length=1, max_length=limits.LINE_RANGES_PER_FILE)

    @model_validator(mode="after")
    def _reject_duplicate_line_ranges(self) -> GroundTruthFile:
        # Reject exact-duplicate ranges only; overlapping but non-identical ranges
        # remain valid (they can carry distinct scoring meaning).
        keys = [(r.start, r.end) for r in self.line_ranges]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate line range in file")
        return self


class GroundTruthBug(_StrictExternal):
    # Stable identifier used to attribute repairs to specific bugs. Auto-assigned
    # as bug-1, bug-2, ... by GroundTruth when a case does not declare one.
    id: str = Field(default="", max_length=limits.IDENTIFIER_LEN)
    summary: Annotated[str, StringConstraints(min_length=1, max_length=limits.SUMMARY_LEN)]
    files: list[GroundTruthFile] = Field(min_length=1, max_length=limits.FILES_PER_BUG)
    concepts: list[BoundedConcept] = Field(min_length=1, max_length=limits.CONCEPTS_PER_BUG)
    must_mention: list[BoundedPhrase] = Field(
        default_factory=list, max_length=limits.MUST_MENTION_PER_BUG
    )
    acceptable_fix_keywords: list[BoundedPhrase] = Field(
        default_factory=list, max_length=limits.FIX_KEYWORDS_PER_BUG
    )

    @model_validator(mode="after")
    def _reject_duplicate_semantics(self) -> GroundTruthBug:
        # These are sets of meanings; an exact-duplicate entry adds nothing and is
        # rejected (not silently deduplicated) so a pack cannot hide a typo.
        for label, items in (
            ("concepts", self.concepts),
            ("must_mention", self.must_mention),
            ("acceptable_fix_keywords", self.acceptable_fix_keywords),
        ):
            if len(items) != len(set(items)):
                raise ValueError(f"duplicate entries in {label}")
        return self


# Back-compat alias: cases authored against the single-bug model import PrimaryBug.
PrimaryBug = GroundTruthBug


class AcceptableFinding(_StrictExternal):
    """A known-good extra finding that is scored neutral, not as a false positive."""

    path: SafeFilePath | None = None
    concepts: list[BoundedConcept] = Field(min_length=1, max_length=limits.CONCEPTS_PER_BUG)


class GroundTruth(_StrictExternal):
    bugs: list[GroundTruthBug] = Field(min_length=1, max_length=MAX_BUGS_PER_CASE)
    acceptable_findings: list[AcceptableFinding] = Field(
        default_factory=list, max_length=limits.ACCEPTABLE_FINDINGS_PER_CASE
    )

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_primary_bug(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "primary_bug" not in value:
            return value
        converted = dict(value)
        legacy = converted.pop("primary_bug")
        bugs = converted.get("bugs")
        if not bugs:
            converted["bugs"] = [legacy]
        elif bugs[0] != legacy:
            raise ValueError("primary_bug and bugs[0] disagree; declare the bug once in bugs")
        return converted

    @model_validator(mode="after")
    def _assign_and_validate_bug_ids(self) -> GroundTruth:
        for index, bug in enumerate(self.bugs):
            if not bug.id:
                bug.id = f"bug-{index + 1}"
        # Uniqueness runs AFTER auto-assignment, so an explicit "bug-1" colliding
        # with an auto-assigned "bug-1" is rejected. Case-fold collisions are also
        # rejected (matching the manifest case-id policy); never silently renamed.
        seen: dict[str, str] = {}
        for bug in self.bugs:
            folded = bug.id.casefold()
            if folded in seen:
                raise ValueError(
                    f"duplicate bug id after auto-assignment: {seen[folded]!r} and {bug.id!r}"
                )
            seen[folded] = bug.id
        return self

    @model_validator(mode="after")
    def _reject_duplicate_acceptable_findings(self) -> GroundTruth:
        keys = [(f.path, tuple(f.concepts)) for f in self.acceptable_findings]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate acceptable_findings entry")
        return self

    # Serialized for dashboards and tooling built on the single-bug shape.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def primary_bug(self) -> GroundTruthBug:
        return self.bugs[0]


class CaseInput(_StrictExternal):
    diff: SafeFilePath = "pr.diff"
    before_dir: SafeDirPath = "before"
    after_dir: SafeDirPath = "after"
    tests_dir: SafeDirPath | None = "tests"


# A pack-controlled command string (test_command / static_analysis_command). A
# bounded length and no separate regex: the executor tokenizes and runs without a
# shell, and an incomplete homemade grammar would be less safe than a length cap.
# These are size bounds only, NOT an executable allowlist.
_BoundedCommand = Annotated[str, StringConstraints(max_length=limits.COMMAND_STRING_LEN)]
# One argv token is non-empty and bounded; one argv command holds 1..ARGV_TOKENS
# tokens; a sequence holds 1..ARGV_COMMANDS commands. Empty outer or inner lists
# are rejected by the min_length=1 bounds.
_CommandToken = Annotated[str, StringConstraints(min_length=1, max_length=limits.TOKEN_LEN)]
_ArgvCommand = Annotated[list[_CommandToken], Field(min_length=1, max_length=limits.ARGV_TOKENS)]
_ArgvSequence = Annotated[list[_ArgvCommand], Field(min_length=1, max_length=limits.ARGV_COMMANDS)]


class ScoreWeights(_StrictExternal):
    concept_match: float = Field(default=35, ge=0, le=limits.SCORE_WEIGHT_MAX)
    file_match: float = Field(default=20, ge=0, le=limits.SCORE_WEIGHT_MAX)
    line_overlap: float = Field(default=15, ge=0, le=limits.SCORE_WEIGHT_MAX)
    severity_match: float = Field(default=10, ge=0, le=limits.SCORE_WEIGHT_MAX)
    fix_quality: float = Field(default=15, ge=0, le=limits.SCORE_WEIGHT_MAX)
    no_false_positives: float = Field(default=5, ge=0, le=limits.SCORE_WEIGHT_MAX)

    def total(self) -> float:
        return float(sum(self.model_dump().values()))


class ScoringConfig(_StrictExternal):
    weights: ScoreWeights = Field(default_factory=ScoreWeights)
    false_positive_penalty: float = Field(default=5, ge=0, le=limits.PENALTY_MAX)
    false_positive_penalty_cap: float = Field(default=15, ge=0, le=limits.PENALTY_MAX)
    invalid_json_penalty: float = Field(default=20, ge=0, le=limits.PENALTY_MAX)


class ExecutionConfig(_StrictExternal):
    run_tests: bool = False
    # One command string, one argv list, or a list of argv lists run in order;
    # the list forms are bounded in count and require non-empty tokens.
    test_command: _BoundedCommand | _ArgvCommand | _ArgvSequence | None = Field(default=None)
    timeout_seconds: int = Field(default=30, ge=1, le=limits.TEST_TIMEOUT_SECONDS_MAX)
    docker_image: (
        Annotated[str, StringConstraints(max_length=limits.DOCKER_IMAGE_REF_LEN)] | None
    ) = None
    run_static_analysis: bool = False
    static_analysis_command: _BoundedCommand | None = None


class ValidationConfig(_StrictExternal):
    patch_required: bool = False
    tests_required: bool = False
    structural_validators: list[BoundedName] = Field(
        default_factory=list, max_length=limits.STRUCTURAL_VALIDATORS_PER_CASE
    )
    max_false_positives: int = Field(default=0, ge=0, le=limits.FINDINGS_PER_RESPONSE)
    protected_paths: list[SafeFilePath] = Field(
        default_factory=list, max_length=limits.PROTECTED_PATHS_PER_CASE
    )
    # Detection completeness required for a deterministic pass. Defaults to
    # all_bugs: for single-bug cases this is identical to at_least_one, and for
    # multi-bug cases it correctly requires every seeded bug to be found.
    detection_requirement: Literal["all_bugs", "at_least_one"] = "all_bugs"

    @model_validator(mode="after")
    def _reject_duplicate_collections(self) -> ValidationConfig:
        for label, items in (
            ("structural_validators", self.structural_validators),
            ("protected_paths", self.protected_paths),
        ):
            if len(items) != len(set(items)):
                raise ValueError(f"duplicate entries in {label}")
        return self


class MetricsConfig(_StrictExternal):
    beta: float = Field(default=1.0, gt=0, le=limits.BETA_MAX)


ExposureCohort = Literal["pre_cutoff", "post_cutoff", "undetermined", "not_applicable"]


class CaseOrigin(_StrictExternal):
    """Where this case's answer came from, and when it first became public.

    This is a DISCLOSURE of prior-exposure risk, never a cleanliness claim. A
    case with ``kind: authored`` was written for this benchmark; ``derived_public``
    was built from a public upstream fix whose answer -- the patch, its regression
    test, and usually a pull-request discussion explaining it -- is plausibly in
    the pretraining corpus of any model under evaluation. ``unknown`` is the
    fail-closed default: absence of a declaration is absence of evidence, and is
    never read as a claim of safety.

    Distinct from arena/benchmark/contamination.py, which asks the unrelated
    question of whether a case leaks its own answer inside the pack.
    """

    kind: Literal["authored", "derived_public", "unknown"] = "unknown"
    public_fix_date: IsoDate | None = None
    public_fix_date_basis: Literal[
        "git_committer_date", "git_author_date", "declared", "min_of_signals", "unknown"
    ] = "unknown"
    source_label: (
        Annotated[str, StringConstraints(max_length=limits.IMPORT_SOURCE_LABEL_LEN)] | None
    ) = None
    upstream_ref: Annotated[str, StringConstraints(max_length=limits.UPSTREAM_REF_LEN)] | None = (
        None
    )

    _normalize_date = field_validator("public_fix_date", mode="before")(_iso_date_before)

    @model_validator(mode="after")
    def _check_kind_date_agreement(self) -> CaseOrigin:
        if self.kind == "authored" and self.public_fix_date is not None:
            raise ValueError("an authored case has no upstream public fix date")
        if self.kind == "derived_public" and self.public_fix_date is None:
            raise ValueError("a derived_public case must declare public_fix_date")
        if self.public_fix_date is not None and self.public_fix_date_basis == "unknown":
            raise ValueError("public_fix_date requires a public_fix_date_basis")
        return self


class BenchmarkCase(_StrictExternal):
    id: SafeCaseId
    title: Annotated[str, StringConstraints(min_length=1, max_length=limits.TITLE_LEN)]
    category: Annotated[str, StringConstraints(min_length=1, max_length=limits.CATEGORY_LEN)]
    severity: Severity
    stack: list[BoundedName] = Field(min_length=1, max_length=limits.STACK_ENTRIES)
    description: Annotated[str, StringConstraints(max_length=limits.DESCRIPTION_LEN)]
    input: CaseInput
    ground_truth: GroundTruth
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    # Optional so every already-shipped case keeps loading under extra="forbid".
    # Absent means undeclared, which is treated as unknown, never as clean.
    origin: CaseOrigin | None = None
    # Runtime state assigned by the loader after validation, not pack-controlled
    # input. Kept excluded from serialization; _reject_runtime_fields rejects any
    # attempt to set it from a case.yaml document (see below).
    case_dir: Path | None = Field(default=None, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _reject_runtime_fields(cls, value: Any) -> Any:
        # case_dir is internal runtime state; reject it from input with any value
        # (including null), while still allowing the loader's post-validation
        # attribute assignment (assignment is not validated).
        if isinstance(value, dict) and "case_dir" in value:
            raise ValueError("case_dir is internal runtime state and cannot be set from pack input")
        return value

    @model_validator(mode="after")
    def _reject_duplicate_stack(self) -> BenchmarkCase:
        if len(self.stack) != len(set(self.stack)):
            raise ValueError("duplicate entries in stack")
        return self


class CaseManifest(_StrictExternal):
    version: Annotated[str, StringConstraints(min_length=1, max_length=limits.IDENTIFIER_LEN)]
    name: Annotated[str, StringConstraints(min_length=1, max_length=limits.TITLE_LEN)]
    cases: list[SafeCaseId] = Field(min_length=1, max_length=limits.CASES_PER_MANIFEST)
    # Execution image applied to every case that does not set its own
    # docker_image. Lets a pack target one sandbox image in a single place
    # instead of repeating it across every case.yaml.
    default_docker_image: (
        Annotated[str, StringConstraints(max_length=limits.DOCKER_IMAGE_REF_LEN)] | None
    ) = None
    # The date this pack's contents first became publicly readable. Once the
    # repository holding it is public the PACK is its own exposure channel:
    # case.yaml carries must_mention and acceptable_fix_keywords, and
    # reference.patch carries the gold answer outright. When set, a case's
    # effective exposure date is min(origin.public_fix_date, published_date).
    published_date: IsoDate | None = None
    # Applied to every case whose origin.kind is still "unknown", mirroring
    # default_docker_image, so an authored pack can declare once rather than
    # repeating itself in ten case files.
    default_origin_kind: Literal["authored", "derived_public"] | None = None

    _normalize_published_date = field_validator("published_date", mode="before")(_iso_date_before)

    @model_validator(mode="after")
    def _reject_duplicate_case_ids(self) -> CaseManifest:
        seen: set[str] = set()
        for case_id in self.cases:
            if case_id in seen:
                raise ValueError(f"duplicate case id in manifest: {case_id}")
            seen.add(case_id)
        return self


class Finding(_StrictExternal):
    title: Annotated[str, StringConstraints(max_length=limits.FINDING_TITLE_LEN)]
    summary: Annotated[str, StringConstraints(max_length=limits.FINDING_SUMMARY_LEN)]
    category: Annotated[str, StringConstraints(max_length=limits.CATEGORY_LEN)]
    severity: Severity
    # Reviewer-controlled path. Bounded here; the reviewer-path containment
    # contract (prefix handling, traversal rejection) lives in the scorer's
    # normalization, which is the single place paths are interpreted.
    file: Annotated[str, StringConstraints(max_length=limits.IDENTIFIER_LEN * 8)]
    line_start: int = Field(ge=1, le=limits.LINE_NUMBER_MAX)
    line_end: int = Field(ge=1, le=limits.LINE_NUMBER_MAX)
    evidence: Annotated[str, StringConstraints(max_length=limits.EVIDENCE_LEN)]
    suggested_fix: Annotated[str, StringConstraints(max_length=limits.SUGGESTED_FIX_LEN)] | None = (
        None
    )
    suggested_patch: Annotated[str, StringConstraints(max_length=limits.PATCH_LEN)] | None = None
    replacement_code: (
        Annotated[str, StringConstraints(max_length=limits.REPLACEMENT_CODE_LEN)] | None
    ) = None
    patch_confidence: float | None = Field(default=None, ge=0, le=1)
    confidence: float = Field(ge=0, le=1)

    @field_validator("line_end")
    @classmethod
    def finding_end_not_before_start(cls, value: int, info: Any) -> int:
        start = info.data.get("line_start")
        if start is not None and value < start:
            raise ValueError("finding line_end cannot precede line_start")
        return value


class ReviewResult(_StrictExternal):
    findings: list[Finding] = Field(max_length=MAX_FINDINGS_PER_RESPONSE)
    # The reviewer's single complete repair for the whole case. This is the only
    # patch Arena applies; per-finding ``suggested_patch`` is advisory and never
    # applied (combining finding patches has ambiguous order/overlap semantics).
    proposed_patch: Annotated[str, StringConstraints(max_length=limits.PATCH_LEN)] | None = None
    overall_risk: Risk
    review_summary: Annotated[str, StringConstraints(max_length=limits.REVIEW_SUMMARY_LEN)]


class ReviewerCaseMetadata(BaseModel):
    id: str
    title: str
    category: str
    severity: Severity
    stack: list[str]
    description: str


class CaseContext(BaseModel):
    case: ReviewerCaseMetadata
    diff: str
    relevant_files: dict[str, str]
    context_truncated: bool = False
    omitted_files: list[str] = Field(default_factory=list)
    test_output: str = ""
    static_analysis_output: str = ""
    case_dir: Path | None = None


class ReviewerResponse(BaseModel):
    raw_response: str
    parsed_response: ReviewResult | None = None
    invalid_output: bool = False
    parse_attempts: int = 1
    # Persisted parse evidence (commit 3+). parse_status is the comparable contract
    # signal; parse_actions / finding counts / parse_error_summary record any
    # development-only salvage. Bounded and inspectable; raw_response is always kept.
    # Counts are None when no valid findings list was established (invalid output)
    # or on old responses that predate the fields (unknown, never fabricated).
    parse_status: ParseStatus = "exact"
    parse_actions: list[_ParseAction] = Field(
        default_factory=list, max_length=limits.PARSE_ACTIONS_MAX
    )
    input_finding_count: int | None = Field(default=None, ge=0, le=limits.JSON_MAX_NODES)
    retained_finding_count: int | None = Field(default=None, ge=0, le=limits.JSON_MAX_NODES)
    dropped_finding_count: int = Field(default=0, ge=0, le=limits.JSON_MAX_NODES)
    parse_error_summary: (
        Annotated[str, StringConstraints(max_length=limits.PARSE_ERROR_SUMMARY_LEN)] | None
    ) = None
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0
    tool_usage: list[str] = Field(default_factory=list)
    # True only for responses loaded from pre-parse-status saved JSON; lets the
    # invariant check below skip historical data while still fully validating any
    # newly produced response. Never serialized.
    derived_from_legacy: bool = Field(default=False, exclude=True, repr=False)

    @model_validator(mode="before")
    @classmethod
    def _derive_parse_status(cls, value: Any) -> Any:
        # Old saved responses predate parse_status: derive it so they keep loading
        # without rewriting any file, and mark them legacy so the invariant check
        # tolerates their missing action/count evidence. invalid_output -> invalid;
        # otherwise by the legacy attempt count (1 exact, 2 tolerant, >=3 repaired).
        if not isinstance(value, dict) or "parse_status" in value:
            return value
        converted = dict(value)
        converted["derived_from_legacy"] = True
        if value.get("invalid_output"):
            converted["parse_status"] = "invalid"
        else:
            attempts = value.get("parse_attempts", 1)
            if attempts <= 1:
                converted["parse_status"] = "exact"
            elif attempts == 2:
                converted["parse_status"] = "tolerant"
            else:
                converted["parse_status"] = "repaired"
        return converted

    @model_validator(mode="after")
    def _check_evidence_invariants(self) -> ReviewerResponse:
        # Legacy responses load as-is; full invariants apply only to new responses.
        if self.derived_from_legacy:
            return self
        if set(self.parse_actions) - PARSE_ACTIONS:
            raise ValueError("parse_actions contains an action outside the fixed vocabulary")
        if self.parse_status == "invalid":
            if not self.invalid_output:
                raise ValueError("invalid parse_status requires invalid_output=True")
            if self.parsed_response is not None:
                raise ValueError("invalid response must not carry a parsed_response")
            if self.retained_finding_count:
                raise ValueError("invalid response cannot retain findings")
            return self
        if self.invalid_output or self.parsed_response is None:
            raise ValueError(f"{self.parse_status} response must be valid with a parsed_response")
        parsed_count = len(self.parsed_response.findings)
        if self.parse_status == "exact":
            if self.parse_attempts != 1 or self.parse_actions or self.dropped_finding_count:
                raise ValueError("exact response must have attempts=1, no actions, no drops")
        elif self.parse_status == "tolerant":
            if not self.parse_actions or self.dropped_finding_count:
                raise ValueError("tolerant response must record actions and drop nothing")
        elif self.parse_status == "repaired" and not self.parse_actions:
            raise ValueError("repaired response must record at least one action")
        if self.retained_finding_count is not None and self.retained_finding_count != parsed_count:
            raise ValueError("retained_finding_count must match the parsed findings")
        if (
            self.input_finding_count is not None
            and self.retained_finding_count is not None
            and self.input_finding_count != self.retained_finding_count + self.dropped_finding_count
        ):
            raise ValueError("input_finding_count must equal retained + dropped")
        return self


class ScoreBreakdown(BaseModel):
    concept_match: float = 0
    file_match: float = 0
    line_overlap: float = 0
    severity_match: float = 0
    fix_quality: float = 0
    no_false_positives: float = 0
    false_positive_penalty: float = 0
    invalid_json_penalty: float = 0
    total: float = 0


# A finding's status once execution evidence is in. "detected" is the pre-execution
# value; the runner upgrades it to repair_validated / detected_but_unrepaired.
FindingEvidence = Literal[
    "repair_validated", "detected_but_unrepaired", "unsupported", "neutral", "detected"
]
# How deeply a validated repair was challenged. basic = required tests passed;
# strong = tests plus structural validators passed. (adversarial/high, backed by
# per-case mutation and differential checks, are measured by certify-pack today.)
RepairConfidence = Literal["unvalidated", "basic", "strong"]
# A case's overall outcome after execution.
CaseStatus = Literal[
    "complete_repair",
    "partial_repair",
    "detected_but_unrepaired",
    "no_detection",
    "tampering",
    "inconclusive",
    "review_only",
]


class ScoredFinding(BaseModel):
    finding: Finding
    is_true_positive: bool
    matched_bug_index: int | None = None
    is_neutral: bool = False
    false_positive_reason: str | None = None
    evidence_status: FindingEvidence | None = None


class BugRepair(BaseModel):
    """Per-bug attribution: was it found by the reviewer, and did the patch fix it?"""

    bug_id: str
    detected: bool
    repaired: bool


class DeterministicCaseScore(BaseModel):
    case_id: str
    detected_bug: bool
    localized_correctly: bool
    patch_provided: bool
    patch_applied: bool
    tests_ran: bool
    tests_passed: bool | None = None
    structural_validation_ran: bool
    structural_validation_passed: bool | None = None
    true_positive_count: int
    false_positive_count: int
    false_negative_count: int
    precision: float
    recall: float
    f1: float
    f_beta: float
    patch_apply_score: float
    execution_score: float
    structural_score: float
    deterministic_pass: bool
    # Whether this case has an executable validation gate (tests and/or structural
    # validators configured). Cases with no gate cannot confirm a repair, so they
    # are excluded from validated_case_rate rather than counted as passes.
    validation_eligible: bool = False
    failure_reasons: list[str] = Field(default_factory=list)


class DeterministicMetrics(BaseModel):
    detection_precision: float = 0.0
    detection_recall: float = 0.0
    detection_f1: float = 0.0
    detection_f_beta: float = 0.0
    # DEPRECATED: validated_precision/f1/f_beta mix a case-level numerator
    # (validated cases) with a finding-level denominator (false-positive
    # findings), so they are not unit-coherent and are not comparable across
    # packs. They remain only so existing run JSON keeps loading. Use
    # validated_case_rate as the case-level repair metric instead; the unit-clean
    # per-finding/per-bug repair metrics arrive with the evidence layer.
    validated_precision: float = 0.0
    validated_recall: float = 0.0
    validated_f1: float = 0.0
    validated_f_beta: float = 0.0
    beta: float
    # None, not 0.0, when the run had no execution-backed case to measure: a
    # rate over an empty denominator is not "repaired nothing", it is "not
    # measured", and publishing 0.0 fabricates a result. The default stays 0.0 so
    # run JSON written before this distinction still loads unchanged.
    deterministic_pass_rate: float | None = 0.0
    # Canonical, unit-coherent case-level repair metric: validated cases over
    # eligible cases (numerically equal to deterministic_pass_rate, which stays
    # as a legacy alias). This is the default leaderboard sort.
    validated_case_rate: float | None = 0.0
    # Wilson 95% confidence interval for validated_case_rate. Wide at the small
    # pack sizes here (10 cases), so overlapping intervals mean two reviewers are
    # not reliably ranked. None when there are no validation-eligible cases.
    validated_case_rate_ci_low: float | None = None
    validated_case_rate_ci_high: float | None = None
    # The denominator behind validated_case_rate, published so a reader never has
    # to guess what the rate is "out of", plus the cases it could not cover
    # (those with no runnable suite -- judged by a structural validator, or not
    # judged at all). Together they account for every scored case. Optional with
    # defaults: run JSON written before these existed still loads.
    validated_eligible_case_count: int | None = None
    non_execution_backed_case_count: int | None = None
    # Evidence-derived dimensions (see docs/metrics.md, "Evidence dimensions"):
    # Repair Success -- cases the patch fully repaired (case unit).
    complete_repair_rate: float | None = 0.0
    # Review Accuracy -- cases where every seeded bug was detected (case unit).
    bug_completeness_rate: float = 0.0
    # Review Trustworthiness -- of the findings that count (excluding neutral
    # acceptable findings), the fraction that matched a real bug (finding unit).
    supported_claim_rate: float | None = None
    localization_rate: float | None = None
    patch_apply_rate: float | None = None
    test_pass_rate: float | None = None
    structural_pass_rate: float | None = None
    false_positives_per_case: float
    cost_per_validated_fix: float | None = None
    latency_per_case_ms: float

    @model_validator(mode="before")
    @classmethod
    def load_legacy_metric_names(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        converted = dict(value)
        converted.setdefault("detection_precision", converted.get("precision", 0.0))
        converted.setdefault("detection_recall", converted.get("recall", 0.0))
        converted.setdefault("detection_f1", converted.get("f1", 0.0))
        converted.setdefault("detection_f_beta", converted.get("f_beta", 0.0))
        converted.setdefault("validated_precision", converted.get("precision", 0.0))
        converted.setdefault("validated_recall", converted.get("recall", 0.0))
        converted.setdefault("validated_f1", converted.get("f1", 0.0))
        converted.setdefault("validated_f_beta", converted.get("f_beta", 0.0))
        converted.setdefault("validated_case_rate", converted.get("deterministic_pass_rate", 0.0))
        converted.setdefault("latency_per_case_ms", converted.get("latency_per_case", 0.0))
        converted.setdefault("cost_per_validated_fix", converted.get("cost_per_true_positive"))
        return converted


class CaseResult(BaseModel):
    case_id: str
    title: str
    category: str
    severity: Severity
    ground_truth_summary: str
    response: ReviewerResponse
    scored_findings: list[ScoredFinding]
    breakdown: ScoreBreakdown
    score: float
    review_quality_score: float | None = None
    bug_found: bool
    correct_file: bool
    correct_line: bool
    line_match: str
    bugs_total: int = 1
    bugs_matched: int = 0
    false_positive_count: int
    context_truncated: bool = False
    test_output: str = ""
    deterministic_case_score: DeterministicCaseScore | None = None
    # Training-data exposure disclosure (see arena/benchmark/exposure.py).
    # Persisted PER CASE so a third party can recompute the whole analysis from a
    # stored run.json at a different cutoff or guard band, with no reviewer calls
    # and no access to the pack.
    exposure_date: str | None = None
    exposure_date_basis: str | None = None
    exposure_cohort: ExposureCohort | None = None
    exposure_cohort_reason: str | None = None
    patch_provided: bool = False
    patch_applied: bool = False
    patch_error: str | None = None
    touched_files: list[str] = Field(default_factory=list)
    # Authoritative Git-patch evidence (Phase 1D). touched_files and patch_error now
    # reflect the actual post-application Git tree; these record its identity. All are
    # optional/defaulted so old saved runs load unchanged.
    patch_sha256: str | None = None
    patch_object_format: str | None = None
    patch_baseline_tree: str | None = None
    patch_result_tree: str | None = None
    # Bounded, human-readable Git diagnostic; never contains patch bytes, file
    # contents, secrets, or private paths. `patch_error` stays the machine reason.
    git_diagnostic: str | None = None
    patch_added: list[str] = Field(default_factory=list)
    patch_deleted: list[str] = Field(default_factory=list)
    patch_mode_changes: list[str] = Field(default_factory=list)
    tests_ran: bool = False
    tests_passed: bool | None = None
    test_stdout_tail: str = ""
    test_stderr_tail: str = ""
    validators_run: list[str] = Field(default_factory=list)
    validators_passed: bool | None = None
    validator_results: list[dict[str, Any]] = Field(default_factory=list)
    deterministic_pass: bool | None = None
    failure_reasons: list[str] = Field(default_factory=list)
    raw_suggested_patch: str | None = None
    # Evidence attribution (populated in patch/full mode).
    case_status: CaseStatus | None = None
    bug_repairs: list[BugRepair] = Field(default_factory=list)
    repair_confidence: RepairConfidence | None = None
    # How this case actually executed (docker / trusted-local / none).
    execution_backend: ExecutionBackend = "none"
    # True when execution was attempted for this case but the backend was
    # unavailable (e.g. Docker required but not running, or local execution
    # disabled): the repair could not be judged, so the verdict is not the
    # reviewer's fault. Used to flag runs whose scores are not a fair measurement.
    execution_unavailable: bool = False


class CohortResult(BaseModel):
    """One exposure cohort's slice of validated_case_rate."""

    cohort: ExposureCohort
    eligible_case_count: int
    validated_case_count: int
    validated_case_rate: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None


class ExposureMetrics(BaseModel):
    """Cohort composition, and the pre/post difference only when it is powered.

    This is NOT a measurement of memorization. `exposure_gap` describes a split
    that is confounded with case difficulty, repository, language and era,
    because cases are not randomly assigned to cohorts -- at small pack sizes
    cohort membership is close to collinear with which repository a case came
    from, which is why `source_composition` is published beside it.

    `publishable` is false whenever `suppression_reasons` is non-empty, and the
    cohort counts are published either way: the composition is a fact about the
    run, and hiding it would be the opposite of the point.
    """

    analysis_version: int = 1
    declared_cutoff: str | None = None
    cutoff_basis: str | None = None
    cutoff_grace_days: int | None = None
    reviewer_retrieval: str = "unknown"
    cohorts: list[CohortResult] = Field(default_factory=list)
    cohort_reason_counts: dict[str, int] = Field(default_factory=dict)
    source_composition: dict[str, dict[str, int]] = Field(default_factory=dict)
    exposure_gap: float | None = None
    exposure_gap_ci_low: float | None = None
    exposure_gap_ci_high: float | None = None
    min_detectable_gap: float | None = None
    publishable: bool = False
    suppression_reasons: list[str] = Field(default_factory=list)
    exposure_analysis_key: str = ""


class RunMetadata(BaseModel):
    prompt_version: str
    benchmark_version: str
    temperature: float = 0.0
    git_commit: str | None = None
    # True when the working tree had uncommitted changes at run time, so the
    # recorded git_commit does not fully describe the code that ran; None when
    # the state could not be determined (no git).
    git_dirty: bool | None = None
    # True when the reviewer was given pre-patch test/static-analysis output
    # (an openly test-assisted run, not a blind code review).
    test_assisted: bool = False
    # True when the reviewer could reach the pack's answer key while reviewing --
    # a custom-command wrapper runs as a host process and can simply read
    # reference.patch, the hidden tests and case.yaml, and reference-patch does so
    # by design. Such a score measures filesystem access, not review skill: a
    # wrapper that reads the oracle and echoes it scores a perfect 1.000 on every
    # metric. Default leaderboard eligibility therefore requires this to be False.
    # None on runs written before the field existed (unknown, treated as unsafe).
    reviewer_oracle_reachable: bool | None = None
    # An operator-declared, UNVERIFIABLE claim about the evaluated model. The
    # harness never infers a cutoff from a model id and never asserts one itself;
    # it records the claim with its basis and citation so a reader can judge it.
    model_knowledge_cutoff: IsoDate | None = None
    model_knowledge_cutoff_basis: Literal["vendor_documented", "operator_estimate"] | None = None
    model_knowledge_cutoff_source: (
        Annotated[str, StringConstraints(max_length=limits.MODEL_CUTOFF_SOURCE_LEN)] | None
    ) = None
    cutoff_grace_days: int | None = None
    # Whether the reviewer could search the web while reviewing. Live retrieval
    # defeats any cutoff argument entirely, and for a custom-command reviewer
    # running as a host process the harness cannot prove otherwise -- so the
    # DEFAULT is the untrusted value. Disclosure only: deliberately not part of
    # leaderboard eligibility, because gating on a self-declared field rewards
    # declaring whatever is convenient.
    reviewer_retrieval: Literal["none", "enabled", "unknown"] = "unknown"
    pack_checksum: str | None = None
    # True/False when the pack ships a pack.sha256 to compare against; None otherwise.
    # This is SELF-consistency only: pack.sha256 lives inside the pack, so an edited
    # pack with a regenerated hash still verifies True. It is not a trust anchor.
    pack_checksum_verified: bool | None = None
    # True only when the run was checked against a digest supplied out of band
    # (--expected-pack-sha256) and matched. This is the external trust anchor that
    # default leaderboard eligibility requires; a regenerated internal pack.sha256
    # cannot set it.
    pack_digest_externally_verified: bool = False
    # Immutable-snapshot evidence (Phase 1C). The run executed from a sealed
    # snapshot of the source pack; pack_checksum above is the snapshot content
    # checksum. These describe the snapshot, never its temporary path, and are None
    # on old runs. snapshot_integrity_verified records that the snapshot was
    # re-verified before reports were sealed.
    snapshot_file_count: int | None = None
    snapshot_total_bytes: int | None = None
    snapshot_manifest_version: int | None = None
    snapshot_integrity_verified: bool | None = None
    # Full-seal digest over the complete snapshot manifest (every file AND directory,
    # including the root pack.sha256, with modes) -- distinct from pack_checksum, which
    # is the public content checksum. None on old runs.
    snapshot_manifest_digest: str | None = None
    # Per-status case counts (exact/tolerant/repaired/invalid). Empty on old runs.
    reviewer_parse_status_counts: dict[str, int] = Field(default_factory=dict)
    # Fail-closed comparability signal: False when every case is exact or invalid
    # (Arena did not reinterpret any output), True when any case was tolerant or
    # repaired, None for old runs where exactness is unknown. None and True are not
    # default-comparable; invalid alone does NOT make a run non-comparable.
    non_exact_output_used: bool | None = None

    @model_validator(mode="after")
    def _cutoff_claim_requires_attribution(self) -> RunMetadata:
        """A cutoff without a basis and a citation is not a claim, it is a number.

        Recording one anyway would let a run publish a cohort split resting on an
        assertion nobody can check or attribute.
        """
        declared = (
            self.model_knowledge_cutoff,
            self.model_knowledge_cutoff_basis,
            self.model_knowledge_cutoff_source,
        )
        if any(v is not None for v in declared) and not all(v is not None for v in declared):
            raise ValueError("a knowledge-cutoff claim must carry both its basis and its source")
        return self


class RunResult(BaseModel):
    run_id: str
    benchmark_set: str
    reviewer: str
    model: str | None
    started_at: datetime
    completed_at: datetime
    metadata: RunMetadata
    case_results: list[CaseResult]
    total_score: float
    budget_stopped_reason: str | None = None
    skipped_case_ids: list[str] = Field(default_factory=list)
    # Run validity and coverage. schema_version defaults to 1 so runs loaded from
    # pre-v2 JSON (which never carried these fields) read as legacy v1 and are
    # excluded from the v2 leaderboard; run_benchmark sets RUN_SCHEMA_VERSION.
    schema_version: int = 1
    run_status: RunStatus = "complete"
    execution_backend: ExecutionBackend = "none"
    eligible_case_count: int = 0
    completed_case_count: int = 0
    failed_case_count: int = 0
    skipped_case_count: int = 0
    coverage_rate: float = 1.0
    mode: Literal["review", "patch", "full"] = "review"
    beta: float = 1.0
    deterministic_metrics: DeterministicMetrics | None = None
    # A SIBLING of deterministic_metrics, never a field inside it:
    # DeterministicMetrics carries a mode="before" legacy-name compatibility path
    # that must not be disturbed by a new nested object.
    exposure_metrics: ExposureMetrics | None = None
    bugs_found: int
    correct_files: int
    correct_lines: int
    false_positives: int
    total_cost: float
    total_latency_ms: int

    @property
    def case_count(self) -> int:
        return len(self.case_results)

    @model_validator(mode="after")
    def _check_parse_evidence_consistency(self) -> RunResult:
        # Old runs (non_exact_output_used is None) are legacy/unknown and bypass this.
        # New runs must have parse-status counts that agree with their case results.
        if self.metadata.non_exact_output_used is None:
            return self
        actual: dict[str, int] = {}
        for case in self.case_results:
            status = case.response.parse_status
            actual[status] = actual.get(status, 0) + 1
        recorded = self.metadata.reviewer_parse_status_counts
        if set(recorded) - {"exact", "tolerant", "repaired", "invalid"}:
            raise ValueError("reviewer_parse_status_counts has an unknown status key")
        if any(count < 0 for count in recorded.values()):
            raise ValueError("reviewer_parse_status_counts has a negative count")
        if {k: v for k, v in recorded.items() if v} != {k: v for k, v in actual.items() if v}:
            raise ValueError("reviewer_parse_status_counts disagree with case_results")
        if sum(recorded.values()) != len(self.case_results):
            raise ValueError("reviewer_parse_status_counts total must equal case count")
        expected_non_exact = any(status in {"tolerant", "repaired"} for status in actual)
        if self.metadata.non_exact_output_used != expected_non_exact:
            raise ValueError("non_exact_output_used disagrees with case parse statuses")
        return self

    @model_validator(mode="after")
    def _check_exposure_cohort_consistency(self) -> RunResult:
        """An edited run.json cannot move or drop a case between cohorts.

        Fires only when exposure_metrics is present, so every historical run and
        every review-mode run passes through unchanged.
        """
        exposure = self.exposure_metrics
        if exposure is None:
            return self
        if self.deterministic_metrics is None:
            raise ValueError("exposure metrics require deterministic metrics")
        counted = sum(item.eligible_case_count for item in exposure.cohorts)
        expected = self.deterministic_metrics.validated_eligible_case_count
        if counted != expected:
            raise ValueError(
                f"exposure cohorts cover {counted} cases but the run reports "
                f"{expected} validation-eligible cases"
            )
        sizes = {item.cohort: item.eligible_case_count for item in exposure.cohorts}
        powered = (
            sizes.get("pre_cutoff", 0) >= limits.MIN_COHORT_CASES
            and sizes.get("post_cutoff", 0) >= limits.MIN_COHORT_CASES
        )
        if exposure.exposure_gap is not None and not powered:
            raise ValueError(
                "an exposure gap cannot be published for cohorts below "
                f"{limits.MIN_COHORT_CASES} cases"
            )
        if exposure.suppression_reasons and exposure.publishable:
            raise ValueError("a suppressed exposure analysis cannot be marked publishable")
        return self
