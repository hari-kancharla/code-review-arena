"""`arena mine-fixes`: propose historical fixes that could become review cases."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

import typer
from rich.console import Console
from rich.table import Table

from arena.core.errors import ArenaError
from arena.importer.candidate_miner import Candidate, mine_candidates
from arena.importer.git_objects import open_repo


def _ground_truth_files(
    candidate: Candidate, ranges_by_path: dict[str, list[dict[str, int]]]
) -> list[dict[str, object]]:
    """Ground-truth file entries, with derived ranges where the diff shows a defect.

    Files the FIX created are skipped: the reviewer reviews the buggy tree, where
    those files do not exist yet, so the defect cannot live in one -- and pack
    validation rejects a ground-truth path missing from `after/`, which would make
    the scaffold produce a case that cannot be imported.

    A path whose change was purely additive or purely a deletion yields no range,
    so it falls back to a placeholder rather than a fabricated one: pointing
    ground truth at the wrong lines is worse than admitting the tool could not
    tell, and the placeholder is obvious enough to demand attention.
    """
    created = set(candidate.created_paths)
    entries: list[dict[str, object]] = []
    for path in candidate.source_paths:
        if path in created:
            continue
        derived = ranges_by_path.get(path) or [{"start": 1, "end": 1}]
        entries.append({"path": path, "line_ranges": derived})
    if not entries:
        # Every source file is new, so there is no pre-image anywhere to point
        # at. Emit a shape-valid placeholder the author must replace rather than
        # an empty list, which the schema rejects outright.
        entries.append({"path": candidate.source_paths[0], "line_ranges": [{"start": 1, "end": 1}]})
    return entries


def _tests_root(candidate: Candidate) -> str | None:
    """The single directory holding the case's tests, if one cleanly exists.

    Returns None when the tests share no single directory, sit at the repository
    root, or live in a directory that also holds source. `import-fix` treats
    tests_root as the tests' own subtree, so a root that also contains source
    files is refused -- and co-located tests (`src/foo.go` beside
    `src/foo_test.go`) are exactly that case. Emitting nothing leaves the author
    a spec that loads, with one field to fill rather than one to debug.
    """
    parents = {str(PurePosixPath(path).parent) for path in candidate.test_paths}
    if len(parents) != 1:
        return None
    only = parents.pop()
    if only in {"", "."}:
        return None
    prefix = f"{only}/"
    if any(source == only or source.startswith(prefix) for source in candidate.source_paths):
        return None
    return only


def _spec_scaffold(candidate: Candidate, *, source_label: str | None) -> dict[str, object]:
    """A ready-to-edit `arena import-fix` invocation plus its import spec.

    The spec half is deliberately SHAPE-VALID: it parses as an `ImportSpec`
    exactly as emitted, so the author edits prose in a file that already loads
    rather than debugging a skeleton. Placeholder values are legal but obviously
    wrong (`severity: medium`, `concepts: ["todo"]`), because a scaffold that
    cannot be loaded is not a saving, and a scaffold that guesses semantics
    corrupts the benchmark. Commits and the source label are `import-fix`
    arguments rather than spec fields, so they are handed back as the command to
    run, not smuggled into the YAML where they would be rejected.
    """
    command = (
        "arena import-fix"
        f" --repo <REPO> --buggy-commit {candidate.buggy_commit}"
        f" --fixed-commit {candidate.fixed_commit}"
        " --spec import-spec.yaml --output <PACK_DIR>"
        f" --source-label {source_label or '<owner/repository>'}"
    )
    spec: dict[str, object] = {
        "schema_version": "1",
        "pack": {"version": "0.1.0", "name": "TODO pack name"},
        "case": {
            "id": "todo_case_id_001",
            "title": "TODO one-line title (do not paraphrase the fix)",
            "category": "todo",
            # Legal so the spec loads; still plainly a placeholder to revisit.
            "severity": "medium",
            "stack": ["todo"],
            "description": "TODO what the reviewer is looking at, without the answer",
        },
        "source_paths": candidate.source_paths,
        "ground_truth": {
            "primary_bug": {
                "summary": "TODO what is wrong, in the reviewer's words",
                # Derived from the fix diff: confirm before keeping. Which change
                # constitutes "the bug" is a semantic judgement.
                "files": _ground_truth_files(candidate, candidate.derived_line_ranges),
                "concepts": ["todo"],
                "must_mention": ["todo"],
                "acceptable_fix_keywords": ["todo"],
            }
        },
    }
    tests_root = _tests_root(candidate)
    if tests_root is not None:
        spec["tests_root"] = tests_root
    return {
        "import_command": command,
        "upstream_subject": candidate.subject,
        "test_paths": candidate.test_paths,
        # Present only with --allow-unclassified: import-fix will refuse the
        # commit until every one of these is covered by a selector.
        "unclassified_paths": candidate.unclassified_paths,
        "spec": spec,
    }


def mine_fixes_command(
    repo: Path,
    revision: str = "HEAD",
    limit: int = 200,
    max_files: int = 12,
    output: Path | None = None,
    source_label: str | None = None,
    as_json: bool = False,
    allow_unclassified: bool = False,
) -> None:
    try:
        with open_repo(repo) as opened:
            candidates = mine_candidates(
                opened,
                limit=limit,
                revision=revision,
                max_files=max_files,
                allow_unclassified=allow_unclassified,
            )
    except ArenaError as exc:
        Console(stderr=True).print(f"[red]ERROR[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if as_json:
        typer.echo(
            json.dumps(
                [_spec_scaffold(item, source_label=source_label) for item in candidates], indent=2
            )
        )
        return

    if not candidates:
        Console().print(
            f"No candidates in the last {limit} commit(s) of {revision}. A candidate is a "
            "non-merge commit that changes both a test file and a source file."
        )
        return

    table = Table(title=f"{len(candidates)} candidate fix(es) in {revision}")
    for column in ("Fixed", "Buggy", "Files", "Source", "Tests", "Subject"):
        table.add_column(column, overflow="fold")
    for item in candidates:
        table.add_row(
            item.fixed_commit[:10],
            item.buggy_commit[:10],
            str(item.changed_file_count),
            ", ".join(item.source_paths[:2]),
            ", ".join(item.test_paths[:2]),
            item.subject[:60],
        )
    Console(width=160).print(table)
    Console(stderr=True).print(
        "[dim]A candidate is a proposal, not a case: only `arena certify-pack` decides, by "
        "running the tests at both commits. Write the semantic fields yourself -- "
        "`--json` emits a scaffolded spec with every derivable field filled in.[/dim]"
    )

    if output is not None:
        payload = [_spec_scaffold(item, source_label=source_label) for item in candidates]
        output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        Console().print(f"Wrote {len(payload)} scaffolded spec(s) to {output}")
