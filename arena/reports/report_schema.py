"""Versioned Pydantic contracts for the JSON the dashboard consumes.

Both writers (the audit-report command and the verification snapshot script)
validate their output against these models before writing, and the dashboard
checks ``schema_version`` on read. A drift between producer and consumer surfaces
as a loud, specific error instead of a silently empty or stale page.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Audit report (dashboard/public/reports/audit-v1.json) ---


class AuditGapRef(_Strict):
    reviewer: str | None = None
    gap: float


class AuditSummary(_Strict):
    benchmark_pack: str
    run_count: int
    case_count: int
    reviewers_tested: list[str]
    biggest_detection_validation_gap: AuditGapRef | None = None
    # Runs the validity policy refused to publish (invalid/partial/failed, or
    # pre-v2 with unknowable validity). Optional with a default so reports written
    # by an older harness still validate; schema_version stays 1.0 because adding
    # an optional field cannot break a reader that does not look for it.
    excluded_run_count: int = 0


class AuditReviewerRow(_Strict):
    reviewer: str
    model: str
    mode: str
    detection_precision: float | None = None
    detection_recall: float | None = None
    detection_f_beta: float | None = None
    validated_precision: float | None = None
    validated_recall: float | None = None
    validated_f_beta: float | None = None
    # v2 evidence metrics (validated_case_rate is the current primary metric).
    validated_case_rate: float | None = None
    complete_repair_rate: float | None = None
    bug_completeness_rate: float | None = None
    supported_claim_rate: float | None = None
    deterministic_pass_rate: float | None = None
    # Denominator behind the rates above: execution-backed cases only. A
    # consumer that scales a rate back into a count must use this, not the
    # pack's total case count, or the fraction contradicts the rate.
    validated_eligible_case_count: int | None = None
    patch_apply_rate: float | None = None
    test_pass_rate: float | None = None
    structural_pass_rate: float | None = None
    false_positives_per_case: float | None = None
    cost_per_validated_fix: float | None = None
    latency_per_case_ms: float | None = None
    run_id: str
    primary_failure_mode: str | None = None
    # Integrity provenance for the run behind this row. Defaulted for reports
    # written before these fields existed.
    run_status: str = "complete"
    pack_checksum_verified: bool | None = None


class AuditGap(_Strict):
    reviewer: str
    model: str
    mode: str
    detection_f_beta: float
    validated_f_beta: float
    gap: float
    run_id: str


class ValidatorEvidence(_Strict):
    name: str | None = None
    passed: bool | None = None
    message: str | None = None


class AuditCaseStudy(_Strict):
    case_id: str
    reviewer: str
    model: str
    finding_summary: str
    failure_reasons: list[str]
    validator_evidence: list[ValidatorEvidence]
    test_stderr_tail: str


class AuditReport(_Strict):
    schema_version: str
    title: str
    generated_at: str
    empty: bool
    summary: AuditSummary
    reviewers: list[AuditReviewerRow]
    gaps: list[AuditGap]
    failure_modes: dict[str, int]
    case_studies: list[AuditCaseStudy]
    reproducibility_commands: list[str]
    limitations: list[str]


# --- Verification snapshot (dashboard/public/verification.json) ---


class VerificationCheck(BaseModel):
    # Checks/baselines carry a variable tail (output_tail, meaning, metrics, run_id).
    model_config = ConfigDict(extra="allow")

    status: str
    checked_at: str | None = None
    command: str


class VerificationSnapshot(_Strict):
    schema_version: str
    project_name: str
    generated_at: str
    benchmark_sets: dict[str, VerificationCheck]
    baselines: dict[str, VerificationCheck]
    quality_checks: dict[str, VerificationCheck]
    capabilities: dict[str, VerificationCheck]
