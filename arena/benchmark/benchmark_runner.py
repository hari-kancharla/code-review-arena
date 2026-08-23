"""End-to-end benchmark run orchestration."""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
import warnings
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Literal

from arena import __version__
from arena.benchmark.case_loader import build_context, load_manifest
from arena.benchmark.exposure import assign_cohort
from arena.benchmark.snapshot import SNAPSHOT_MANIFEST_VERSION, PackSnapshot, snapshot_pack
from arena.core import limits
from arena.core.config import (
    PROMPT_VERSION,
    database_path,
    runs_path,
    trusted_pack_hashes,
)
from arena.core.errors import ValidationError
from arena.core.models import (
    RUN_SCHEMA_VERSION,
    BenchmarkCase,
    BugRepair,
    CaseResult,
    CaseStatus,
    DeterministicCaseScore,
    ExecutionBackend,
    FindingEvidence,
    RepairConfidence,
    ReviewerResponse,
    ReviewResult,
    RunMetadata,
    RunResult,
    RunStatus,
    ScoreBreakdown,
    ScoredFinding,
)
from arena.execution.integrity import file_manifest, manifest_changes
from arena.execution.sandbox import materialized_case
from arena.execution.test_executor import TestExecutionRequest, TestExecutionResult, TestExecutor
from arena.patching.patch_applier import PatchApplier
from arena.patching.patch_models import PatchApplyRequest
from arena.reports.bundle import write_bundle_checksums
from arena.reports.html_report import write_html_report
from arena.reports.json_report import write_json_report
from arena.reports.markdown_report import write_markdown_report
from arena.reviewers.base import BaseReviewer
from arena.scoring.deterministic_scorer import (
    aggregate_deterministic_metrics,
    is_execution_backed,
    score_deterministic_case,
)
from arena.scoring.exposure_metrics import aggregate_exposure_metrics
from arena.scoring.scorer import apply_execution_fix_quality, score_case
from arena.storage.repository import RunRepository
from arena.tools.static_analyzer import run_static_analysis
from arena.validators.base import ValidatorContext
from arena.validators.registry import run_validators


