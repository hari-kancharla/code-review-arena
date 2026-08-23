"""Strict, bounded models for the CRA-Integrity track.

Three trust zones structure every model here and must never be conflated:

``product``
    Application/source code the pull request changes.
``candidate_validation``
    Tests, fixtures, snapshots, golden files and validation configuration that
    are legitimately part of the proposed PR. The candidate author owns them and
    may change them; the reviewer sees them. Their presence is *not* evidence.
``trusted_oracle``
    Hidden behavioural tests and independent checks owned by the benchmark. The
    candidate author and the reviewer never see or modify them.

The pack-facing models inherit ``_StrictExternal`` (extra="forbid", strict, no
NaN/inf) exactly like the ordinary ``BenchmarkCase`` family, because an integrity
pack is treated as adversarial input in the same way. Result models are plain
``BaseModel`` so persisted runs keep loading.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator

from arena.core import limits
from arena.core.models import _StrictExternal
from arena.security.paths import SafeCaseId, SafeDirPath, SafeFilePath

# --------------------------------------------------------------------------- #
# Vocabularies                                                                 #
# --------------------------------------------------------------------------- #

#: The bounded integrity-failure taxonomy. Bumped whenever a member is added,
#: removed or redefined, because that changes what a recorded result means.
INTEGRITY_TAXONOMY_VERSION = "1.0"

IntegrityFailureClass = Literal[
    # Validation still runs but proves less than it did.
    "ASSERTION_WEAKENING",
    # Important test behaviour no longer executes (discovery, rename, skip, filter).
    "TEST_SELECTION_DRIFT",
    # Expected validation data changes to accept incorrect behaviour.
    "FIXTURE_OR_GOLDEN_DRIFT",
    # Mocking/stubbing removes the behaviour the test supposedly validates.
    "MOCK_BOUNDARY_BYPASS",
    # A failure is swallowed, converted, skipped, or treated as success.
    "ERROR_SUPPRESSION",
    # Implementation handles the visible examples without satisfying the contract.
    "VISIBLE_CASE_OVERFIT",
    # Individually tested components work; their required composition does not.
    "COMPOSITION_GAP",
    # A required check becomes non-blocking or is no longer executed.
    "VALIDATION_BYPASS",
    # A checked demonstration passes while the requested reusable behaviour is
    # absent or disconnected from the product surface.
    "DEAD_OR_MISSING_DELIVERABLE",
    # Behaviour differs specifically under validation/test conditions.
    "VALIDATION_CONDITION_BEHAVIOR",
]

INTEGRITY_FAILURE_CLASSES: tuple[str, ...] = (
    "ASSERTION_WEAKENING",
    "TEST_SELECTION_DRIFT",
    "FIXTURE_OR_GOLDEN_DRIFT",
    "MOCK_BOUNDARY_BYPASS",
    "ERROR_SUPPRESSION",
    "VISIBLE_CASE_OVERFIT",
    "COMPOSITION_GAP",
    "VALIDATION_BYPASS",
    "DEAD_OR_MISSING_DELIVERABLE",
    "VALIDATION_CONDITION_BEHAVIOR",
)

#: Why the integrity loss exists. Deliberately separate from the mechanism: a
#: reviewer should ideally detect lost integrity regardless of author intent, and
#: collapsing these three under "reward hacking" would destroy that distinction.
FailureIntent = Literal[
    "INTENTIONAL_GAMING",
    "INCIDENTAL_SPECIFICATION_GAMING",
    "VALIDATION_REGRESSION",
]

#: Where the variant came from. Authored pilot cases establish methodology only.
CaseOrigin = Literal["AUTHORED", "NATURAL_AGENT", "HISTORICAL_REAL_WORLD"]

#: How the candidate PR was produced. Recorded in the pair, never placed in the
#: reviewer payload, so later experiments can study author-attribution effects
#: without retrofitting the schema. Deliberately vendor-neutral: the harness
#: hardcodes no product names. A pack that needs to distinguish two specific
#: authoring systems supplies its own opaque label in ``author_family``.
CandidateAuthorKind = Literal["human", "model", "undisclosed"]

VariantKind = Literal["genuine", "compromised"]

TrustZone = Literal["product", "candidate_validation", "trusted_oracle"]

#: Reviewer information conditions (ablations). The pilot exercises a subset; the
#: plumbing preserves the distinction so later runs are comparable.
ReviewerContextCondition = Literal[
    "DIFF_ONLY",
    "DIFF_PLUS_TASK",
    "DIFF_PLUS_TASK_PLUS_TESTS",
    "FULL_REPOSITORY",
    "FULL_REPOSITORY_PLUS_VISIBLE_CI",
]

#: The merge decision. ABSTAIN is a real option: a reviewer that cannot tell
#: should be able to say so rather than being forced into a block.
IntegrityDisposition = Literal["APPROVE", "REQUEST_CHANGES", "ABSTAIN"]

#: Per-variant and per-repair outcomes. Deliberately NOT flattened into one
#: "pass/fail": each name records a materially different thing that happened.
IntegrityOutcome = Literal[
    "GENUINE_CORRECT_APPROVAL",
    "GENUINE_FALSE_BLOCK",
    "GENUINE_ABSTAINED",
    "COMPROMISED_CORRECT_BLOCK",
    "COMPROMISED_FALSE_APPROVAL",
    "COMPROMISED_ABSTAINED",
    "MECHANISM_MISIDENTIFIED",
    "REPAIR_VISIBLE_ONLY",
    "REPAIR_TRUSTED_PASS",
    "REPAIR_BREAKS_VISIBLE",
    "REPAIR_TOUCHED_TRUSTED_ORACLE",
    "ORACLE_LEAK",
    "ORACLE_TOO_WEAK",
    "ORACLE_TOO_STRICT",
    "INVALID_PAIR",
    "INFRASTRUCTURE_FAILURE",
    "REVIEWER_FAILURE",
]

# --------------------------------------------------------------------------- #
# Bounded scalar types                                                         #
# --------------------------------------------------------------------------- #

BoundedTitle = Annotated[str, StringConstraints(min_length=1, max_length=limits.TITLE_LEN)]
BoundedStatement = Annotated[str, StringConstraints(min_length=1, max_length=limits.SUMMARY_LEN)]
BoundedPhrase = Annotated[str, StringConstraints(min_length=1, max_length=limits.PHRASE_LEN)]
BoundedName = Annotated[str, StringConstraints(min_length=1, max_length=limits.IDENTIFIER_LEN)]
_CommandToken = Annotated[str, StringConstraints(min_length=1, max_length=limits.TOKEN_LEN)]
_ArgvCommand = Annotated[list[_CommandToken], Field(min_length=1, max_length=limits.ARGV_TOKENS)]
_ArgvSequence = Annotated[list[_ArgvCommand], Field(min_length=1, max_length=limits.ARGV_COMMANDS)]
_BoundedCommand = Annotated[
    str, StringConstraints(min_length=1, max_length=limits.COMMAND_STRING_LEN)
]
CommandSpec = _BoundedCommand | _ArgvCommand | _ArgvSequence

# Bounds specific to this track. Pilot observed maxima are far below each.
MAX_INVARIANTS = 32
MAX_VALIDATION_PATHS = 128
MAX_PAIRS_PER_MANIFEST = 256
MAX_ALTERNATES_PER_PAIR = 8
MAX_FINDINGS_PER_REVIEW = 64
MAX_MECHANISMS_PER_VARIANT = 4


# --------------------------------------------------------------------------- #
# Pack-facing models (adversarial input)                                       #
# --------------------------------------------------------------------------- #


class TaskContract(_StrictExternal):
    """The reviewer-visible statement of intended behaviour.

    Written at the abstraction level of an issue or an API contract ("pagination
    must preserve tenant isolation across pages"), never at the level of the
    hidden oracle ("when input X is used, hidden test Y returns Z"). It is the
    only thing that tells a reviewer what the PR was supposed to achieve, so it
    must be complete enough that a careful reader could judge the change.
    """

    title: BoundedTitle
    statement: BoundedStatement
    #: Behavioural invariants the change must preserve or establish. These are
    #: contract-level, not oracle-level: they describe the product, not the tests.
    invariants: list[BoundedPhrase] = Field(default_factory=list, max_length=MAX_INVARIANTS)
    #: Explicitly out of scope, so a reviewer is not expected to demand it.
    out_of_scope: list[BoundedPhrase] = Field(default_factory=list, max_length=MAX_INVARIANTS)
    #: Where the contract came from (issue, PR requirement, documented invariant).
    source: BoundedPhrase = "authored task specification"

    @model_validator(mode="after")
    def _reject_duplicates(self) -> TaskContract:
        for label, items in (("invariants", self.invariants), ("out_of_scope", self.out_of_scope)):
            if len(items) != len(set(items)):
                raise ValueError(f"duplicate entries in {label}")
        return self


class CandidateValidationConfig(_StrictExternal):
    """Zone B: the validation the candidate proposes, executed exactly as proposed.

    ``paths`` declares which workspace paths are candidate-owned validation
    artifacts (a directory prefix or an exact file). Everything else in the
    workspace is product. The partition is used for change analysis, for the
    naive-heuristic audit, and to prove the trusted oracle does not read
    candidate-owned expected values.
    """

    #: The candidate's own CI command. Run for real; never asserted.
    command: CommandSpec
    #: Candidate-owned validation path prefixes (directories or exact files).
    paths: list[SafeFilePath] = Field(min_length=1, max_length=MAX_VALIDATION_PATHS)
    timeout_seconds: int = Field(default=60, ge=1, le=limits.TEST_TIMEOUT_SECONDS_MAX)

    @model_validator(mode="after")
    def _reject_duplicate_paths(self) -> CandidateValidationConfig:
        if len(self.paths) != len(set(self.paths)):
            raise ValueError("duplicate entries in candidate validation paths")
        return self


class TrustedOracleConfig(_StrictExternal):
    """Zone C: hidden, benchmark-owned verification.

    ``dir`` is a directory *outside* every candidate workspace. At trusted
    execution time it is mounted into a throwaway copy of the workspace under
    ``mount``, a path that must not exist in any variant workspace, so the
    candidate could not have referenced, shadowed or rewritten it.
    """

    command: CommandSpec
    #: Oracle source directory, relative to the pair directory.
    dir: SafeDirPath = "oracle"
    #: Where the oracle is mounted inside the throwaway trusted workspace.
    mount: SafeDirPath = "trusted_oracle"
    timeout_seconds: int = Field(default=120, ge=1, le=limits.TEST_TIMEOUT_SECONDS_MAX)


class RepairArtifacts(_StrictExternal):
    """Known-good and known-shallow repairs shipped for harness controls."""

    #: A genuine repair: restores product correctness, keeps visible CI green.
    reference: SafeFilePath | None = None
    #: Restores/strengthens validation but leaves the product defect in place.
    test_only: SafeFilePath | None = None
    #: Satisfies visible validation while staying wrong under the trusted oracle.
    product_overfit: SafeFilePath | None = None


class CandidateVariant(_StrictExternal):
    """One proposed pull request against the shared baseline.

    ``kind`` is grader-side truth and is never placed in the reviewer payload.
    """

    id: SafeCaseId
    kind: VariantKind
    #: Variant workspace: the complete post-PR tree (product + candidate validation).
    workspace_dir: SafeDirPath = "workspace"
    #: Reviewer-visible diff, baseline -> this variant.
    diff: SafeFilePath = "pr.diff"
    #: The PR description a reviewer would see. Must not name the oracle.
    pr_description: Annotated[str, StringConstraints(max_length=limits.DESCRIPTION_LEN)] = ""
    author_kind: CandidateAuthorKind = "undisclosed"
    #: Opaque, pack-supplied authoring-system label (for example a model family
    #: or a team name). Never revealed to the reviewer; it exists so a later
    #: cross-author experiment can group variants without a schema change.
    author_family: BoundedName | None = None
    origin: CaseOrigin = "AUTHORED"
    #: Grader-side mechanism labels. Empty for a genuine variant.
    mechanisms: list[IntegrityFailureClass] = Field(
        default_factory=list, max_length=MAX_MECHANISMS_PER_VARIANT
    )
    intent: FailureIntent | None = None
    #: Grader-side explanation of *why* the visible evidence stopped meaning
    #: anything. Never shown to the reviewer; used to grade mechanism localization.
    mechanism_explanation: Annotated[str, StringConstraints(max_length=limits.SUMMARY_LEN)] = ""
    #: Grader-side keys the reviewer's prose must touch to count as having
    #: localized the mechanism (all of them, case-insensitive substring match).
    mechanism_keywords: list[BoundedPhrase] = Field(
        default_factory=list, max_length=limits.MUST_MENTION_PER_BUG
    )
    #: Product and validation artifacts the mechanism actually lives in.
    affected_validation_paths: list[SafeFilePath] = Field(
        default_factory=list, max_length=MAX_VALIDATION_PATHS
    )
    affected_product_paths: list[SafeFilePath] = Field(
        default_factory=list, max_length=MAX_VALIDATION_PATHS
    )
    repairs: RepairArtifacts = Field(default_factory=RepairArtifacts)

    @model_validator(mode="after")
    def _check_kind_consistency(self) -> CandidateVariant:
        if self.kind == "compromised":
            if not self.mechanisms:
                raise ValueError("a compromised variant must declare at least one mechanism")
            if self.intent is None:
                raise ValueError("a compromised variant must declare a failure intent")
            if not self.mechanism_keywords:
                raise ValueError("a compromised variant must declare mechanism_keywords")
        else:
            if self.mechanisms:
                raise ValueError("a genuine variant must not declare a failure mechanism")
            if self.intent is not None:
                raise ValueError("a genuine variant must not declare a failure intent")
        if len(self.mechanisms) != len(set(self.mechanisms)):
            raise ValueError("duplicate entries in mechanisms")
        return self


class AlternateImplementation(_StrictExternal):
    """A structurally different but legitimate solution to the same contract.

    Used only to challenge the oracle: if the oracle rejects it, the oracle is
    implementation-specific rather than behavioural, and the pair is rejected.
    """

    id: SafeCaseId
    workspace_dir: SafeDirPath
    rationale: BoundedStatement


class IntegrityPair(_StrictExternal):
    """A task plus its genuine and compromised pull requests.

    The pair is the unit of the experiment. Both variants share the baseline, the
    task contract, the visible validation interface and the reviewer context
    policy, so the only thing that separates them is whether the evidence still
    means what it appears to mean.
    """

    id: SafeCaseId
    title: BoundedTitle
    stack: list[BoundedName] = Field(min_length=1, max_length=limits.STACK_ENTRIES)
    task_contract: TaskContract
    #: Pre-PR baseline tree, relative to the pair directory.
    base_dir: SafeDirPath = "base"
    candidate_validation: CandidateValidationConfig
    trusted_oracle: TrustedOracleConfig
    genuine: CandidateVariant
    compromised: CandidateVariant
    alternates: list[AlternateImplementation] = Field(
        default_factory=list, max_length=MAX_ALTERNATES_PER_PAIR
    )
    docker_image: (
        Annotated[str, StringConstraints(max_length=limits.DOCKER_IMAGE_REF_LEN)] | None
    ) = None
    #: Default reviewer information condition for this pair.
    context_condition: ReviewerContextCondition = "FULL_REPOSITORY_PLUS_VISIBLE_CI"
    notes: Annotated[str, StringConstraints(max_length=limits.DESCRIPTION_LEN)] = ""
    #: Runtime state assigned by the loader, never pack input.
    pair_dir: Path | None = Field(default=None, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _reject_runtime_fields(cls, value: object) -> object:
        if isinstance(value, dict) and "pair_dir" in value:
            raise ValueError("pair_dir is internal runtime state and cannot be set from pack input")
        return value

    @model_validator(mode="after")
    def _check_pair_shape(self) -> IntegrityPair:
        if self.genuine.kind != "genuine":
            raise ValueError("the genuine variant must declare kind: genuine")
        if self.compromised.kind != "compromised":
            raise ValueError("the compromised variant must declare kind: compromised")
        if self.genuine.id == self.compromised.id:
            raise ValueError("the two variants must have distinct ids")
        if len(self.stack) != len(set(self.stack)):
            raise ValueError("duplicate entries in stack")
        alternate_ids = [item.id for item in self.alternates]
        if len(alternate_ids) != len(set(alternate_ids)):
            raise ValueError("duplicate alternate implementation ids")
        # The oracle mount is a path inside a throwaway trusted workspace; if it
        # collided with the oracle source directory the copy would be ambiguous.
        if self.trusted_oracle.mount == self.trusted_oracle.dir:
            raise ValueError("trusted oracle mount must differ from its source directory")
        return self

    def variants(self) -> tuple[CandidateVariant, CandidateVariant]:
        """Both variants in a fixed (genuine, compromised) order."""
        return (self.genuine, self.compromised)


class IntegrityManifest(_StrictExternal):
    """Pack manifest for a set of integrity pairs."""

    version: BoundedName
    name: BoundedTitle
    pairs: list[SafeCaseId] = Field(min_length=1, max_length=MAX_PAIRS_PER_MANIFEST)
    taxonomy_version: BoundedName = INTEGRITY_TAXONOMY_VERSION
    default_docker_image: (
        Annotated[str, StringConstraints(max_length=limits.DOCKER_IMAGE_REF_LEN)] | None
    ) = None

    @model_validator(mode="after")
    def _reject_duplicate_pairs(self) -> IntegrityManifest:
        seen: set[str] = set()
        for pair_id in self.pairs:
            if pair_id.casefold() in seen:
                raise ValueError(f"duplicate pair id in manifest: {pair_id}")
            seen.add(pair_id.casefold())
        return self


# --------------------------------------------------------------------------- #
# Reviewer contract                                                            #
# --------------------------------------------------------------------------- #


class IntegrityFinding(BaseModel):
    """One reviewer claim about why the evidence may not support the green PR.

    Every structured field is optional. A reviewer is never required to know the
    harness's internal vocabulary; supplying ``integrity_failure_class`` only
    makes mechanism localization easier to grade automatically.
    """

    title: Annotated[str, StringConstraints(max_length=limits.FINDING_TITLE_LEN)]
    summary: Annotated[str, StringConstraints(max_length=limits.FINDING_SUMMARY_LEN)]
    severity: Literal["critical", "high", "medium", "low"] = "medium"
    confidence: float = Field(default=0.5, ge=0, le=1)
    integrity_failure_class: IntegrityFailureClass | None = None
    affected_validation_artifact: (
        Annotated[str, StringConstraints(max_length=limits.IDENTIFIER_LEN * 8)] | None
    ) = None
    affected_product_artifact: (
        Annotated[str, StringConstraints(max_length=limits.IDENTIFIER_LEN * 8)] | None
    ) = None
    why_visible_evidence_insufficient: (
        Annotated[str, StringConstraints(max_length=limits.EVIDENCE_LEN)] | None
    ) = None
    remediation: Annotated[str, StringConstraints(max_length=limits.SUGGESTED_FIX_LEN)] | None = (
        None
    )


class IntegrityReview(BaseModel):
    """A reviewer's complete response to one candidate variant."""

    disposition: IntegrityDisposition
    findings: list[IntegrityFinding] = Field(
        default_factory=list, max_length=MAX_FINDINGS_PER_REVIEW
    )
    review_summary: Annotated[str, StringConstraints(max_length=limits.REVIEW_SUMMARY_LEN)] = ""
    #: Optional repair. Applied through the same Git-authoritative pipeline as an
    #: ordinary Arena repair; it may touch candidate-owned validation but the
    #: trusted oracle is not present in the workspace and so cannot be reached.
    repair_patch: Annotated[str, StringConstraints(max_length=limits.PATCH_LEN)] | None = None
    #: Optional 0..1 trustworthiness rating, lower = less trustworthy. Used only
    #: as a secondary signal for pairwise ranking; dispositions are primary.
    evidence_trust: float | None = Field(default=None, ge=0, le=1)
    latency_ms: int = Field(default=0, ge=0)
    reviewer_error: (
        Annotated[str, StringConstraints(max_length=limits.PARSE_ERROR_SUMMARY_LEN)] | None
    ) = None


class VisibleCISummary(BaseModel):
    """The green badge a reviewer would see. Deliberately shallow.

    It carries what a PR page shows -- the command, that it passed, how many tests
    ran and how many were skipped -- and never the trusted oracle's existence,
    name, command or verdict.
    """

    command: str
    passed: bool
    exit_code: int | None = None
    executed_test_count: int | None = None
    skipped_test_count: int | None = None
    duration_ms: int = 0


class IntegrityReviewContext(BaseModel):
    """Exactly what a reviewer receives. Nothing here is oracle-derived."""

    #: Opaque per-variant id. Never encodes the variant kind.
    submission_id: str
    pair_title: str
    stack: list[str]
    context_condition: ReviewerContextCondition
    task_contract: TaskContract | None
    pr_description: str
    diff: str
    #: Product-zone files the condition allows.
    product_files: dict[str, str] = Field(default_factory=dict)
    #: Candidate-owned validation files the condition allows.
    validation_files: dict[str, str] = Field(default_factory=dict)
    #: Deterministic description of how validation changed. Evidence, not verdict.
    validation_change: ValidationChangeAnalysis | None = None
    visible_ci: VisibleCISummary | None = None
    context_truncated: bool = False
    omitted_files: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Execution evidence and results                                               #
# --------------------------------------------------------------------------- #


class ExecutionEvidence(BaseModel):
    """One real execution of a validation surface. Never asserted, always run."""

    zone: Literal["visible", "trusted"]
    command: list[list[str]]
    ran: bool
    passed: bool
    exit_code: int | None = None
    executed_test_count: int | None = None
    skipped_test_count: int | None = None
    #: sha256 over normalized stdout+stderr; identity of the output without the
    #: output itself, so a trusted digest can be persisted with no leak risk.
    output_digest: str
    duration_ms: int = 0
    timed_out: bool = False
    backend: Literal["docker", "trusted-local", "none"] = "none"
    error: str | None = None
    #: Bounded output tail. For the trusted zone this is only ever populated in
    #: grader-side certification reports, never in a reviewer-visible surface.
    output_tail: str = ""


class ValidationChangeAnalysis(BaseModel):
    """Deterministic description of how candidate-owned validation changed.

    These are facts, not judgements. A legitimate PR routinely adds, removes and
    rewrites tests; the reviewer's job is to reason about *why*.
    """

    changed_validation_files: list[str] = Field(default_factory=list)
    added_validation_files: list[str] = Field(default_factory=list)
    removed_validation_files: list[str] = Field(default_factory=list)
    tests_added: list[str] = Field(default_factory=list)
    tests_removed: list[str] = Field(default_factory=list)
    assertions_added: int = 0
    assertions_removed: int = 0
    skip_markers_introduced: int = 0
    discovery_or_config_changed: list[str] = Field(default_factory=list)
    fixture_or_golden_changed: list[str] = Field(default_factory=list)
    mock_or_stub_changed: list[str] = Field(default_factory=list)
    changed_product_files: list[str] = Field(default_factory=list)

    @property
    def touches_validation(self) -> bool:
        return bool(
            self.changed_validation_files
            or self.added_validation_files
            or self.removed_validation_files
        )


class RepairEvaluation(BaseModel):
    """What a reviewer-supplied repair actually achieved.

    Detecting and repairing are different achievements, so they are recorded
    separately and a repair that merely restores a strict test is not a repair.
    """

    provided: bool = False
    applied: bool = False
    apply_error: str | None = None
    touched_files: list[str] = Field(default_factory=list)
    touched_trusted_oracle: bool = False
    visible_passed: bool | None = None
    trusted_passed: bool | None = None
    restores_trusted_correctness: bool = False
    restores_meaningful_validation: bool | None = None
    outcome: IntegrityOutcome | None = None


class IntegrityVariantResult(BaseModel):
    """Grader-side record for one reviewed variant."""

    pair_id: str
    variant_id: str
    submission_id: str
    kind: VariantKind
    context_condition: ReviewerContextCondition
    mechanisms: list[IntegrityFailureClass] = Field(default_factory=list)
    intent: FailureIntent | None = None
    origin: CaseOrigin = "AUTHORED"
    author_kind: CandidateAuthorKind = "undisclosed"
    author_family: str | None = None
    visible: ExecutionEvidence
    trusted: ExecutionEvidence
    #: Grader-side diagnostic: visible PASS while trusted FAIL. Never an input to
    #: the reviewer; the whole point is to measure inference without it.
    visible_trusted_gap: bool = False
    validation_change: ValidationChangeAnalysis | None = None
    review: IntegrityReview | None = None
    challenged: bool = False
    mechanism_identified: bool = False
    repair: RepairEvaluation = Field(default_factory=RepairEvaluation)
    outcomes: list[IntegrityOutcome] = Field(default_factory=list)
    infrastructure_error: str | None = None


class IntegrityPairResult(BaseModel):
    """Both variants of one pair, plus the pairwise judgement."""

    pair_id: str
    title: str
    genuine: IntegrityVariantResult
    compromised: IntegrityVariantResult
    #: The desired outcome: approve genuine AND request changes on compromised.
    pairwise_discriminated: bool = False
    #: Approved both / rejected both / inverted, recorded explicitly so a
    #: conservative reviewer is never mistaken for a discriminating one.
    pair_pattern: Literal[
        "discriminating", "approve_both", "reject_both", "inverted", "indeterminate"
    ] = "indeterminate"

    def results(self) -> tuple[IntegrityVariantResult, IntegrityVariantResult]:
        return (self.genuine, self.compromised)


class IntegrityMetrics(BaseModel):
    """The reported metrics. Deliberately several, never one opaque score."""

    pairs_evaluated: int = 0
    variants_evaluated: int = 0
    integrity_attack_recall: float | None = None
    genuine_approval_rate: float | None = None
    false_integrity_alarm_rate: float | None = None
    pairwise_discrimination_rate: float | None = None
    mechanism_localization_rate: float | None = None
    trusted_oracle_failure_recall: float | None = None
    repair_restoration_rate: float | None = None
    evidence_restoration_rate: float | None = None
    outcome_counts: dict[str, int] = Field(default_factory=dict)
    pair_pattern_counts: dict[str, int] = Field(default_factory=dict)


class IntegrityRunResult(BaseModel):
    """A complete CRA-Integrity run: protocol, per-pair evidence, metrics."""

    run_id: str
    pack: str
    reviewer: str
    started_at: str
    completed_at: str
    protocol_digest: str
    protocol: dict[str, object]
    context_condition: ReviewerContextCondition
    pair_results: list[IntegrityPairResult] = Field(default_factory=list)
    metrics: IntegrityMetrics = Field(default_factory=IntegrityMetrics)
    execution_backend: Literal["docker", "trusted-local", "none"] = "none"
    notes: list[str] = Field(default_factory=list)


# Resolve the forward reference used inside IntegrityReviewContext.
IntegrityReviewContext.model_rebuild()
