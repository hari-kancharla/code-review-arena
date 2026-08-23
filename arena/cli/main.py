"""Command-line application for CodeReview Arena."""

from pathlib import Path
from typing import Literal

import typer

from arena.cli.commands.audit_report import audit_report as audit_report_command
from arena.cli.commands.import_fix import import_fix_command
from arena.cli.commands.integrity import (
    available_reviewers,
)
from arena.cli.commands.integrity import (
    integrity_audit as integrity_audit_command,
)
from arena.cli.commands.integrity import (
    integrity_certify as integrity_certify_command,
)
from arena.cli.commands.integrity import (
    integrity_run as integrity_run_command,
)
from arena.cli.commands.integrity import (
    integrity_validate as integrity_validate_command,
)
from arena.cli.commands.leaderboard import leaderboard as leaderboard_command
from arena.cli.commands.list_cases import list_cases as list_cases_command
from arena.cli.commands.report import report as report_command
from arena.cli.commands.run import run as run_command
from arena.cli.commands.validate import validate as validate_command
from arena.cli.commands.verify_reviewer import verify_reviewer as verify_reviewer_command
from arena.core import limits
from arena.core.config import DEFAULT_BENCHMARK_SET, DEFAULT_RUNS_DIR, resolve_benchmark_path

app = typer.Typer(help="Benchmark AI code reviewers on realistic pull-request bugs.")


@app.command("list-cases")
def list_cases(benchmark_set: Path = typer.Argument(DEFAULT_BENCHMARK_SET)) -> None:
    list_cases_command(resolve_benchmark_path(benchmark_set))


@app.command()
def validate(benchmark_set: Path = typer.Argument(DEFAULT_BENCHMARK_SET)) -> None:
    validate_command(resolve_benchmark_path(benchmark_set))


