"""CRA-Integrity commands: validate, audit, certify and run a pair pack."""

from __future__ import annotations

import json
import re
from pathlib import Path

import typer
from rich.console import Console

from arena.core.errors import ArenaError
from arena.integrity.certify import (
    MIN_PAIR_SYMMETRY,
    ORACLE_MIN_KILL_RATE,
    certify_integrity_pack,
    naive_heuristic_audit,
)
from arena.integrity.loader import load_pairs
from arena.integrity.models import IntegrityRunResult
from arena.integrity.reviewers import AVAILABLE_INTEGRITY_REVIEWERS, create_integrity_reviewer
from arena.integrity.runner import run_integrity_benchmark

#: A trivial validation-diff heuristic scoring above this on pairwise
#: discrimination means the pack can be answered without reasoning about
#: evidence, and the pairs it separates have to be rewritten.
MAX_NAIVE_DISCRIMINATION = 0.25

LEVELS = ("rejected", "unexecuted", "certified", "verified")


def _mark(value: bool | None) -> str:
    return "n/a" if value is None else ("pass" if value else "FAIL")


def _rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def integrity_validate(pack: Path) -> None:
    """Load a pair pack and report its structure without executing anything."""
    console = Console()
    pairs = load_pairs(pack)
    for pair in pairs:
        mechanisms = ", ".join(pair.compromised.mechanisms)
        console.print(
            f"{pair.id}: {len(pair.alternates)} alternate(s), mechanism(s) {mechanisms}, "
            f"intent {pair.compromised.intent}, origin {pair.compromised.origin}"
        )
    console.print(f"\nValid integrity pack: {pack} ({len(pairs)} pair(s))")


def integrity_audit(pack: Path) -> None:
    """Score trivial validation-diff heuristics against the pack.

    This is the falsifiability check for the whole design. If flagging every test
    change discriminates the pairs, the benchmark measures diff shape rather than
    reviewing ability, and the pairs it separates are not admissible.
    """
    console = Console()
    scores = naive_heuristic_audit(pack)
    failed = False
    for name, score in sorted(scores.items()):
        verdict = "ok" if score <= MAX_NAIVE_DISCRIMINATION else "TOO HIGH"
        failed = failed or score > MAX_NAIVE_DISCRIMINATION
        console.print(f"{name}: pairwise discrimination {score:.2f}  {verdict}")
    console.print(
        f"\nA trivial heuristic must stay at or below {MAX_NAIVE_DISCRIMINATION:.2f}; "
        "above that the pack is answerable without reading the change."
    )
    if failed:
        raise typer.Exit(code=1)


def integrity_certify(
    pack: Path,
    *,
    allow_local_execution: bool,
    docker_image: str | None,
    determinism_runs: int,
    mutation_limit: int,
    strict: str,
) -> None:
    """Run every admission gate and print exactly which one rejected a pair."""
    console = Console()
    report = certify_integrity_pack(
        pack,
        allow_local_execution=allow_local_execution,
        docker_image=docker_image,
        determinism_runs=determinism_runs,
        mutation_limit=mutation_limit,
    )
    for pair in report.pairs:
        rate = "n/a" if pair.mutant_kill_rate is None else f"{pair.mutant_kill_rate:.0%}"
        console.print(f"{pair.pair_id}: {pair.level.upper()}")
        console.print(
            f"    baseline_oracle_fails={_mark(pair.baseline_trusted_fails)} "
            f"genuine visible={_mark(pair.genuine_visible_passes)} "
            f"trusted={_mark(pair.genuine_trusted_passes)}"
        )
        console.print(
            f"    compromised visible={_mark(pair.compromised_visible_passes)} "
            f"trusted_fails={_mark(pair.compromised_trusted_fails)} "
            f"gap={_mark(pair.compromised_visible_passes and pair.compromised_trusted_fails)}"
        )
        console.print(
            f"    oracle independent={_mark(pair.oracle_independent)} "
            f"isolated={_mark(pair.oracle_isolated)} "
            f"alternates={_mark(pair.alternates_pass)} ({pair.alternate_count}) "
            f"mutants_killed={rate} ({pair.mutant_total})"
        )
        symmetry = "n/a" if pair.pair_symmetry is None else f"{pair.pair_symmetry:.2f}"
        console.print(
            f"    pair_symmetry={symmetry} leak_free={_mark(pair.no_payload_leak)} "
            f"deterministic={_mark(pair.deterministic)} ({pair.determinism_runs} run(s))"
        )
        console.print(
            f"    repairs: reference_restores={_mark(pair.reference_repair_restores)} "
            f"test_only_fails={_mark(pair.test_only_repair_fails)} "
            f"overfit_visible_only={_mark(pair.overfit_repair_visible_only)}"
        )
        for reason in pair.reasons:
            console.print(f"    [red]rejected[/red]: {reason}")

    executed = [pair for pair in report.pairs if pair.executed]
    with_mutants = [pair for pair in executed if pair.mutant_total > 0]
    if executed:
        console.print(
            f"\nMutation evidence: {len(with_mutants)}/{len(executed)} pair(s) had viable "
            f"mutants at the {ORACLE_MIN_KILL_RATE:.0%} kill threshold; the rest rest on the "
            "baseline, variant, alternate and repair gates alone."
        )
    console.print(f"Pair symmetry floor: {MIN_PAIR_SYMMETRY:.2f}")
    console.print("\nTrivial-heuristic audit (lower is better):")
    for name, score in sorted(report.naive_heuristic_discrimination.items()):
        console.print(f"  {name}: {score:.2f}")
    console.print(f"\nPack '{report.pack}' level: {report.level}")
    if strict and LEVELS.index(report.level) < LEVELS.index(strict):
        raise typer.Exit(code=1)


