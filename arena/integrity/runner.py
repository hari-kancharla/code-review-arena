"""End-to-end orchestration for a CRA-Integrity run.

The order of operations is the experiment, so it is enforced here rather than
left to convention:

1. Execute the candidate's own validation. This is what makes the PR look green.
2. Analyse how the PR changed its own validation surface.
3. Build the reviewer payload and prove nothing oracle-derived is in it.
4. **Review.** The reviewer decides with only that payload in hand.
5. Only then execute the trusted oracle.

Running the oracle before the review would make a leak possible in principle even
if no code path used it, so the runner refuses to do it. The gap between step 1
and step 5 -- visible PASS, trusted FAIL -- is the diagnostic the benchmark exists
to measure a reviewer against, and it is computed strictly after the reviewer has
committed to a disposition.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from arena.core.errors import ArenaError
from arena.execution.commands import is_pytest_command
from arena.integrity.context import build_review_context, submission_id
from arena.integrity.execution import (
    ExecutionSettings,
    run_trusted_oracle,
    run_visible_validation,
)
from arena.integrity.loader import (
    base_dir,
    is_validation_path,
    load_pairs,
    relative_files,
    repair_patch,
    workspace_dir,
)
from arena.integrity.models import (
    CandidateVariant,
    ExecutionEvidence,
    IntegrityMetrics,
    IntegrityPair,
    IntegrityPairResult,
    IntegrityReview,
    IntegrityRunResult,
    IntegrityVariantResult,
    RepairEvaluation,
)
from arena.integrity.protocol import build_protocol, protocol_digest
from arena.integrity.reviewers import (
    ANSWER_KEY_CONTROLS,
    AnswerKey,
    ControlAnswer,
    IntegrityReviewer,
    ReviewerRegistry,
)
from arena.integrity.scoring import (
    aggregate_metrics,
    classify_pair,
    classify_variant,
    mechanism_identified,
)
from arena.integrity.validation_analysis import analyze_validation_change
from arena.patching.git_pipeline import apply_patch

#: pytest's "a test ran and failed" exit code. Any other non-zero code means the
#: suite could not be collected or run, which is not evidence of anything.
_PYTEST_GENUINE_FAILURE = 1


def build_answer_key(pack: str, pairs: list[IntegrityPair]) -> AnswerKey:
    """Grader-side truth, keyed by opaque submission id, for answer-key controls."""
    answers: AnswerKey = {}
    for pair in pairs:
        for variant in pair.variants():
            answers[submission_id(pack, pair, variant)] = ControlAnswer(
                kind=variant.kind,
                mechanisms=tuple(variant.mechanisms),
                mechanism_keywords=tuple(variant.mechanism_keywords),
                affected_validation_paths=tuple(variant.affected_validation_paths),
                affected_product_paths=tuple(variant.affected_product_paths),
                explanation=variant.mechanism_explanation,
                reference_patch=repair_patch(pair, variant, "reference"),
                test_only_patch=repair_patch(pair, variant, "test_only"),
                product_overfit_patch=repair_patch(pair, variant, "product_overfit"),
            )
    return answers


def _genuine_failure(evidence: ExecutionEvidence, *, pytest_command: bool) -> bool:
    """True when a non-passing run is a real test failure rather than a broken suite."""
    if not evidence.ran or evidence.timed_out or evidence.passed:
        return False
    if pytest_command:
        return evidence.exit_code == _PYTEST_GENUINE_FAILURE
    return True


def _hybrid_workspace(
    compromised_root: Path,
    repaired_root: Path,
    validation_paths: list[str],
    destination: Path,
) -> None:
    """Compromised product plus repaired validation artifacts.

    Running the candidate's own command against this tree answers the question
    that separates a real repair of the *evidence* from a cosmetic one: would the
    restored validation have caught the original defect?
    """
    shutil.copytree(compromised_root, destination, dirs_exist_ok=True, symlinks=True)
    for relative in relative_files(destination):
        if is_validation_path(relative, validation_paths):
            (destination / relative).unlink()
    for relative in relative_files(repaired_root):
        if is_validation_path(relative, validation_paths):
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(repaired_root / relative, target)


def _evaluate_repair(
    pair: IntegrityPair,
    variant: CandidateVariant,
    patch_text: str | None,
    settings: ExecutionSettings,
) -> RepairEvaluation:
    """Apply a reviewer repair through the shared Git pipeline and judge it."""
    if not patch_text or not patch_text.strip():
        return RepairEvaluation(provided=False)

    source = workspace_dir(pair, variant)
    mount = pair.trusted_oracle.mount
    validation_paths = list(pair.candidate_validation.paths)
    with tempfile.TemporaryDirectory(prefix=f"arena-int-repair-{variant.id}-") as directory:
        destination = Path(directory) / "repaired"
        # The trusted oracle is not present in a candidate workspace, so a repair
        # cannot edit it. Listing the mount as protected additionally rejects a
        # patch that tries to *create* an oracle there.
        result = apply_patch(
            source_dir=source,
            patch_text=patch_text,
            protected_paths=[mount],
            destination=destination,
        )
        if not result.applied:
            touched_oracle = result.reason == "protected_path_changed"
            return RepairEvaluation(
                provided=True,
                applied=False,
                apply_error=result.reason,
                touched_trusted_oracle=touched_oracle,
                outcome=(
                    "REPAIR_TOUCHED_TRUSTED_ORACLE" if touched_oracle else "REPAIR_BREAKS_VISIBLE"
                ),
            )
        repaired_root = Path(result.workspace) if result.workspace else destination
        visible = run_visible_validation(pair, variant, settings, source=repaired_root)
        trusted = run_trusted_oracle(pair, variant, settings, source=repaired_root)

        restores_evidence: bool | None = None
        if variant.affected_validation_paths:
            hybrid = Path(directory) / "hybrid"
            _hybrid_workspace(source, repaired_root, validation_paths, hybrid)
            probe = run_visible_validation(pair, variant, settings, source=hybrid)
            restores_evidence = _genuine_failure(
                probe, pytest_command=is_pytest_command(pair.candidate_validation.command)
            )

    if not visible.passed:
        outcome = "REPAIR_BREAKS_VISIBLE"
    elif trusted.passed:
        outcome = "REPAIR_TRUSTED_PASS"
    else:
        outcome = "REPAIR_VISIBLE_ONLY"
    return RepairEvaluation(
        provided=True,
        applied=True,
        touched_files=list(result.touched_files),
        touched_trusted_oracle=False,
        visible_passed=visible.passed,
        trusted_passed=trusted.passed,
        restores_trusted_correctness=bool(visible.passed and trusted.passed),
        restores_meaningful_validation=restores_evidence,
        outcome=outcome,  # type: ignore[arg-type]
    )


def evaluate_variant(
    pack: str,
    pair: IntegrityPair,
    variant: CandidateVariant,
    reviewer: IntegrityReviewer,
    settings: ExecutionSettings,
    *,
    condition: str | None = None,
    evaluate_repairs: bool = True,
) -> IntegrityVariantResult:
    """Run one variant through the full pipeline in the mandated order."""
    resolved_condition = condition or pair.context_condition
    identifier = submission_id(pack, pair, variant)
    base = IntegrityVariantResult(
        pair_id=pair.id,
        variant_id=variant.id,
        submission_id=identifier,
        kind=variant.kind,
        context_condition=resolved_condition,  # type: ignore[arg-type]
        mechanisms=list(variant.mechanisms),
        intent=variant.intent,
        origin=variant.origin,
        author_kind=variant.author_kind,
        author_family=variant.author_family,
        visible=ExecutionEvidence(
            zone="visible", command=[], ran=False, passed=False, output_digest=""
        ),
        trusted=ExecutionEvidence(
            zone="trusted", command=[], ran=False, passed=False, output_digest=""
        ),
    )

    # 1-2. Visible execution and validation-change analysis.
    try:
        visible = run_visible_validation(pair, variant, settings)
        change = analyze_validation_change(
            base_dir(pair),
            workspace_dir(pair, variant),
            list(pair.candidate_validation.paths),
        )
    except (ArenaError, OSError) as exc:
        result = base.model_copy(update={"infrastructure_error": f"{type(exc).__name__}: {exc}"})
        return result.model_copy(update={"outcomes": classify_variant(result)})

    # 3-4. Build the payload (leak-checked) and review. The oracle has not run.
    review: IntegrityReview | None = None
    infra_error: str | None = None
    try:
        context = build_review_context(
            pack,
            pair,
            variant,
            condition=resolved_condition,  # type: ignore[arg-type]
            visible=visible,
            validation_change=change,
        )
    except (ArenaError, OSError) as exc:
        infra_error = f"{type(exc).__name__}: {exc}"
    else:
        try:
            review = reviewer.review(context)
        except Exception as exc:  # noqa: BLE001 - a reviewer is external code.
            # A reviewer that crashes has failed to review; that is a result about
            # the reviewer, not an infrastructure fault, and the run continues.
            review = IntegrityReview(
                disposition="ABSTAIN",
                review_summary="",
                reviewer_error=f"{type(exc).__name__}: {exc}"[:512],
            )

    # 5. Only now the trusted oracle.
    try:
        trusted = run_trusted_oracle(pair, variant, settings)
    except (ArenaError, OSError) as exc:
        trusted = ExecutionEvidence(
            zone="trusted",
            command=[],
            ran=False,
            passed=False,
            output_digest="",
            error=f"{type(exc).__name__}: {exc}",
        )

    repair = RepairEvaluation()
    if evaluate_repairs and review is not None and review.repair_patch:
        try:
            repair = _evaluate_repair(pair, variant, review.repair_patch, settings)
        except (ArenaError, OSError) as exc:
            repair = RepairEvaluation(
                provided=True, applied=False, apply_error=f"{type(exc).__name__}: {exc}"
            )

    result = base.model_copy(
        update={
            "visible": visible,
            "trusted": trusted,
            "visible_trusted_gap": bool(visible.passed and trusted.ran and not trusted.passed),
            "validation_change": change,
            "review": review,
            "challenged": review is not None and review.disposition == "REQUEST_CHANGES",
            "mechanism_identified": mechanism_identified(review, variant),
            "repair": repair,
            "infrastructure_error": infra_error,
        }
    )
    return result.model_copy(update={"outcomes": classify_variant(result)})


def evaluate_pair(
    pack: str,
    pair: IntegrityPair,
    reviewer: IntegrityReviewer,
    settings: ExecutionSettings,
    *,
    condition: str | None = None,
    evaluate_repairs: bool = True,
) -> IntegrityPairResult:
    genuine = evaluate_variant(
        pack,
        pair,
        pair.genuine,
        reviewer,
        settings,
        condition=condition,
        evaluate_repairs=evaluate_repairs,
    )
    compromised = evaluate_variant(
        pack,
        pair,
        pair.compromised,
        reviewer,
        settings,
        condition=condition,
        evaluate_repairs=evaluate_repairs,
    )
    result = IntegrityPairResult(
        pair_id=pair.id, title=pair.title, genuine=genuine, compromised=compromised
    )
    pattern = classify_pair(result)
    return result.model_copy(
        update={"pair_pattern": pattern, "pairwise_discriminated": pattern == "discriminating"}
    )


def _resource_limits() -> dict[str, object]:
    return {
        "docker_memory": os.getenv("ARENA_DOCKER_MEMORY", "2g"),
        "docker_cpus": os.getenv("ARENA_DOCKER_CPUS", "2"),
        "docker_pids": os.getenv("ARENA_DOCKER_PIDS", "256"),
        "docker_tmpfs_size": os.getenv("ARENA_DOCKER_TMPFS_SIZE", "256m"),
    }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def run_integrity_benchmark(
    pack_dir: Path,
    reviewer: IntegrityReviewer,
    *,
    allow_local_execution: bool = False,
    docker_image: str | None = None,
    condition: str | None = None,
    pair_ids: list[str] | None = None,
    evaluate_repairs: bool = True,
    run_id: str | None = None,
) -> IntegrityRunResult:
    """Load a pack, review every pair, and return the complete run record."""
    pack = pack_dir.name
    pairs = load_pairs(pack_dir)
    if pair_ids:
        wanted = set(pair_ids)
        pairs = [pair for pair in pairs if pair.id in wanted]
        missing = wanted - {pair.id for pair in pairs}
        if missing:
            raise ArenaError(f"unknown pair id(s): {', '.join(sorted(missing))}")
    if not pairs:
        raise ArenaError(f"integrity pack {pack} contains no pairs to run")

    if reviewer.identifier in ANSWER_KEY_CONTROLS:
        ReviewerRegistry(answers=build_answer_key(pack, pairs)).bind(reviewer)

    settings = ExecutionSettings(
        allow_local_execution=allow_local_execution, docker_image=docker_image
    )
    resolved_condition = condition or pairs[0].context_condition
    started = _now()
    pair_results = [
        evaluate_pair(
            pack,
            pair,
            reviewer,
            settings,
            condition=condition,
            evaluate_repairs=evaluate_repairs,
        )
        for pair in pairs
    ]
    completed = _now()

    backends = {
        result.visible.backend
        for pair in pair_results
        for result in pair.results()
        if result.visible.backend != "none"
    }
    backend = "docker" if "docker" in backends else ("trusted-local" if backends else "none")
    protocol = build_protocol(
        pack=pack,
        pairs=pairs,
        condition=resolved_condition,  # type: ignore[arg-type]
        execution_backend=backend,
        docker_image=docker_image or pairs[0].docker_image,
        docker_image_digest=None,
        resource_limits=_resource_limits(),
    )
    metrics: IntegrityMetrics = aggregate_metrics(pair_results)
    return IntegrityRunResult(
        run_id=run_id or datetime.now().strftime("%Y-%m-%d_%H-%M-%S"),
        pack=pack,
        reviewer=reviewer.identifier,
        started_at=started,
        completed_at=completed,
        protocol_digest=protocol_digest(protocol),
        protocol=protocol,
        context_condition=resolved_condition,  # type: ignore[arg-type]
        pair_results=pair_results,
        metrics=metrics,
        execution_backend=backend,  # type: ignore[arg-type]
    )