@app.command("import-fix")
def import_fix(
    repo: Path = typer.Option(..., "--repo", help="Local Git repository (worktree or bare)."),
    buggy_commit: str = typer.Option(..., "--buggy-commit", help="Full buggy commit object id."),
    fixed_commit: str = typer.Option(..., "--fixed-commit", help="Full fixed commit object id."),
    spec: Path = typer.Option(..., "--spec", help="Strict import-spec YAML."),
    output: Path = typer.Option(..., "--output", help="Nonexistent output pack directory."),
    source_label: str | None = typer.Option(
        None, "--source-label", help="Stable source label (e.g. owner/repo); never a local path."
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit a machine-readable report."),
) -> None:
    """Convert a local buggy/fixed commit pair into a candidate reverse-fix pack.

    Local-only, offline, deterministic, non-AI. Reads committed Git objects only
    (never the working tree); does not run tests, certify, or publish.
    """
    import_fix_command(
        repo=repo,
        buggy_commit=buggy_commit,
        fixed_commit=fixed_commit,
        spec=spec,
        output=output,
        source_label=source_label,
        json_output=json_output,
    )


@app.command()
def run(
    benchmark_set: Path = typer.Argument(DEFAULT_BENCHMARK_SET),
    reviewer: str = typer.Option("control:perfect", "--reviewer"),
    mode: Literal["review", "patch", "full"] = typer.Option("review", "--mode"),
    beta: float | None = typer.Option(None, "--beta", min=0.01, max=limits.BETA_MAX),
    allow_local_execution: bool = typer.Option(False, "--allow-local-execution"),
    command: str | None = typer.Option(None, "--command"),
    model: str | None = typer.Option(
        None,
        "--model",
        help="Model name for openai:/http: reviewers (e.g. llama3). "
        "Falls back to ARENA_HTTP_MODEL.",
    ),
    reviewer_timeout_seconds: int = typer.Option(
        120, "--reviewer-timeout-seconds", min=1, max=limits.REVIEWER_TIMEOUT_SECONDS_MAX
    ),
    reveal_metadata: bool = typer.Option(
        False,
        "--reveal-metadata",
        help="Include case title/description/category/severity in the reviewer payload. "
        "Debugging only: scored runs should stay blind.",
    ),
    reveal_test_output: bool = typer.Option(
        False,
        "--reveal-test-output",
        help="Include pre-patch test and static-analysis output in the reviewer payload "
        "(an openly test-assisted run, recorded as test_assisted in metadata). Off by "
        "default: a blind run must not see failing-test expected values.",
    ),
    enable_repair: bool = typer.Option(
        False,
        "--enable-repair",
        help="Development-only deterministic salvage of malformed reviewer JSON. Any "
        "tolerant or repaired case makes the run NON-COMPARABLE by default (excluded "
        "from the default leaderboard); the raw output is always retained. Off by "
        "default, where only exact output is accepted.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit the run result as JSON to stdout."),
    max_wall_seconds: float | None = typer.Option(
        None,
        "--max-wall-seconds",
        min=0,
        max=limits.API_WALL_SECONDS_MAX,
        help="Stop scheduling new cases once the run has taken this long.",
    ),
    max_cost: float | None = typer.Option(
        None,
        "--max-cost",
        min=0,
        max=limits.API_COST_MAX,
        help="Stop scheduling new cases once estimated reviewer cost reaches this budget.",
    ),
    expected_pack_sha256: str | None = typer.Option(
        None,
        "--expected-pack-sha256",
        help="Abort unless the pack's content checksum equals this digest. Pin it from "
        "an out-of-band source (a signed release) since pack.sha256 lives inside the pack.",
    ),
) -> None:
    run_command(
        resolve_benchmark_path(benchmark_set),
        reviewer,
        mode,
        beta,
        allow_local_execution,
        command,
        reviewer_timeout_seconds,
        as_json,
        reveal_metadata=reveal_metadata,
        enable_repair=enable_repair,
        reveal_test_output=reveal_test_output,
        max_wall_seconds=max_wall_seconds,
        max_cost=max_cost,
        model=model,
        expected_pack_sha256=expected_pack_sha256,
    )


@app.command("mutation-test")
def mutation_test(
    benchmark_set: Path = typer.Argument(DEFAULT_BENCHMARK_SET),
    allow_local_execution: bool = typer.Option(False, "--allow-local-execution"),
    limit: int = typer.Option(20, "--limit", min=1, help="Max mutants per case."),
) -> None:
    """Mutate each case's solution and report how many mutants its tests kill."""
    from rich.console import Console

    from arena.benchmark.mutation import run_mutation_test
    from arena.benchmark.snapshot import snapshot_pack

    console = Console()
    # Mutate the solution from the immutable snapshot, not the mutable source.
    with snapshot_pack(resolve_benchmark_path(benchmark_set)) as snapshot:
        for case in snapshot.load():
            if not case.execution.run_tests or not case.execution.test_command:
                console.print(f"{case.id}: skipped (no executable tests)")
                continue
            result = run_mutation_test(
                case, allow_local_execution=allow_local_execution, limit=limit
            )
            rate = f"{result.kill_rate:.0%}" if result.kill_rate is not None else "n/a"
            console.print(f"{case.id}: mutant_kill_rate={rate} ({result.killed}/{result.total})")


@app.command("verify-run")
def verify_run(
    run_dir: Path = typer.Argument(..., help="Path to a run directory (contains checksums.json)."),
    expected_id: str | None = typer.Option(
        None, "--expected-id", help="Out-of-band bundle id to pin against a consistent rewrite."
    ),
) -> None:
    """Verify a saved run's evidence bundle has not been edited since it was written."""
    from rich.console import Console

    from arena.reports.bundle import verify_bundle

    console = Console()
    result = verify_bundle(run_dir, expected_id=expected_id)
    if result.error:
        console.print(f"[red]ERROR[/red] {result.error}")
        raise typer.Exit(code=1)
    if result.ok:
        console.print(f"[green]VERIFIED[/green] evidence bundle {result.bundle_id}")
        return
    console.print("[red]TAMPERED[/red] evidence bundle integrity check failed")
    for name in result.modified:
        console.print(f"  modified: {name}")
    for name in result.missing:
        console.print(f"  missing:  {name}")
    for name in result.added:
        console.print(f"  added:    {name}")
    if not result.bundle_id_ok:
        console.print("  checksums.json is internally inconsistent")
    if not result.expected_id_ok:
        console.print(f"  bundle id does not match expected {expected_id}")
    raise typer.Exit(code=1)


@app.command("certify-pack")
def certify_pack(
    benchmark_set: Path = typer.Argument(DEFAULT_BENCHMARK_SET),
    allow_local_execution: bool = typer.Option(False, "--allow-local-execution"),
    limit: int = typer.Option(20, "--limit", min=1, help="Max mutants per case."),
    determinism_runs: int = typer.Option(
        1,
        "--determinism-runs",
        min=1,
        help="Re-run gates this many times to earn 'verified'; 1 disables the check.",
    ),
    strict: str = typer.Option(
        "",
        "--strict",
        help="Exit nonzero unless the pack reaches this level (certified or verified).",
    ),
) -> None:
    """Grade each case on the draft/development/certified/verified ladder."""
    from rich.console import Console

    from arena.benchmark.certify import LEVELS
    from arena.benchmark.certify import certify_pack as run_certify

    if strict and strict not in LEVELS:
        raise typer.BadParameter(f"--strict must be one of {', '.join(LEVELS)}")

    console = Console()
    report = run_certify(
        resolve_benchmark_path(benchmark_set),
        allow_local_execution=allow_local_execution,
        mutation_limit=limit,
        determinism_runs=determinism_runs,
    )

    def mark(value: bool | None) -> str:
        return "n/a" if value is None else ("pass" if value else "FAIL")

    unexecuted = 0
    for case in report.cases:
        if not case.executable:
            console.print(f"{case.case_id}: draft (no executable tests)")
            continue
        if not case.executed:
            unexecuted += 1
            console.print(f"{case.case_id}: not executed (no available backend)")
            continue
        kill = "n/a" if case.mutant_kill_rate is None else f"{case.mutant_kill_rate:.0%}"
        determinism = (
            "" if case.deterministic is None else f" deterministic={mark(case.deterministic)}"
        )
        # No square brackets: rich would parse them as markup and hide them.
        console.print(
            f"{case.case_id}: {case.level.upper()}  "
            f"baseline_fails={mark(case.baseline_fails)} "
            f"reference_passes={mark(case.reference_passes)} "
            f"mutant_kill_rate={kill} ({case.mutant_total} mutants)"
            f"{determinism}"
        )
    if unexecuted:
        console.print(
            f"[yellow]{unexecuted} case(s) had no backend to run their tests.[/yellow] "
            "Pass --allow-local-execution, or build the pack's docker image "
            "(scripts/build_bench_image.sh) and set default_docker_image."
        )
    # Mutation evidence is the cheat-resistance signal, but it only applies to
    # cases whose fix is a Python logic-operator change. Report coverage honestly
    # so a pack that leans on baseline-fails + reference-passes is visible.
    executed_cases = [case for case in report.cases if case.executable and case.executed]
    with_mutants = [case for case in executed_cases if case.mutant_total > 0]
    if executed_cases:
        console.print(
            f"Mutation evidence: {len(with_mutants)}/{len(executed_cases)} executed case(s) "
            "had viable mutants (others rest on baseline-fails + reference-passes)."
        )
    console.print(f"\nPack '{report.pack}' level: {report.level}")
    if strict and LEVELS.index(report.level) < LEVELS.index(strict):
        raise typer.Exit(code=1)


DEFAULT_INTEGRITY_PACK = Path("benchmark_sets/integrity_pilot_v0")


@app.command("integrity-validate")
def integrity_validate(
    pack: Path = typer.Argument(DEFAULT_INTEGRITY_PACK),
) -> None:
    """Load a CRA-Integrity pair pack and report its structure (no execution)."""
    integrity_validate_command(resolve_benchmark_path(pack))


@app.command("integrity-audit")
def integrity_audit(
    pack: Path = typer.Argument(DEFAULT_INTEGRITY_PACK),
) -> None:
    """Score trivial validation-diff heuristics against a pair pack.

    A pack a "flag every test change" strategy can separate is measuring diff
    shape, not reviewing ability. Offline and fast; no execution.
    """
    integrity_audit_command(resolve_benchmark_path(pack))


@app.command("integrity-certify")
def integrity_certify(
    pack: Path = typer.Argument(DEFAULT_INTEGRITY_PACK),
    allow_local_execution: bool = typer.Option(False, "--allow-local-execution"),
    docker_image: str | None = typer.Option(None, "--docker-image"),
    determinism_runs: int = typer.Option(
        1,
        "--determinism-runs",
        min=1,
        help="Re-run the four defining verdicts this many times to earn 'verified'.",
    ),
    limit: int = typer.Option(24, "--limit", min=1, help="Max oracle mutants per pair."),
    strict: str = typer.Option(
        "", "--strict", help="Exit nonzero unless the pack reaches this level."
    ),
) -> None:
    """Admit or reject each pair: green-and-correct versus green-but-wrong, proven."""
    from arena.cli.commands.integrity import LEVELS

    if strict and strict not in LEVELS:
        raise typer.BadParameter(f"--strict must be one of {', '.join(LEVELS)}")
    integrity_certify_command(
        resolve_benchmark_path(pack),
        allow_local_execution=allow_local_execution,
        docker_image=docker_image,
        determinism_runs=determinism_runs,
        mutation_limit=limit,
        strict=strict,
    )


@app.command("integrity-run")
def integrity_run(
    pack: Path = typer.Argument(DEFAULT_INTEGRITY_PACK),
    reviewer: str = typer.Option(
        "integrity:reference", "--reviewer", help=f"One of: {available_reviewers()}"
    ),
    allow_local_execution: bool = typer.Option(False, "--allow-local-execution"),
    docker_image: str | None = typer.Option(None, "--docker-image"),
    condition: str | None = typer.Option(
        None,
        "--condition",
        help="Reviewer information condition (ablation); defaults to the pair's own.",
    ),
    pair_id: list[str] = typer.Option([], "--pair-id", help="Restrict the run to these pairs."),
    as_json: bool = typer.Option(False, "--json", help="Emit the run result as JSON."),
    output: Path | None = typer.Option(
        None, "--output", help="Directory to write the run record into."
    ),
) -> None:
    """Review every pair with one reviewer and report the integrity metrics."""
    integrity_run_command(
        resolve_benchmark_path(pack),
        reviewer,
        allow_local_execution=allow_local_execution,
        docker_image=docker_image,
        condition=condition,
        pair_id=list(pair_id),
        as_json=as_json,
        output=output,
    )


@app.command("lint-cases")
def lint_cases(
    benchmark_set: Path = typer.Argument(DEFAULT_BENCHMARK_SET),
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Exit nonzero when answer-surface contamination is found.",
    ),
) -> None:
    """Scan cases for ground-truth vocabulary leaking into reviewer-visible surfaces."""
    from arena.benchmark.contamination import scan_benchmark

    # Added diff lines, after-tree comments, and test names are answer surfaces a
    # reviewer could echo without reasoning, so they fail a strict run. Removed
    # diff lines are the other side of the review: in a forward-review diff they
    # are legitimate context (the code being changed), so they are reported but do
    # not by themselves fail a strict run. For inverse RealFix diffs a removed
    # line can carry the historical fix; the import gate rejects such cases at
    # build time and the case audit reviews them, so they are surfaced here too.
    blocking_surfaces = {"diff_added_line", "after_comment", "test_name"}
    warnings = scan_benchmark(resolve_benchmark_path(benchmark_set))
    for warning in warnings:
        typer.echo(warning.render())
    blocking = [warning for warning in warnings if warning.surface in blocking_surfaces]
    removed = [warning for warning in warnings if warning.surface == "diff_removed_line"]
    if blocking:
        typer.echo(
            f"{len(blocking)} answer-surface leak(s); leaked phrases hand reviewers "
            "the answer (test names appear in pre-patch test output).",
            err=True,
        )
    if removed:
        typer.echo(
            f"{len(removed)} removed-diff-line match(es) reported for review-surface audit.",
            err=True,
        )
    if not warnings:
        typer.echo("No contamination found.")
    if strict and blocking:
        raise typer.Exit(code=1)


