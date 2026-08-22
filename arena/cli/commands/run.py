import re
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Literal

import typer
from rich.console import Console

from arena.benchmark.benchmark_runner import run_benchmark
from arena.core import limits
from arena.core.config import runs_path
from arena.core.errors import ArenaError
from arena.core.registry import create_reviewer

_CUTOFF_BASES = {"vendor_documented", "operator_estimate"}
_RETRIEVAL_MODES = {"none", "enabled", "unknown"}


def _validate_cutoff_claim(
    cutoff: str | None, basis: str | None, source: str | None, retrieval: str
) -> None:
    """Reject an incoherent exposure declaration before any run directory exists.

    A cutoff is an operator claim the harness cannot verify. It is accepted only
    complete -- with the basis for the claim and a citation for it -- because a
    bare date would let a run publish a cohort split resting on an assertion
    nobody can attribute or check.
    """
    if retrieval not in _RETRIEVAL_MODES:
        Console(stderr=True).print(
            f"[red]ERROR[/red] --reviewer-retrieval must be one of {sorted(_RETRIEVAL_MODES)}"
        )
        raise typer.Exit(code=1)
    declared = [cutoff, basis, source]
    if all(value is None for value in declared):
        return
    if any(value is None for value in declared):
        Console(stderr=True).print(
            "[red]ERROR[/red] a knowledge-cutoff claim needs all three of "
            "--model-knowledge-cutoff, --model-cutoff-basis and --model-cutoff-source"
        )
        raise typer.Exit(code=1)
    if basis not in _CUTOFF_BASES:
        Console(stderr=True).print(
            f"[red]ERROR[/red] --model-cutoff-basis must be one of {sorted(_CUTOFF_BASES)}"
        )
        raise typer.Exit(code=1)
    assert cutoff is not None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", cutoff):
        Console(stderr=True).print("[red]ERROR[/red] --model-knowledge-cutoff must be YYYY-MM-DD")
        raise typer.Exit(code=1)
    try:
        parsed = date.fromisoformat(cutoff)
    except ValueError:
        Console(stderr=True).print("[red]ERROR[/red] --model-knowledge-cutoff is not a real date")
        raise typer.Exit(code=1) from None
    if parsed > date.today():
        # A future cutoff would sweep every case into the pre-cutoff arm and
        # leave nothing to compare it against.
        Console(stderr=True).print("[red]ERROR[/red] --model-knowledge-cutoff is in the future")
        raise typer.Exit(code=1)


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
    model_knowledge_cutoff: str | None = None,
    model_cutoff_basis: str | None = None,
    model_cutoff_source: str | None = None,
    cutoff_grace_days: int = limits.DEFAULT_CUTOFF_GRACE_DAYS,
    reviewer_retrieval: str = "unknown",
) -> None:
    # Enforce the centralized string caps before reviewer creation (Typer cannot
    # express max-length on these string options directly).
    for label, value, cap in (
        ("reviewer", reviewer_spec, limits.REVIEWER_ID_LEN),
        ("model", model, limits.MODEL_ID_LEN),
        ("command", command, limits.COMMAND_LEN),
        ("model-cutoff-source", model_cutoff_source, limits.MODEL_CUTOFF_SOURCE_LEN),
    ):
        if value is not None and len(value) > cap:
            Console(stderr=True).print(f"[red]ERROR[/red] {label} exceeds {cap} characters")
            raise typer.Exit(code=1)
    _validate_cutoff_claim(
        model_knowledge_cutoff, model_cutoff_basis, model_cutoff_source, reviewer_retrieval
    )
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
            model_knowledge_cutoff=model_knowledge_cutoff,
            model_knowledge_cutoff_basis=model_cutoff_basis,
            model_knowledge_cutoff_source=model_cutoff_source,
            cutoff_grace_days=cutoff_grace_days,
            reviewer_retrieval=reviewer_retrieval,
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
    unavailable = sum(1 for case in result.case_results if case.execution_unavailable)
    if unavailable:
        ran_backends = {"docker", "trusted-local"}
        executed = sum(1 for case in result.case_results if case.execution_backend in ran_backends)
        # Name the actual blocker. A missing test runner is not fixed by any of
        # the Docker/--allow-local-execution advice, so pointing there sends the
        # reader down the wrong path entirely.
        missing_runners = sorted(
            {
                (case.test_stderr_tail or "").split(":", 1)[1].strip()
                for case in result.case_results
                if (case.test_stderr_tail or "").startswith("test_runner_unavailable:")
            }
        )
        if missing_runners:
            # The backslash escapes rich markup: an unescaped "[run]" is parsed as
            # a style tag and vanishes, printing an install command that would not
            # install the runner.
            hint = (
                f"the test runner {', '.join(missing_runners)} is not installed in this "
                r'environment; install it (pip install "codereview-arena\[run]")'
            )
        elif executed == 0:
            hint = "start Docker, or pass --allow-local-execution to run image-less cases locally"
        else:
            hint = "some cases ran and some could not"
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
        # Count over the SAME population the rate is computed on. Counting every
        # case here printed a fraction that disagreed with the rate beside it and
        # silently re-included the validator-only passes the rate excludes.
        eligible = [
            item
            for item in result.case_results
            if item.deterministic_case_score and item.deterministic_case_score.validation_eligible
        ]
        deterministic_passes = sum(item.deterministic_pass is True for item in eligible)
        excluded = result.case_count - len(eligible)
        excluded_note = f" ({excluded} not execution-backed)" if excluded else ""
        Console().print(f"Detection: detection_f_beta={metrics.detection_f_beta:.3f}")
        Console().print(
            f"Validation: passes={deterministic_passes}/{len(eligible)}{excluded_note}, "
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
        exposure = result.exposure_metrics
        if exposure is not None:
            sizes = {item.cohort: item.eligible_case_count for item in exposure.cohorts}
            band = (
                f"cutoff {exposure.declared_cutoff}, {exposure.cutoff_basis}, "
                f"+/-{exposure.cutoff_grace_days}d"
                if exposure.declared_cutoff
                else "no cutoff declared"
            )
            Console().print(
                "Exposure: "
                f"pre={sizes.get('pre_cutoff', 0)} post={sizes.get('post_cutoff', 0)} "
                f"undetermined={sizes.get('undetermined', 0)} "
                f"n/a={sizes.get('not_applicable', 0)} ({band})"
            )
            if exposure.suppression_reasons:
                # Say why there is no number, rather than leaving a reader to
                # assume one was not worth computing.
                Console(stderr=True).print(
                    "[yellow]NOTE[/yellow] no exposure difference published: "
                    f"{', '.join(exposure.suppression_reasons)}. Cohort rates above are "
                    "still exact; the headline validated_case_rate mixes cohorts."
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