def _git_commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _git_dirty() -> bool | None:
    """True if the working tree has uncommitted changes; None if git is absent.

    A clean recorded git_commit is meaningless if the tree was dirty, so the run
    records this explicitly rather than implying the commit fully describes the
    code that ran.
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return bool(result.stdout.strip())


# Failure reasons that stop a case counting as an execution-validated repair.
# A module constant rather than a literal buried in the attribution function, so
# a new reason cannot be added to the scorer while silently missing this set --
# which would turn the very case it describes into a validated repair.
_BLOCKING_FAILURE_REASONS = frozenset(
    {
        "patch_required_but_missing",
        "patch_apply_failed",
        "tests_failed",
        # An execution that produced no verdict blocks just as firmly as a failing
        # one: nothing was confirmed either way.
        "execution_inconclusive",
        "structural_validation_failed",
        "test_integrity_violation",
        "no_execution_evidence",
    }
)


def _run_level_beta(cases: Sequence[BenchmarkCase]) -> float:
    """The single beta a whole-run F-beta is reported at.

    A run-level F-beta is only well defined when every case agrees on beta. When
    a pack declares more than one, no single value is faithful, so this falls
    back to the balanced 1.0 and says so rather than silently adopting one case's
    weighting for the entire run.
    """
    declared = {case.metrics.beta for case in cases}
    if len(declared) == 1:
        return declared.pop()
    if declared:
        print(
            f"WARNING: pack declares mixed metrics.beta values {sorted(declared)}; "
            "reporting run-level F-beta at beta=1.0. Pass --beta to choose explicitly.",
            file=sys.stderr,
        )
    return 1.0


def _run_status(
    *,
    results: int,
    failed: int,
    skipped: bool,
    checksum_verified: bool | None,
    execution_required: bool,
    executed: int,
    unavailable: int,
    all_output_invalid: bool = False,
) -> RunStatus:
    """Classify a finished run's trust level (see RunStatus).

    Implements the documented invariant (docs/trusted-evaluation-architecture.md):
    ``complete`` requires zero failed, zero skipped, completed == eligible, and
    every required execution conclusive.

    A tampered pack invalidates the whole run; a run that produced no usable
    result at all failed; a run where the reviewer never once produced parseable
    output is invalid, because a wrapper that crashes on every case is a broken
    measurement and not a reviewer that scored zero; a run that needed test
    execution but whose backend was never available (nothing executed, something
    tried) is invalid because no repair could be judged; a budget-truncated run,
    one where a case raised inside the harness, or one where some cases ran and
    some could not, is partial; otherwise it is complete.
    """
    if checksum_verified is False:
        return "invalid"
    if skipped and results == 0:
        # The budget tripped before any case ran: a truncation, not a crash.
        return "partial"
    if results == 0:
        return "failed"
    # When the reviewer never once produced parseable output, say so: that is a
    # broken reviewer contract, which is a more specific and more actionable
    # verdict than the generic "nothing survived" below, and it must win over it.
    if all_output_invalid:
        return "invalid"
    # A case that raised inside the harness contributes a placeholder result, not
    # a measurement. If none survived, the run carries no usable result at all.
    if failed >= results:
        return "failed"
    if execution_required and unavailable > 0 and executed == 0:
        return "invalid"
    if skipped or failed or (execution_required and unavailable > 0):
        return "partial"
    return "complete"


def _reserve_run_dir(root: Path) -> tuple[str, Path]:
    base = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    candidate = base
    suffix = 1
    while True:
        run_dir = root / candidate
        try:
            run_dir.mkdir(parents=True)
            return candidate, run_dir
        except FileExistsError:
            candidate = f"{base}_{suffix}"
            suffix += 1


def _select_case_patch(parsed: ReviewResult | None) -> tuple[str | None, str]:
    """Pick the single repair to apply, returning (patch_text, source).

    The case-level ``proposed_patch`` is authoritative. For legacy reviewers that
    only attach a patch to a finding, that patch is adopted only when exactly one
    finding carries one (unambiguous). Two or more competing finding patches are
    never silently concatenated -- order, overlap, and conflicts make that
    meaningless -- so the result is reported as ``ambiguous`` with no patch.
    """
    if parsed is None:
        return None, "none"
    if parsed.proposed_patch and parsed.proposed_patch.strip():
        return parsed.proposed_patch, "proposed_patch"
    finding_patches = [
        finding.suggested_patch
        for finding in parsed.findings
        if finding.suggested_patch and finding.suggested_patch.strip()
    ]
    if len(finding_patches) == 1:
        return finding_patches[0], "single_finding"
    if len(finding_patches) > 1:
        return None, "ambiguous"
    return None, "none"


def _effective_timeout(base_seconds: int, deadline: float | None) -> int:
    """Clamp a per-stage timeout to the remaining run budget (floor of 1s).

    Without this a single case could run for its full timeout past the run's
    wall-clock budget; the deadline makes the budget hard rather than advisory.
    """
    if deadline is None:
        return base_seconds
    remaining = deadline - monotonic()
    return max(1, min(base_seconds, int(remaining)))


def _attribute_evidence(
    case: BenchmarkCase,
    review_result: CaseResult,
    deterministic: DeterministicCaseScore,
    *,
    execution_validated: bool,
    integrity_violated: bool,
) -> tuple[list[BugRepair], list[ScoredFinding], CaseStatus]:
    """Attribute the repair to bugs and stamp each finding with its evidence status.

    Repair is judged at the suite level (execution_validated): per-bug oracle
    mapping for multi-bug cases is a later refinement, so every seeded bug shares
    the suite's repair verdict while detection stays per-bug.
    """
    matched = {
        item.matched_bug_index for item in review_result.scored_findings if item.is_true_positive
    }
    bug_repairs = [
        BugRepair(bug_id=bug.id, detected=index in matched, repaired=execution_validated)
        for index, bug in enumerate(case.ground_truth.bugs)
    ]

    findings: list[ScoredFinding] = []
    for item in review_result.scored_findings:
        if item.is_neutral:
            status: FindingEvidence = "neutral"
        elif not item.is_true_positive:
            status = "unsupported"
        elif execution_validated:
            status = "repair_validated"
        else:
            status = "detected_but_unrepaired"
        findings.append(item.model_copy(update={"evidence_status": status}))

    total = len(bug_repairs)
    detected_count = sum(bug.detected for bug in bug_repairs)
    ran_anything = deterministic.tests_ran or deterministic.structural_validation_ran
    if integrity_violated:
        case_status: CaseStatus = "tampering"
    elif not ran_anything:
        case_status = "inconclusive"
    elif execution_validated and detected_count == total:
        case_status = "complete_repair"
    elif execution_validated:
        case_status = "partial_repair"
    elif detected_count > 0:
        case_status = "detected_but_unrepaired"
    else:
        case_status = "no_detection"
    return bug_repairs, findings, case_status


def _repair_confidence(
    *, execution_validated: bool, deterministic: DeterministicCaseScore
) -> RepairConfidence:
    """Label how deeply a validated repair was challenged (see RepairConfidence).

    basic = the repair passed required tests; strong = it also satisfied the
    case's structural validators. unvalidated = the repair did not pass.
    """
    if not execution_validated:
        return "unvalidated"
    if deterministic.structural_validation_ran and deterministic.structural_validation_passed:
        return "strong"
    return "basic"


def _evaluate_case(
    case: BenchmarkCase,
    reviewer: BaseReviewer,
    *,
    test_executor: TestExecutor,
    patch_applier: PatchApplier,
    run_id: str,
    mode: Literal["review", "patch", "full"],
    selected_beta: float,
    allow_local_execution: bool,
    deadline: float | None = None,
) -> CaseResult:
    test_output = ""
    static_output = ""
    with materialized_case(case) as materialized:
        if allow_local_execution and case.execution.run_tests and case.execution.test_command:
            executed = test_executor.execute(
                TestExecutionRequest(
                    case_id=case.id,
                    workspace_path=materialized,
                    test_command=case.execution.test_command,
                    timeout_seconds=_effective_timeout(case.execution.timeout_seconds, deadline),
                    docker_image=case.execution.docker_image,
                    allow_local_execution=allow_local_execution,
                )
            )
            test_output = (
                f"exit_code={executed.exit_code}\nduration_ms={executed.duration_ms}\n"
                f"{executed.stdout}{executed.stderr}"
            )
        if (
            allow_local_execution
            and case.execution.run_static_analysis
            and case.execution.static_analysis_command
        ):
            # static_analysis_command is pack-controlled, so it is host code
            # execution and must require the same explicit local-execution opt-in
            # as the test command, never run unconditionally.
            static_output = run_static_analysis(
                materialized,
                case.execution.static_analysis_command,
                _effective_timeout(case.execution.timeout_seconds, deadline),
            )
    context = build_context(case, test_output=test_output, static_analysis_output=static_output)
    response = reviewer.review(context)
    review_result = score_case(case, response, test_output=test_output).model_copy(
        update={"context_truncated": context.context_truncated}
    )
    if mode == "review":
        return review_result
    assert case.case_dir is not None
    # The representative finding only supplies structural-validator context; the
    # patch that is actually applied is the single case-level repair (see
    # _select_case_patch). They are deliberately decoupled so multi-bug cases are
    # repaired by one complete diff rather than one bug's finding patch.
    matching_finding = next(
        (item.finding for item in review_result.scored_findings if item.matched_bug_index == 0),
        None,
    ) or next(
        (item.finding for item in review_result.scored_findings if item.is_true_positive), None
    )
    patch_text, patch_source = _select_case_patch(review_result.response.parsed_response)
    extra_reasons = ["ambiguous_patch_source"] if patch_source == "ambiguous" else []
    protected_paths = list(case.validation.protected_paths)
    if case.input.tests_dir:
        protected_paths.append(case.input.tests_dir)
    patch = patch_applier.apply(
        PatchApplyRequest(
            case_id=case.id,
            source_dir=case.case_dir / case.input.after_dir,
            patch_text=patch_text or "",
            run_id=run_id,
            protected_paths=protected_paths,
        )
    )
    executed_tests: TestExecutionResult | None = None
    integrity_changes: list[str] = []
    if patch.applied and case.execution.run_tests and case.execution.test_command:
        tests_dir = case.input.tests_dir
        tests_root = Path(patch.workspace_path) / tests_dir if tests_dir else None
        if tests_dir and (case.case_dir / tests_dir).is_dir():
            shutil.copytree(
                case.case_dir / tests_dir,
                Path(patch.workspace_path) / tests_dir,
                dirs_exist_ok=True,
            )
        # Snapshot the hidden tests so candidate code that rewrites them mid-run
        # is caught even though the patch itself could not declare those paths.
        before_tests = file_manifest(tests_root) if tests_root else {}
        # A timeout means different things depending on WHOSE budget ran out. If
        # the run deadline clamped the case's own timeout, the suite was cut short
        # by the harness, and charging that to the reviewer publishes 0% for what
        # may be a perfect repair -- on a run that still calls itself complete.
        test_timeout = _effective_timeout(case.execution.timeout_seconds, deadline)
        deadline_truncated = test_timeout < case.execution.timeout_seconds
        executed_tests = test_executor.execute(
            TestExecutionRequest(
                case_id=case.id,
                workspace_path=Path(patch.workspace_path),
                test_command=case.execution.test_command,
                timeout_seconds=test_timeout,
                docker_image=case.execution.docker_image,
                allow_local_execution=allow_local_execution,
                # Pin the hidden tests read-only in Docker so a patch cannot
                # rewrite them to pass (local execution relies on detection).
                readonly_paths=[tests_dir] if tests_dir else [],
            )
        )
        if executed_tests.timed_out and deadline_truncated:
            executed_tests = executed_tests.model_copy(update={"error": "test_deadline_truncated"})
        if tests_root:
            integrity_changes = manifest_changes(before_tests, file_manifest(tests_root))
    validators = []
    if patch.applied and case.validation.structural_validators:
        validators = run_validators(
            case.validation.structural_validators,
            ValidatorContext(
                case_id=case.id,
                workspace_path=Path(patch.workspace_path),
                changed_files=patch.touched_files,
                finding=matching_finding,
                case_metadata=case,
            ),
        )
    deterministic = score_deterministic_case(
        case, review_result, patch, executed_tests, validators, selected_beta
    )
    integrity_violated = bool(integrity_changes)
    if integrity_violated:
        # Fold tampering into the stored score itself, not just the top-level
        # flag: aggregate metrics read deterministic_case_score, so a tampering
        # case with otherwise-passing tests must not count as a validated fix.
        deterministic = deterministic.model_copy(
            update={
                "deterministic_pass": False,
                "failure_reasons": [*deterministic.failure_reasons, "test_integrity_violation"],
            }
        )
    execution_validated = deterministic.patch_applied and not (
        _BLOCKING_FAILURE_REASONS & set(deterministic.failure_reasons)
    )
    if executed_tests is None:
        case_backend: ExecutionBackend = "none"
    elif executed_tests.execution_mode == "docker":
        case_backend = "docker"
    elif executed_tests.execution_mode == "local":
        case_backend = "trusted-local"
    else:
        case_backend = "none"
    # Execution was attempted (we built a request) but the backend itself was
    # missing, so the repair never got a verdict. Content-level skips (a bad
    # test command, a missing workspace) are the case's problem, not the harness'.
    execution_unavailable = executed_tests is not None and (
        executed_tests.error
        in {
            "docker_required_but_unavailable",
            "docker_image_not_present",
            "local_execution_disabled",
            # The run's wall-clock budget, not the backend, ended this case. The
            # run is degraded rather than reporting a clean, fully covered
            # measurement that charges the truncation to the reviewer.
            "test_deadline_truncated",
        }
        # A test runner this interpreter cannot import is a missing backend just
        # like an absent Docker daemon: nothing ran, so no repair got a verdict.
        # Carries the module name as a suffix, hence the prefix match.
        or (executed_tests.error or "").startswith("test_runner_unavailable")
    )
    review_result = apply_execution_fix_quality(case, review_result, validated=execution_validated)
    bug_repairs, scored_findings, case_status = _attribute_evidence(
        case,
        review_result,
        deterministic,
        execution_validated=execution_validated,
        integrity_violated=integrity_violated,
    )
    repair_confidence = _repair_confidence(
        execution_validated=execution_validated, deterministic=deterministic
    )
    return review_result.model_copy(
        update={
            "scored_findings": scored_findings,
            "bug_repairs": bug_repairs,
            "case_status": case_status,
            "repair_confidence": repair_confidence,
            "execution_backend": case_backend,
            "execution_unavailable": execution_unavailable,
            "deterministic_case_score": deterministic,
            "patch_provided": deterministic.patch_provided,
            "patch_applied": deterministic.patch_applied,
            "patch_error": patch.error,
            "touched_files": patch.touched_files,
            "patch_sha256": patch.patch_sha256,
            "patch_object_format": patch.object_format,
            "patch_baseline_tree": patch.baseline_tree,
            "patch_result_tree": patch.result_tree,
            "git_diagnostic": patch.git_diagnostic,
            "patch_added": patch.added,
            "patch_deleted": patch.deleted,
            "patch_mode_changes": patch.mode_changes,
            "tests_ran": deterministic.tests_ran,
            "tests_passed": deterministic.tests_passed,
            "test_stdout_tail": _tail(executed_tests.stdout if executed_tests else ""),
            "test_stderr_tail": _tail(
                (executed_tests.stderr or executed_tests.error or "") if executed_tests else ""
            ),
            "validators_run": [item.name for item in validators],
            "validators_passed": deterministic.structural_validation_passed,
            "validator_results": [item.model_dump() for item in validators],
            "deterministic_pass": deterministic.deterministic_pass,
            "failure_reasons": deterministic.failure_reasons + extra_reasons,
            "raw_suggested_patch": patch_text,
        }
    )


def _failed_case_result(
    case: BenchmarkCase,
    error: Exception,
    mode: Literal["review", "patch", "full"],
) -> CaseResult:
    """Record an unexpected per-case failure as a non-passing result and keep going."""
    reasons = [f"case_execution_error: {type(error).__name__}: {error}"]
    deterministic = None
    if mode != "review":
        deterministic = DeterministicCaseScore(
            case_id=case.id,
            detected_bug=False,
            localized_correctly=False,
            patch_provided=False,
            patch_applied=False,
            tests_ran=False,
            tests_passed=None,
            structural_validation_ran=False,
            structural_validation_passed=None,
            true_positive_count=0,
            false_positive_count=0,
            false_negative_count=len(case.ground_truth.bugs),
            precision=0.0,
            recall=0.0,
            f1=0.0,
            f_beta=0.0,
            patch_apply_score=0.0,
            execution_score=0.0,
            structural_score=0.0,
            deterministic_pass=False,
            # Same predicate the real scorer uses, so a crashed case cannot be
            # charged to the reviewer inside a denominator that excludes it.
            validation_eligible=is_execution_backed(case),
            failure_reasons=reasons,
        )
    return CaseResult(
        case_id=case.id,
        title=case.title,
        category=case.category,
        severity=case.severity,
        ground_truth_summary=case.ground_truth.primary_bug.summary,
        response=ReviewerResponse(raw_response="", invalid_output=True, parse_status="invalid"),
        scored_findings=[],
        breakdown=ScoreBreakdown(),
        score=0.0,
        review_quality_score=0.0,
        bug_found=False,
        correct_file=False,
        correct_line=False,
        line_match="wrong_file",
        bugs_total=len(case.ground_truth.bugs),
        bugs_matched=0,
        false_positive_count=0,
        deterministic_case_score=deterministic,
        deterministic_pass=False if mode != "review" else None,
        failure_reasons=reasons,
    )


def run_benchmark(
    benchmark_dir: Path,
    reviewer: BaseReviewer,
    output_dir: Path | None = None,
    db_path: Path | None = None,
    persist: bool = True,
    mode: Literal["review", "patch", "full"] = "review",
    beta: float | None = None,
    allow_local_execution: bool = False,
    max_wall_seconds: float | None = None,
    max_cost: float | None = None,
    expected_pack_sha256: str | None = None,
    model_knowledge_cutoff: str | None = None,
    model_knowledge_cutoff_basis: str | None = None,
    model_knowledge_cutoff_source: str | None = None,
    cutoff_grace_days: int = limits.DEFAULT_CUTOFF_GRACE_DAYS,
    reviewer_retrieval: str = "unknown",
) -> RunResult:
    """Snapshot the source pack, then run entirely from the immutable snapshot.

    The snapshot is created before any run directory is reserved and is verified
    again before reports are sealed; the mutable source is never re-read during the
    run, and the snapshot is removed on return (including on error).
    """
    with snapshot_pack(benchmark_dir) as snapshot:
        return _run_on_snapshot(
            snapshot,
            reviewer,
            output_dir=output_dir,
            db_path=db_path,
            persist=persist,
            mode=mode,
            beta=beta,
            allow_local_execution=allow_local_execution,
            max_wall_seconds=max_wall_seconds,
            max_cost=max_cost,
            expected_pack_sha256=expected_pack_sha256,
            model_knowledge_cutoff=model_knowledge_cutoff,
            model_knowledge_cutoff_basis=model_knowledge_cutoff_basis,
            model_knowledge_cutoff_source=model_knowledge_cutoff_source,
            cutoff_grace_days=cutoff_grace_days,
            reviewer_retrieval=reviewer_retrieval,
        )


def _run_on_snapshot(
    snapshot: PackSnapshot,
    reviewer: BaseReviewer,
    *,
    output_dir: Path | None = None,
    db_path: Path | None = None,
    persist: bool = True,
    mode: Literal["review", "patch", "full"] = "review",
    beta: float | None = None,
    allow_local_execution: bool = False,
    max_wall_seconds: float | None = None,
    max_cost: float | None = None,
    expected_pack_sha256: str | None = None,
    model_knowledge_cutoff: str | None = None,
    model_knowledge_cutoff_basis: str | None = None,
    model_knowledge_cutoff_source: str | None = None,
    cutoff_grace_days: int = limits.DEFAULT_CUTOFF_GRACE_DAYS,
    reviewer_retrieval: str = "unknown",
) -> RunResult:
    # Validation is a precondition: a partially valid or tampered pack must abort
    # before any run directory or side effect is created.
    cases = snapshot.load_and_validate()
    checksum = snapshot.checksum
    # External trust anchor: pack.sha256 lives inside the pack, so on its own it
    # cannot prove the pack was not tampered with and its hash regenerated. When a
    # caller pins the expected digest out of band (a signed release, CI), a pack
    # whose content does not match aborts before any run directory is created.
    if expected_pack_sha256 is not None and checksum != expected_pack_sha256:
        raise ValidationError(
            f"pack checksum {checksum} does not match the expected {expected_pack_sha256}; "
            "refusing to run a pack that does not match its pinned digest"
        )
    root = output_dir or runs_path()
    root.mkdir(parents=True, exist_ok=True)
    run_id, run_dir = _reserve_run_dir(root)
    manifest = load_manifest(snapshot.root)
    pinned = snapshot.stored_checksum
    # Defense in depth: when an operator pins a trusted-hash allowlist, a pack
    # not on it does not get host execution even if the caller passed the flag.
    effective_allow_local = allow_local_execution
    if allow_local_execution:
        trusted = trusted_pack_hashes()
        if trusted and checksum not in trusted:
            effective_allow_local = False
            warnings.warn(
                f"local execution requested but pack checksum {checksum} is not in "
                "ARENA_TRUSTED_PACK_HASHES; running without local execution.",
                stacklevel=2,
            )
    started = datetime.now()
    # Monotonic deadline makes max_wall_seconds a hard budget: each case's
    # execution timeout is clamped to the time left, not just checked between cases.
    run_deadline = monotonic() + max_wall_seconds if max_wall_seconds is not None else None
    case_results = []
    skipped_case_ids: list[str] = []
    budget_stopped_reason: str | None = None
    running_cost = 0.0
    errored = 0
    test_executor = TestExecutor()
    patch_applier = PatchApplier(root)
    selected_beta = beta or 1.0
    # The run-level beta is resolved once, up front. It used to be whatever the
    # last executed case happened to declare, so on a pack with mixed
    # metrics.beta values simply reordering the manifest changed the published
    # headline F-beta without any reviewer behaving differently. Per-case scoring
    # still uses each case's own beta (selected_beta, below).
    run_beta = beta if beta is not None else _run_level_beta(cases)
    for case in cases:
        if budget_stopped_reason is None:
            elapsed = (datetime.now() - started).total_seconds()
            if max_wall_seconds is not None and elapsed >= max_wall_seconds:
                budget_stopped_reason = (
                    f"max_wall_seconds={max_wall_seconds} exceeded after {elapsed:.1f}s"
                )
            elif max_cost is not None and running_cost >= max_cost:
                budget_stopped_reason = f"max_cost={max_cost} exceeded at {running_cost:.6f}"
        if budget_stopped_reason is not None:
            skipped_case_ids.append(case.id)
            continue
        if beta is None:
            selected_beta = case.metrics.beta
        try:
            case_results.append(
                _evaluate_case(
                    case,
                    reviewer,
                    test_executor=test_executor,
                    patch_applier=patch_applier,
                    run_id=run_id,
                    mode=mode,
                    selected_beta=selected_beta,
                    allow_local_execution=effective_allow_local,
                    deadline=run_deadline,
                )
            )
        except Exception as exc:  # noqa: BLE001 - one failing case must not abort the batch.
            case_results.append(_failed_case_result(case, exc, mode))
            errored += 1
        running_cost += case_results[-1].response.estimated_cost
    completed = datetime.now()
    total_cost = round(sum(item.response.estimated_cost for item in case_results), 6)
    total_latency = sum(item.response.latency_ms for item in case_results)
    produced = len(case_results)
    eligible = produced + len(skipped_case_ids)
    checksum_verified = None if pinned is None else pinned == checksum
    # Derive the run backend from what actually executed, weakest link first: a
    # single trusted-local case makes the whole run unverified, regardless of the
    # --allow-local-execution flag or any per-case docker_image.
    case_backends = {case.execution_backend for case in case_results}
    if "trusted-local" in case_backends:
        execution_backend: ExecutionBackend = "trusted-local"
    elif "docker" in case_backends:
        execution_backend = "docker"
    else:
        execution_backend = "none"
    executed_cases = sum(
        1 for case in case_results if case.execution_backend in {"docker", "trusted-local"}
    )
    unavailable_cases = sum(1 for case in case_results if case.execution_unavailable)
    # Comparability evidence: count cases per parse status, and flag the run
    # non-exact when any case was salvaged (tolerant/repaired). invalid alone does
    # NOT make a run non-comparable (it is a reviewer-contract failure that scores).
    parse_status_counts: dict[str, int] = {}
    for item in case_results:
        status = item.response.parse_status
        parse_status_counts[status] = parse_status_counts.get(status, 0) + 1
    non_exact_output_used = any(
        item.response.parse_status in {"tolerant", "repaired"} for item in case_results
    )
    # Stamp every case with its training-data exposure cohort. `cases` comes from
    # snapshot.load_and_validate(), so the origin read here is the SNAPSHOT's and
    # is covered by pack_checksum -- not the mutable source tree. The loop covers
    # failed-case placeholders too, so a crashed case is never quietly dropped
    # from the census.
    case_by_id = {case.id: case for case in cases}
    for item in case_results:
        source_case = case_by_id.get(item.case_id)
        if source_case is None:
            continue
        assignment = assign_cohort(
            source_case, manifest.published_date, model_knowledge_cutoff, cutoff_grace_days
        )
        item.exposure_date = assignment.exposure_date
        item.exposure_date_basis = assignment.basis
        item.exposure_cohort = assignment.cohort
        item.exposure_cohort_reason = assignment.reason
    # A single invalid case is a scoreable reviewer-contract failure. Every case
    # invalid is something else: the reviewer never returned usable output at all
    # (a crashing wrapper, a control with no answers for this pack), which would
    # otherwise be published as a legitimate 0.0 alongside reviewers that ran.
    all_output_invalid = produced > 0 and parse_status_counts.get("invalid", 0) == produced
    run = RunResult(
        run_id=run_id,
        benchmark_set=manifest.version,
        reviewer=reviewer.name,
        model=reviewer.model,
        started_at=started,
        completed_at=completed,
        metadata=RunMetadata(
            prompt_version=PROMPT_VERSION,
            benchmark_version=manifest.version,
            model_knowledge_cutoff=model_knowledge_cutoff,
            model_knowledge_cutoff_basis=model_knowledge_cutoff_basis,  # type: ignore[arg-type]
            model_knowledge_cutoff_source=model_knowledge_cutoff_source,
            cutoff_grace_days=cutoff_grace_days,
            reviewer_retrieval=reviewer_retrieval,  # type: ignore[arg-type]
            git_commit=_git_commit(),
            git_dirty=_git_dirty(),
            test_assisted=bool(getattr(reviewer, "reveal_test_output", False)),
            reviewer_oracle_reachable=bool(getattr(reviewer, "oracle_reachable", False)),
            pack_checksum=checksum,
            pack_checksum_verified=checksum_verified,
            # Reaching here with expected_pack_sha256 set means it matched (a
            # mismatch aborts above), so the pack was verified against an external
            # digest, not just its own pack.sha256.
            pack_digest_externally_verified=expected_pack_sha256 is not None,
            snapshot_file_count=snapshot.file_count,
            snapshot_total_bytes=snapshot.total_bytes,
            snapshot_manifest_version=SNAPSHOT_MANIFEST_VERSION,
            snapshot_manifest_digest=snapshot.manifest_digest,
            snapshot_integrity_verified=True,
            reviewer_parse_status_counts=parse_status_counts,
            non_exact_output_used=non_exact_output_used,
        ),
        case_results=case_results,
        total_score=(
            round(sum(item.score for item in case_results) / len(case_results), 2)
            if case_results
            else 0.0
        ),
        budget_stopped_reason=budget_stopped_reason,
        skipped_case_ids=skipped_case_ids,
        schema_version=RUN_SCHEMA_VERSION,
        run_status=_run_status(
            results=produced,
            failed=errored,
            skipped=bool(skipped_case_ids),
            checksum_verified=checksum_verified,
            execution_required=mode != "review",
            executed=executed_cases,
            unavailable=unavailable_cases,
            all_output_invalid=all_output_invalid,
        ),
        execution_backend=execution_backend,
        eligible_case_count=eligible,
        completed_case_count=produced - errored,
        failed_case_count=errored,
        skipped_case_count=len(skipped_case_ids),
        # Coverage is completed / eligible, per the documented invariant: a case
        # that raised inside the harness produced a placeholder, not a covered
        # measurement, so it must not count toward coverage.
        coverage_rate=round((produced - errored) / eligible, 6) if eligible else 0.0,
        mode=mode,
        beta=run_beta,
        deterministic_metrics=(
            aggregate_deterministic_metrics(case_results, run_beta, total_cost, total_latency)
            if mode != "review"
            else None
        ),
        exposure_metrics=(
            aggregate_exposure_metrics(
                case_results,
                metadata=RunMetadata(
                    prompt_version=PROMPT_VERSION,
                    benchmark_version=manifest.version,
                    model_knowledge_cutoff=model_knowledge_cutoff,
                    model_knowledge_cutoff_basis=model_knowledge_cutoff_basis,  # type: ignore[arg-type]
                    model_knowledge_cutoff_source=model_knowledge_cutoff_source,
                    cutoff_grace_days=cutoff_grace_days,
                    reviewer_retrieval=reviewer_retrieval,  # type: ignore[arg-type]
                ),
                pack_checksum=checksum,
                source_labels={
                    case.id: (case.origin.source_label if case.origin else None) for case in cases
                },
            )
            if mode != "review"
            else None
        ),
        bugs_found=sum(item.bug_found for item in case_results),
        correct_files=sum(item.correct_file for item in case_results),
        correct_lines=sum(item.correct_line for item in case_results),
        false_positives=sum(item.false_positive_count for item in case_results),
        total_cost=total_cost,
        total_latency_ms=total_latency,
    )
    # Re-verify the snapshot before sealing any evidence: a successful result is
    # never published if the snapshot changed under the run.
    snapshot.verify()
    write_json_report(run, run_dir / "run.json")
    write_markdown_report(run, run_dir / "report.md")
    write_html_report(run, run_dir / "report.html")
    _write_run_manifest(
        run_dir,
        run,
        reviewer,
        snapshot.source,
        max_wall_seconds=max_wall_seconds,
        max_cost=max_cost,
    )
    # Seal the run's artifacts into a content-addressed bundle (arena verify-run).
    write_bundle_checksums(run_dir)
    shutil.copyfile(run_dir / "run.json", root / "latest.json")
    if persist:
        RunRepository(db_path or database_path()).save(run)
    return run


def _write_run_manifest(
    run_dir: Path,
    run: RunResult,
    reviewer: BaseReviewer,
    benchmark_dir: Path,
    *,
    max_wall_seconds: float | None,
    max_cost: float | None,
) -> None:
    """Everything needed to reproduce or audit the run, with secrets redacted."""
    payload = {
        "harness_version": __version__,
        "harness_git_commit": run.metadata.git_commit,
        "harness_git_dirty": run.metadata.git_dirty,
        "test_assisted": run.metadata.test_assisted,
        "run_id": run.run_id,
        "benchmark_set": run.benchmark_set,
        "benchmark_dir": str(benchmark_dir),
        "pack_checksum": run.metadata.pack_checksum,
        "pack_checksum_verified": run.metadata.pack_checksum_verified,
        "pack_digest_externally_verified": run.metadata.pack_digest_externally_verified,
        "snapshot_file_count": run.metadata.snapshot_file_count,
        "snapshot_total_bytes": run.metadata.snapshot_total_bytes,
        "snapshot_manifest_version": run.metadata.snapshot_manifest_version,
        "snapshot_manifest_digest": run.metadata.snapshot_manifest_digest,
        "snapshot_integrity_verified": run.metadata.snapshot_integrity_verified,
        "reviewer_parse_status_counts": run.metadata.reviewer_parse_status_counts,
        "non_exact_output_used": run.metadata.non_exact_output_used,
        "prompt_version": run.metadata.prompt_version,
        "reviewer": {
            "identifier": reviewer.identifier,
            "name": reviewer.name,
            "model": reviewer.model,
            "config": reviewer.safe_config(),
        },
        "mode": run.mode,
        "beta": run.beta,
        "budgets": {"max_wall_seconds": max_wall_seconds, "max_cost": max_cost},
        "budget_stopped_reason": run.budget_stopped_reason,
        "skipped_case_ids": run.skipped_case_ids,
        "started_at": run.started_at.isoformat(),
        "completed_at": run.completed_at.isoformat(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "cases": [
            {
                "case_id": case.case_id,
                "score": case.score,
                "deterministic_pass": case.deterministic_pass,
                "latency_ms": case.response.latency_ms,
                "estimated_cost": case.response.estimated_cost,
            }
            for case in run.case_results
        ],
    }
    (run_dir / "run_manifest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _tail(output: str, limit: int = 2000) -> str:
    return output[-limit:]