@app.command()
def schema(
    output: Path | None = typer.Option(
        None, "--output", help="Write the schema to a file instead of stdout."
    ),
) -> None:
    """Emit the versioned JSON Schema a reviewer's output must satisfy."""
    import json

    from arena.core.config import REVIEW_SCHEMA_VERSION
    from arena.core.models import ReviewResult

    payload = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": (
            "https://github.com/harihkk/code-review-arena/"
            f"schema/review-result-{REVIEW_SCHEMA_VERSION}.json"
        ),
        "version": REVIEW_SCHEMA_VERSION,
        **ReviewResult.model_json_schema(),
    }
    rendered = json.dumps(payload, indent=2)
    if output is not None:
        output.write_text(rendered + "\n", encoding="utf-8")
        typer.echo(f"Wrote {output}")
    else:
        typer.echo(rendered)


@app.command("verify-reviewer")
def verify_reviewer(
    benchmark_set: Path = typer.Argument(DEFAULT_BENCHMARK_SET),
    command: str = typer.Option(..., "--command", help="Wrapper command template to verify."),
    case_id: str | None = typer.Option(None, "--case-id"),
    reviewer_timeout_seconds: int = typer.Option(
        120, "--reviewer-timeout-seconds", min=1, max=limits.REVIEWER_TIMEOUT_SECONDS_MAX
    ),
    reveal_metadata: bool = typer.Option(False, "--reveal-metadata"),
    enable_repair: bool = typer.Option(
        False,
        "--enable-repair",
        help="Development-only salvage. Tolerant or repaired output prints SALVAGED and "
        "is non-comparable; default mode accepts only exact output.",
    ),
) -> None:
    """Run a wrapper against one case and validate its output with actionable errors."""
    verify_reviewer_command(
        resolve_benchmark_path(benchmark_set),
        command,
        case_id,
        reviewer_timeout_seconds,
        reveal_metadata,
        enable_repair,
    )


