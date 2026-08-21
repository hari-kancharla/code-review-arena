from collections import Counter
from pathlib import Path
from typing import Literal

import typer
from rich.console import Console

from arena.benchmark.benchmark_runner import run_benchmark
from arena.core import limits
from arena.core.config import runs_path
from arena.core.errors import ArenaError
from arena.core.registry import create_reviewer


def run(
    benchmark_set: Path,
    reviewer_spec: str,
    mode: Literal["review", "patch", "full"],
    beta: float | None,
    allow_local_execution: bool,
    command: str | None,
    reviewer_timeout_seconds: int,
    as_json: bool = False,
    reveal_metadata: bool = False,
    enable_repair: bool = False,
    reveal_test_output: bool = False,
    max_wall_seconds: float | None = None,
    max_cost: float | None = None,
    model: str | None = None,
    expected_pack_sha256: str | None = None,
) -> None:
    # Enforce the centralized string caps before reviewer creation (Typer cannot
    # express max-length on these string options directly).
    for label, value, cap in (
        ("reviewer", reviewer_spec, limits.REVIEWER_ID_LEN),
        ("model", model, limits.MODEL_ID_LEN),
        ("command", command, limits.COMMAND_LEN),
    ):
        if value is not None and len(value) > cap:
            Console(stderr=True).print(f"[red]ERROR[/red] {label} exceeds {cap} characters")
            raise typer.Exit(code=1)
    try:
        reviewer = create_reviewer(
            reviewer_spec,
            command=command,
            model=model,
            reviewer_timeout_seconds=reviewer_timeout_seconds,
            reveal_metadata=reveal_metadata,
            enable_repair=enable_repair,
            reveal_test_output=reveal_test_output,
        )
        result = run_benchmark(
            benchmark_set,
            reviewer,
            mode=mode,
            beta=beta,
            allow_local_execution=allow_local_execution,
            max_wall_seconds=max_wall_seconds,
            max_cost=max_cost,
            expected_pack_sha256=expected_pack_sha256,
        )
    except ArenaError as exc:
        Console(stderr=True).print(f"[red]ERROR[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if result.budget_stopped_reason:
        Console(stderr=True).print(
            f"[yellow]Stopped early[/yellow] {result.budget_stopped_reason}; "
            f"skipped {len(result.skipped_case_ids)} case(s). Partial results recorded."
        )
    if result.metadata.pack_checksum_verified is False:
        Console(stderr=True).print(
            "[yellow]WARNING[/yellow] benchmark pack content does not match its stored "
            "pack.sha256; results may come from a tampered pack."
        )
    if result.metadata.non_exact_output_used:
        counts = dict(result.metadata.reviewer_parse_status_counts)
        Console(stderr=True).print(
            "[yellow]WARNING[/yellow] development salvage was used (tolerant or repaired "
            "reviewer output): this run is NON-COMPARABLE by default and excluded from the "
            f"default leaderboard. parse status counts: {counts}."
        )
    # Unparseable output is recorded in run.json but was previously never shown, so a
    # reviewer that failed on every case read as a reviewer that simply found nothing.
    parse_counts = dict(result.metadata.reviewer_parse_status_counts)
    invalid_cases = parse_counts.get("invalid", 0)
    if invalid_cases:
        produced = sum(parse_counts.values())
        detail = (
            "the reviewer never returned usable output; this is a broken reviewer, not a "
            "score of zero"
            if invalid_cases == produced
            else "those cases scored as reviewer-contract failures"
        )
        Console(stderr=True).print(
            f"[yellow]WARNING[/yellow] {invalid_cases}/{produced} case(s) produced unparseable "
            f"reviewer output: {detail}. parse status counts: {parse_counts}."
        )
    unavailable = sum(1 for case in result.case_results if case.execution_unavailable)
    if unavailable:
        ran_backends = {"docker", "trusted-local"}
        executed = sum(1 for case in result.case_results if case.execution_backend in ran_backends)
        hint = (
            "start Docker, or pass --allow-local-execution to run image-less cases locally"
            if executed == 0
            else "some cases ran and some could not"
        )
        Console(stderr=True).print(
            f"[yellow]WARNING[/yellow] {unavailable} case(s) could not execute (no available "
            f"backend); run_status={result.run_status}. Repair was not judged: {hint}."
        )
    # A run that produced no results, or needed execution it never got, is not a
    # trustworthy measurement; exit nonzero so scripted callers do not read it as a
    # clean pass (matching certify-pack / verify-run / pack-hash).
    invalid_run = result.run_status in {"invalid", "failed"}
    if invalid_run and not unavailable:
        Console(stderr=True).print(
            f"[red]ERROR[/red] run_status={result.run_status}: this run is not a "
            "trustworthy measurement."
        )

    if as_json:
        typer.echo(result.model_dump_json(indent=2))
        if invalid_run:
            raise typer.Exit(code=1)
        return

    Console().print(
        f"[green]Completed[/green] {result.run_id}: review_quality_score={result.total_score:.1f}, "
        f"bugs={result.bugs_found}/{result.case_count}, false_positives={result.false_positives}"
    )
    if result.deterministic_metrics:
        metrics = result.deterministic_metrics
        deterministic_passes = sum(item.deterministic_pass is True for item in result.case_results)
        Console().print(f"Detection: detection_f_beta={metrics.detection_f_beta:.3f}")
        Console().print(
            f"Validation: passes={deterministic_passes}/{result.case_count}, "
            f"validated_case_rate={_format_rate(metrics.validated_case_rate)}, "
            f"validated_f_beta={metrics.validated_f_beta:.3f} (deprecated), "
            f"patch_apply_rate={_format_rate(metrics.patch_apply_rate)}, "
            f"structural_pass_rate={_format_rate(metrics.structural_pass_rate)}"
        )
        Console().print(
            "Dimensions: "
            f"review_accuracy(bug_completeness)={_format_rate(metrics.bug_completeness_rate)}, "
            f"repair_success(complete_repair)={_format_rate(metrics.complete_repair_rate)}, "
            f"trustworthiness(supported_claims)={_format_rate(metrics.supported_claim_rate)}"
        )
        confidence = Counter(
            item.repair_confidence for item in result.case_results if item.repair_confidence
        )
        Console().print(
            "Repair confidence: "
            f"strong={confidence['strong']}, basic={confidence['basic']}, "
            f"unvalidated={confidence['unvalidated']}"
        )
    Console().print(f"Reports: {runs_path() / result.run_id}/")
    if invalid_run:
        raise typer.Exit(code=1)


def _format_rate(value: float | None) -> str:
    return f"{value:.1%}" if value is not None else "n/a"