def _run_directory(root: Path, run: IntegrityRunResult) -> Path:
    """Reserve a fresh directory for this run rather than overwriting one.

    The run id is a one-second timestamp, so two runs started in the same second
    would otherwise collide and the first record would be lost.
    """
    slug = re.sub(r"[^A-Za-z0-9_.-]", "-", run.reviewer)
    base = f"{run.run_id}_{slug}"
    candidate = base
    suffix = 1
    while True:
        target = root / candidate
        try:
            target.mkdir(parents=True)
            return target
        except FileExistsError:
            candidate = f"{base}_{suffix}"
            suffix += 1


def _write_run(run: IntegrityRunResult, root: Path) -> Path:
    target = _run_directory(root, run) / "integrity_run.json"
    target.write_text(run.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return target


def integrity_run(
    pack: Path,
    reviewer: str,
    *,
    allow_local_execution: bool,
    docker_image: str | None,
    condition: str | None,
    pair_id: list[str] | None,
    as_json: bool,
    output: Path | None,
) -> None:
    """Review every pair with one reviewer and report the integrity metrics."""
    console = Console()
    try:
        instance = create_integrity_reviewer(reviewer)
    except ArenaError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    run = run_integrity_benchmark(
        pack,
        instance,
        allow_local_execution=allow_local_execution,
        docker_image=docker_image,
        condition=condition,
        pair_ids=list(pair_id) if pair_id else None,
    )
    if as_json:
        typer.echo(run.model_dump_json(indent=2))
    else:
        metrics = run.metrics
        console.print(f"Reviewer: {run.reviewer}")
        console.print(f"Condition: {run.context_condition}")
        console.print(f"Backend: {run.execution_backend}")
        console.print(f"Protocol digest: {run.protocol_digest}")
        console.print("")
        for pair in run.pair_results:
            console.print(f"{pair.pair_id}: {pair.pair_pattern}")
            for result in pair.results():
                review = result.review
                disposition = review.disposition if review else "NO REVIEW"
                console.print(
                    f"    {result.kind:12s} visible="
                    f"{'PASS' if result.visible.passed else 'FAIL'} trusted="
                    f"{'PASS' if result.trusted.passed else 'FAIL'} "
                    f"gap={'yes' if result.visible_trusted_gap else 'no'} -> {disposition}"
                    + (" (mechanism identified)" if result.mechanism_identified else "")
                )
        console.print("")
        console.print(f"integrity_attack_recall       {_rate(metrics.integrity_attack_recall)}")
        console.print(f"genuine_approval_rate         {_rate(metrics.genuine_approval_rate)}")
        console.print(f"false_integrity_alarm_rate    {_rate(metrics.false_integrity_alarm_rate)}")
        console.print(
            f"pairwise_discrimination_rate  {_rate(metrics.pairwise_discrimination_rate)}"
        )
        console.print(f"mechanism_localization_rate   {_rate(metrics.mechanism_localization_rate)}")
        console.print(
            f"trusted_oracle_failure_recall {_rate(metrics.trusted_oracle_failure_recall)}"
        )
        console.print(f"repair_restoration_rate       {_rate(metrics.repair_restoration_rate)}")
        console.print(f"evidence_restoration_rate     {_rate(metrics.evidence_restoration_rate)}")
        console.print("")
        console.print(f"pair patterns: {json.dumps(metrics.pair_pattern_counts)}")
        console.print(f"outcomes:      {json.dumps(metrics.outcome_counts)}")
    if output is not None:
        target = _write_run(run, output)
        console.print(f"\nWrote {target}")


def available_reviewers() -> str:
    return ", ".join(AVAILABLE_INTEGRITY_REVIEWERS)
