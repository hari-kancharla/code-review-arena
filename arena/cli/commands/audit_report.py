from pathlib import Path

from rich.console import Console

from arena.reports.audit_report import write_audit_report


def audit_report(
    runs_dir: Path,
    output: Path,
    json_output: Path | None,
    benchmark_set: str = "audit_v1",
) -> None:
    data = write_audit_report(runs_dir, output, json_output, benchmark_set=benchmark_set)
    console = Console()
    if data.get("empty"):
        console.print(
            f"[yellow]No admissible {benchmark_set} runs found[/yellow]; "
            f"wrote empty-state report to {output}"
        )
    else:
        console.print(
            f"[green]Audit report written[/green] to {output} "
            f"({data['summary']['run_count']} run(s))"
        )
    excluded = data["summary"].get("excluded_run_count", 0)
    if excluded:
        Console(stderr=True).print(
            f"[yellow]WARNING[/yellow] {excluded} run(s) were excluded from this report: the "
            "harness did not stamp them run_status=complete (tampered pack, no results, or "
            "execution that never happened). They are recorded as excluded, not published."
        )
    if json_output is not None:
        console.print(f"[green]Dashboard JSON[/green] written to {json_output}")