@app.command("pack-hash")
def pack_hash(
    benchmark_set: Path = typer.Argument(DEFAULT_BENCHMARK_SET),
    write: bool = typer.Option(
        False, "--write", help="Store the checksum as pack.sha256 inside the pack."
    ),
) -> None:
    from arena.benchmark.pack_hash import stored_checksum, write_checksum
    from arena.benchmark.snapshot import snapshot_pack

    benchmark_set = resolve_benchmark_path(benchmark_set)
    if write:
        checksum = write_checksum(benchmark_set)
    else:
        # Compute the displayed checksum from the immutable snapshot.
        with snapshot_pack(benchmark_set) as snapshot:
            checksum = snapshot.checksum
    typer.echo(checksum)
    pinned = stored_checksum(benchmark_set)
    if not write and pinned is not None and pinned != checksum:
        typer.echo(f"WARNING: pack content does not match stored pack.sha256 ({pinned})", err=True)
        raise typer.Exit(code=1)


@app.command()
def report(
    run_path: Path = typer.Argument(...),
    format: str = typer.Option("markdown", "--format"),
) -> None:
    report_command(run_path, format)


@app.command("audit-report")
def audit_report(
    runs_dir: Path = typer.Argument(DEFAULT_RUNS_DIR),
    output: Path = typer.Option(
        Path("docs/reports/audit-v1-results.md"),
        "--output",
    ),
    json_output: Path | None = typer.Option(
        Path("dashboard/public/reports/audit-v1.json"),
        "--json-output",
    ),
    benchmark_set: str = typer.Option(
        "audit_v1", "--benchmark-set", help="Which pack's runs to aggregate."
    ),
) -> None:
    audit_report_command(runs_dir, output, json_output, benchmark_set)


@app.command()
def leaderboard(
    runs_dir: Path = typer.Argument(DEFAULT_RUNS_DIR),
    metric: str = typer.Option("validated_case_rate", "--metric"),
    beta: float = typer.Option(1.0, "--beta", min=0.01),
    as_json: bool = typer.Option(False, "--json", help="Emit leaderboard rows as JSON to stdout."),
    include_unverified: bool = typer.Option(
        False,
        "--include-unverified",
        help="Include trusted-local (non-Docker) runs, which are excluded by default.",
    ),
) -> None:
    leaderboard_command(runs_dir, metric, beta, as_json, include_unverified)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
) -> None:
    import uvicorn

    uvicorn.run("arena.server.main:app", host=host, port=port)


if __name__ == "__main__":
    app()
