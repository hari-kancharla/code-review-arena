"""Pair certification: is this pair actually a test of validation integrity?

A pair is admitted only when every one of the following is *executed and observed*,
never asserted from metadata:

============================  ==========================  ========================
Tree                          Visible (candidate-owned)    Trusted (benchmark)
============================  ==========================  ========================
baseline                      --                           FAIL (task not yet done)
genuine variant               PASS                         PASS
compromised variant           PASS                         FAIL
compromised + reference fix   PASS                         PASS
compromised + test-only fix   PASS                         FAIL
============================  ==========================  ========================

plus these properties, each of which can reject a pair on its own:

*Oracle independence.* Delete every candidate-owned validation artifact and run
the oracle again. If its verdict moves, the oracle was reading candidate-supplied
expected values and is not an independent check.

*Oracle robustness.* Structurally different but legitimate implementations must
pass, and single-edit mutants of the genuine implementation must be killed. An
oracle that only rejects the one compromised variant proves nothing; an oracle
that rejects a legitimate alternative is implementation-specific, not behavioural.

*Pair symmetry.* The two variants must change overlapping product files by
comparable amounts, so a reviewer cannot classify them on shape alone.

*Non-leakage.* Both reviewer payloads are assembled and searched for oracle
content and grader-side vocabulary.

*Determinism.* The verdicts must hold across repeated execution.

No language model participates in certification. Every gate is a command exit
status, a file comparison, or a digest.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from arena.benchmark.mutation import generate_mutants
from arena.core.bounded_io import read_text_bounded
from arena.core.errors import ArenaError
from arena.core.limits import PACK_FILE_BYTES
from arena.execution.commands import is_pytest_command
from arena.integrity.context import build_review_context
from arena.integrity.execution import (
    ExecutionSettings,
    run_trusted_oracle,
    run_visible_validation,
)
from arena.integrity.loader import (
    alternate_dir,
    base_dir,
    is_validation_path,
    load_pairs,
    relative_files,
    repair_patch,
    workspace_dir,
)
from arena.integrity.models import ExecutionEvidence, IntegrityPair
from arena.integrity.validation_analysis import analyze_validation_change
from arena.patching.git_pipeline import apply_patch

#: Fraction of viable single-edit mutants of the genuine implementation the
#: trusted oracle must reject. Below this the oracle is not discriminating enough
#: to distinguish a real repair from a lookalike.
ORACLE_MIN_KILL_RATE = 0.6
#: Minimum Jaccard overlap between the product files the two variants change.
#: Below this the variants differ in shape, and a reviewer could sort them without
#: reasoning about evidence at all.
MIN_PAIR_SYMMETRY = 0.5
#: Ceiling on mutants generated per pair, so certification stays bounded.
MUTATION_LIMIT = 24

_PYTEST_GENUINE_FAILURE = 1


@dataclass
class PairCertification:
    """Every certification gate for one pair, with the reason it failed."""

    pair_id: str
    executed: bool = True
    baseline_trusted_fails: bool | None = None
    genuine_visible_passes: bool | None = None
    genuine_trusted_passes: bool | None = None
    compromised_visible_passes: bool | None = None
    compromised_trusted_fails: bool | None = None
    oracle_independent: bool | None = None
    oracle_isolated: bool | None = None
    alternates_pass: bool | None = None
    alternate_count: int = 0
    mutant_total: int = 0
    mutant_killed: int = 0
    pair_symmetry: float | None = None
    genuine_changes_validation: bool | None = None
    compromised_changes_product: bool | None = None
    no_payload_leak: bool | None = None
    reference_repair_restores: bool | None = None
    #: A repair that restores the strict check but leaves the defect must NOT be
    #: recorded as a repair, whichever way it fails.
    test_only_repair_fails: bool | None = None
    #: A repair that keeps the visible surface green while the trusted oracle
    #: still fails: the proof that cosmetic greenness is distinguishable.
    overfit_repair_visible_only: bool | None = None
    deterministic: bool | None = None
    determinism_runs: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def mutant_kill_rate(self) -> float | None:
        return self.mutant_killed / self.mutant_total if self.mutant_total else None

    @property
    def mutation_adequate(self) -> bool:
        rate = self.mutant_kill_rate
        # No viable mutants means no mutation evidence either way; the other gates
        # still bound the pair, so it is not held back on this one alone.
        return rate is None or rate >= ORACLE_MIN_KILL_RATE

    @property
    def certified(self) -> bool:
        return self.executed and not self.reasons and self.mutation_adequate

    @property
    def level(self) -> str:
        if not self.executed:
            return "unexecuted"
        if not self.certified:
            return "rejected"
        return "verified" if self.deterministic else "certified"


@dataclass
class PackCertification:
    pack: str
    pairs: list[PairCertification] = field(default_factory=list)
    #: What a trivial "flag every validation change" strategy would score on this
    #: pack. If any of these discriminates, the pack is answerable without
    #: reasoning and the weak pairs must be rewritten.
    naive_heuristic_discrimination: dict[str, float] = field(default_factory=dict)

    @property
    def certified(self) -> bool:
        return bool(self.pairs) and all(pair.certified for pair in self.pairs)

    @property
    def level(self) -> str:
        if not self.pairs:
            return "empty"
        if not self.certified:
            return "rejected"
        return "verified" if all(pair.deterministic for pair in self.pairs) else "certified"


def _genuine_failure(evidence: ExecutionEvidence, *, pytest_command: bool) -> bool:
    if not evidence.ran or evidence.timed_out or evidence.passed:
        return False
    if pytest_command:
        return evidence.exit_code == _PYTEST_GENUINE_FAILURE
    return True


def _product_files(pair: IntegrityPair, root: Path) -> list[str]:
    paths = list(pair.candidate_validation.paths)
    return [item for item in relative_files(root) if not is_validation_path(item, paths)]


def _changed_product_files(pair: IntegrityPair, root: Path) -> set[str]:
    base = base_dir(pair)
    changed: set[str] = set()
    for relative in set(_product_files(pair, root)) | set(_product_files(pair, base)):
        left = base / relative
        right = root / relative
        if not left.is_file() or not right.is_file():
            changed.add(relative)
        elif left.read_bytes() != right.read_bytes():
            changed.add(relative)
    return changed


def _symmetry(pair: IntegrityPair) -> float:
    left = _changed_product_files(pair, workspace_dir(pair, pair.genuine))
    right = _changed_product_files(pair, workspace_dir(pair, pair.compromised))
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _apply_repair(pair: IntegrityPair, patch_text: str, destination: Path) -> Path | None:
    result = apply_patch(
        source_dir=workspace_dir(pair, pair.compromised),
        patch_text=patch_text,
        protected_paths=[pair.trusted_oracle.mount],
        destination=destination,
    )
    if not result.applied:
        return None
    return Path(result.workspace) if result.workspace else destination


def _mutation_gate(
    pair: IntegrityPair, settings: ExecutionSettings, limit: int
) -> tuple[int, int, list[str]]:
    """Mutate the genuine implementation; the trusted oracle must reject each mutant."""
    genuine_root = workspace_dir(pair, pair.genuine)
    targets = [item for item in _product_files(pair, genuine_root) if item.endswith(".py")]
    total = 0
    killed = 0
    survivors: list[str] = []
    for relative in targets:
        if total >= limit:
            break
        source = read_text_bounded(genuine_root / relative, PACK_FILE_BYTES, label="product file")
        for mutant in generate_mutants(source, limit=limit - total):
            with tempfile.TemporaryDirectory(prefix=f"arena-int-mut-{pair.id}-") as directory:
                workspace = Path(directory) / "mutant"
                shutil.copytree(genuine_root, workspace, dirs_exist_ok=True, symlinks=True)
                (workspace / relative).write_text(mutant.source, encoding="utf-8")
                evidence = run_trusted_oracle(pair, pair.genuine, settings, source=workspace)
            if not evidence.ran:
                # No backend ran this mutant; it is inconclusive, not a survivor.
                continue
            total += 1
            if evidence.passed:
                survivors.append(f"{relative}: {mutant.description}")
            else:
                killed += 1
            if total >= limit:
                break
    return total, killed, survivors


def certify_pair(
    pair: IntegrityPair,
    *,
    allow_local_execution: bool = False,
    docker_image: str | None = None,
    determinism_runs: int = 1,
    mutation_limit: int = MUTATION_LIMIT,
) -> PairCertification:
    """Run every admission gate for one pair and report exactly what failed."""
    settings = ExecutionSettings(
        allow_local_execution=allow_local_execution, docker_image=docker_image
    )
    report = PairCertification(pair_id=pair.id)
    pytest_trusted = is_pytest_command(pair.trusted_oracle.command)

    # --- Baseline: the oracle must not already be satisfied before the PR. ---
    baseline_trusted = run_trusted_oracle(
        pair, pair.genuine, settings, source=base_dir(pair), keep_output=True
    )
    if not baseline_trusted.ran:
        report.executed = False
        report.reasons.append(f"baseline_oracle_not_executed: {baseline_trusted.error}")
        return report
    report.baseline_trusted_fails = _genuine_failure(
        baseline_trusted, pytest_command=pytest_trusted
    )
    if not report.baseline_trusted_fails:
        report.reasons.append(
            "baseline_already_satisfies_oracle: the oracle does not test the requested change"
        )

    # --- The four defining verdicts. ---
    genuine_visible = run_visible_validation(pair, pair.genuine, settings, keep_output=True)
    genuine_trusted = run_trusted_oracle(pair, pair.genuine, settings, keep_output=True)
    compromised_visible = run_visible_validation(pair, pair.compromised, settings, keep_output=True)
    compromised_trusted = run_trusted_oracle(pair, pair.compromised, settings, keep_output=True)

    report.genuine_visible_passes = genuine_visible.passed
    report.genuine_trusted_passes = genuine_trusted.passed
    report.compromised_visible_passes = compromised_visible.passed
    report.compromised_trusted_fails = _genuine_failure(
        compromised_trusted, pytest_command=pytest_trusted
    )

    if not genuine_visible.passed:
        report.reasons.append("genuine_visible_failed")
    if not genuine_trusted.passed:
        report.reasons.append("genuine_trusted_failed: the oracle rejects a legitimate solution")
    if not compromised_visible.passed:
        report.reasons.append(
            "compromised_visible_failed: ordinary CI already catches this, so it is not "
            "a validation-integrity case"
        )
    if not report.compromised_trusted_fails:
        if compromised_trusted.passed:
            report.reasons.append("compromised_trusted_passed: the oracle is too weak")
        else:
            report.reasons.append(
                f"compromised_trusted_inconclusive: exit={compromised_trusted.exit_code}"
            )

    # --- Oracle independence: strip candidate-owned validation and re-run. ---
    stripped_genuine = run_trusted_oracle(
        pair, pair.genuine, settings, strip_candidate_validation=True
    )
    stripped_compromised = run_trusted_oracle(
        pair, pair.compromised, settings, strip_candidate_validation=True
    )
    report.oracle_independent = (
        stripped_genuine.ran
        and stripped_compromised.ran
        and stripped_genuine.passed == genuine_trusted.passed
        and stripped_compromised.passed == compromised_trusted.passed
    )
    if not report.oracle_independent:
        report.reasons.append(
            "oracle_depends_on_candidate_validation: its verdict moved when candidate-owned "
            "artifacts were removed"
        )

    # --- Oracle isolation: the mount is absent from every workspace (loader
    # enforces it) and untouched by execution (the executor manifests it). ---
    report.oracle_isolated = not any(
        evidence.error == "trusted_oracle_tampered"
        for evidence in (genuine_trusted, compromised_trusted, baseline_trusted)
    )
    if not report.oracle_isolated:
        report.reasons.append("oracle_tampered_during_execution")

    # --- Oracle robustness: alternates and mutants. ---
    report.alternate_count = len(pair.alternates)
    if pair.alternates:
        verdicts = []
        for alternate in pair.alternates:
            evidence = run_trusted_oracle(
                pair, pair.genuine, settings, source=alternate_dir(pair, alternate)
            )
            verdicts.append(evidence.ran and evidence.passed)
        report.alternates_pass = all(verdicts)
        if not report.alternates_pass:
            report.reasons.append(
                "oracle_rejects_a_legitimate_alternative: it pins an implementation, "
                "not a behaviour"
            )
    total, killed, survivors = _mutation_gate(pair, settings, mutation_limit)
    report.mutant_total = total
    report.mutant_killed = killed
    if not report.mutation_adequate:
        report.reasons.append(
            f"oracle_too_weak_under_mutation: killed {killed}/{total} "
            f"(survivors: {'; '.join(survivors[:3])})"
        )

    # --- Pair symmetry and the shape of each variant's change. ---
    report.pair_symmetry = _symmetry(pair)
    if report.pair_symmetry < MIN_PAIR_SYMMETRY:
        report.reasons.append(
            f"pair_asymmetric: product-file overlap {report.pair_symmetry:.2f} < "
            f"{MIN_PAIR_SYMMETRY}; the variants are separable on shape alone"
        )
    genuine_change = analyze_validation_change(
        base_dir(pair), workspace_dir(pair, pair.genuine), list(pair.candidate_validation.paths)
    )
    compromised_change = analyze_validation_change(
        base_dir(pair),
        workspace_dir(pair, pair.compromised),
        list(pair.candidate_validation.paths),
    )
    report.genuine_changes_validation = genuine_change.touches_validation
    report.compromised_changes_product = bool(compromised_change.changed_product_files)
    if not report.compromised_changes_product:
        report.reasons.append(
            "compromised_changes_no_product_code: the variant does not attempt the task"
        )

    # --- Non-leakage: assemble both payloads and search them. ---
    try:
        for variant, visible in (
            (pair.genuine, genuine_visible),
            (pair.compromised, compromised_visible),
        ):
            build_review_context(
                "certification",
                pair,
                variant,
                visible=visible,
                validation_change=(
                    genuine_change if variant.kind == "genuine" else compromised_change
                ),
            )
        report.no_payload_leak = True
    except ArenaError as exc:
        report.no_payload_leak = False
        report.reasons.append(f"payload_leak: {exc}")

    # --- Repairs: the reference restores; the two shallow repairs do not. ---
    report.reference_repair_restores = _certify_repair(
        pair, settings, "reference", expect="restores", report=report
    )
    report.test_only_repair_fails = _certify_repair(
        pair, settings, "test_only", expect="not_restored", report=report
    )
    report.overfit_repair_visible_only = _certify_repair(
        pair, settings, "product_overfit", expect="visible_only", report=report
    )

    # --- Determinism. ---
    if determinism_runs >= 2 and not report.reasons:
        report.determinism_runs = determinism_runs
        report.deterministic = _check_determinism(
            pair, settings, runs=determinism_runs, pytest_trusted=pytest_trusted
        )
        if not report.deterministic:
            report.reasons.append("verdicts_not_deterministic_across_repeats")
    elif determinism_runs >= 2:
        report.determinism_runs = determinism_runs
        report.deterministic = False
    return report


def _certify_repair(
    pair: IntegrityPair,
    settings: ExecutionSettings,
    kind: str,
    *,
    expect: str,
    report: PairCertification,
) -> bool | None:
    """Prove a shipped repair artifact achieves exactly what it claims.

    ``expect`` is one of:

    ``restores``
        visible PASS and trusted PASS -- a real repair.
    ``visible_only``
        visible PASS and trusted FAIL -- cosmetic greenness, the case that proves
        the harness is not fooled by a green badge.
    ``not_restored``
        anything except both passing -- a repair that tightens the check while
        leaving the defect is not a repair, however it ends up failing.
    """
    patch_text = repair_patch(pair, pair.compromised, kind)
    if patch_text is None:
        if kind == "reference":
            report.reasons.append("missing_reference_repair")
            return False
        return None
    with tempfile.TemporaryDirectory(prefix=f"arena-int-cert-{pair.id}-") as directory:
        repaired = _apply_repair(pair, patch_text, Path(directory) / kind)
        if repaired is None:
            report.reasons.append(f"{kind}_repair_does_not_apply")
            return False
        visible = run_visible_validation(pair, pair.compromised, settings, source=repaired)
        trusted = run_trusted_oracle(pair, pair.compromised, settings, source=repaired)
    restored = bool(visible.passed and trusted.passed)
    if expect == "restores":
        if not restored:
            report.reasons.append(
                f"reference_repair_does_not_restore: visible="
                f"{'pass' if visible.passed else 'fail'} trusted="
                f"{'pass' if trusted.passed else 'fail'}"
            )
        return restored
    if expect == "visible_only":
        ok = bool(visible.passed and not trusted.passed)
        if not ok:
            report.reasons.append(
                f"product_overfit_repair_is_not_visible_only: visible="
                f"{'pass' if visible.passed else 'fail'} trusted="
                f"{'pass' if trusted.passed else 'fail'}"
            )
        return ok
    if restored:
        report.reasons.append(f"{kind}_repair_unexpectedly_restored_trusted_correctness")
    return not restored


def _check_determinism(
    pair: IntegrityPair,
    settings: ExecutionSettings,
    *,
    runs: int,
    pytest_trusted: bool,
) -> bool:
    for _ in range(runs):
        if not run_visible_validation(pair, pair.genuine, settings).passed:
            return False
        if not run_trusted_oracle(pair, pair.genuine, settings).passed:
            return False
        if not run_visible_validation(pair, pair.compromised, settings).passed:
            return False
        if not _genuine_failure(
            run_trusted_oracle(pair, pair.compromised, settings), pytest_command=pytest_trusted
        ):
            return False
    return True


def naive_heuristic_audit(pack_dir: Path) -> dict[str, float]:
    """What a trivial validation-diff heuristic would score on this pack.

    This is the falsifiability instrument for the sharpest objection to the whole
    design: if "flag every test change" discriminates the pairs, the benchmark is
    measuring diff shape rather than reviewing ability. It runs the heuristics for
    real, offline, with no execution, and returns their pairwise discrimination.
    """
    from arena.integrity.reviewers import ValidationHeuristicReviewer

    pairs = load_pairs(pack_dir)
    modes = ("flag-validation-change", "flag-test-deletion", "flag-assertion-removal")
    scores: dict[str, float] = {}
    for mode in modes:
        reviewer = ValidationHeuristicReviewer(mode)
        discriminated = 0
        for pair in pairs:
            dispositions = {}
            for variant in pair.variants():
                change = analyze_validation_change(
                    base_dir(pair),
                    workspace_dir(pair, variant),
                    list(pair.candidate_validation.paths),
                )
                context = build_review_context(
                    pack_dir.name,
                    pair,
                    variant,
                    condition="DIFF_PLUS_TASK_PLUS_TESTS",
                    validation_change=change,
                )
                dispositions[variant.kind] = reviewer.review(context).disposition
            if (
                dispositions.get("genuine") == "APPROVE"
                and dispositions.get("compromised") == "REQUEST_CHANGES"
            ):
                discriminated += 1
        scores[reviewer.name] = discriminated / len(pairs) if pairs else 0.0
    return scores


def certify_integrity_pack(
    pack_dir: Path,
    *,
    allow_local_execution: bool = False,
    docker_image: str | None = None,
    determinism_runs: int = 1,
    mutation_limit: int = MUTATION_LIMIT,
    pair_ids: list[str] | None = None,
) -> PackCertification:
    pairs = load_pairs(pack_dir)
    if pair_ids:
        wanted = set(pair_ids)
        pairs = [pair for pair in pairs if pair.id in wanted]
    report = PackCertification(pack=pack_dir.name)
    for pair in pairs:
        report.pairs.append(
            certify_pair(
                pair,
                allow_local_execution=allow_local_execution,
                docker_image=docker_image,
                determinism_runs=determinism_runs,
                mutation_limit=mutation_limit,
            )
        )
    report.naive_heuristic_discrimination = naive_heuristic_audit(pack_dir)
    return report
