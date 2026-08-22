import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from arena.reports.leaderboard import leaderboard_rows


def leaderboard(
    runs_dir: Path,
    metric: str = "validated_case_rate",
    beta: float = 1.0,
    as_json: bool = False,
    include_unverified: bool = False,
) -> None:
    if not runs_dir.exists():
        Console(stderr=True).print(f"[red]ERROR[/red] runs directory not found: {runs_dir}")
        raise typer.Exit(code=1)
    try:
        rows = leaderboard_rows(
            runs_dir, metric=metric, beta=beta, include_unverified=include_unverified
        )
    except ValueError as exc:
        Console(stderr=True).print(f"[red]ERROR[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if as_json:
        typer.echo(json.dumps(rows, indent=2))
        return

    display_metric = "detection_f_beta" if metric == "f_beta" else metric
    table = Table()
    for column in [
        "Reviewer",
        "Model",
        display_metric,
        "Bugs Found",
        "False Positives",
        "Cost",
        "Latency",
        "Pack",
    ]:
        table.add_column(column)
    show_ci = any(row.get("metric_ci") for row in rows)
    for row in rows:
        cell = _metric_value(row["metric_value"], display_metric)
        ci = row.get("metric_ci")
        if ci is not None:
            cell += f"  [{ci[0] * 100:.0f}-{ci[1] * 100:.0f}]"
        table.add_row(
            str(row["reviewer"]),
            str(row["model"]),
            cell,
            str(row["bugs_found"]),
            str(row["false_positives"]),
            f"${row['cost']:.4f}",
            f"{row['latency_ms'] / 1000:.2f}s",
            str(row["pack"]),
        )
    Console(width=140).print(table)
    if not rows and not include_unverified:
        # An empty table with no explanation reads as "the harness produced
        # nothing", when the usual cause is simply that every run was executed
        # locally and the default view is restricted to verified runs.
        filtered = len(
            leaderboard_rows(runs_dir, metric=metric, beta=beta, include_unverified=True)
        )
        if filtered:
            Console(stderr=True).print(
                f"[yellow]NOTE[/yellow] {filtered} run(s) were excluded as unverified "
                "(a verified run needs Docker and --expected-pack-sha256). "
                "Re-run with --include-unverified to inspect them."
            )
    if show_ci:
        Console(stderr=True).print(
            "[dim]Bracketed range is the Wilson 95% interval; at these pack sizes it is "
            "wide, so reviewers whose intervals overlap are not reliably ranked.[/dim]"
        )


def _metric_value(value: float | None, metric: str) -> str:
    if value is None:
        return "n/a"
    if metric.endswith("_rate") or "_f_" in metric or metric.endswith(("_precision", "_recall")):
        return f"{value:.3f}"
    if metric == "cost_per_validated_fix":
        return f"${value:.4f}"
    return f"{value:.1f}"
