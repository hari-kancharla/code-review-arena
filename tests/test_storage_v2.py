"""v2 storage: new run-validity columns persist and gate the API leaderboard."""

from datetime import datetime

from arena.core.models import DeterministicMetrics, RunMetadata, RunResult
from arena.storage.repository import RunRepository


def _metrics(case_rate: float) -> DeterministicMetrics:
    return DeterministicMetrics(
        beta=1.0,
        false_positives_per_case=0.0,
        latency_per_case_ms=0.0,
        validated_case_rate=case_rate,
        validated_f_beta=case_rate,
        deterministic_pass_rate=case_rate,
    )


def _run(
    run_id: str,
    model: str,
    *,
    status: str,
    schema: int = 2,
    case_rate: float = 1.0,
    backend: str = "trusted-local",
    externally_verified: bool = False,
    non_exact_output_used: bool | None = False,
    benchmark_set: str = "v1",
):
    return RunResult(
        run_id=run_id,
        benchmark_set=benchmark_set,
        reviewer="control",
        model=model,
        started_at=datetime.now(),
        completed_at=datetime.now(),
        metadata=RunMetadata(
            prompt_version="v1",
            benchmark_version="v1",
            pack_digest_externally_verified=externally_verified,
            non_exact_output_used=non_exact_output_used,
            reviewer_oracle_reachable=False,
        ),
        case_results=[],
        total_score=0.0,
        schema_version=schema,
        run_status=status,  # type: ignore[arg-type]
        execution_backend=backend,  # type: ignore[arg-type]
        mode="full",
        deterministic_metrics=_metrics(case_rate),
        bugs_found=0,
        correct_files=0,
        correct_lines=0,
        false_positives=0,
        total_cost=0.0,
        total_latency_ms=0,
    )


def test_get_round_trips_v2_run_fields(tmp_path):
    repo = RunRepository(tmp_path / "arena.db")
    repo.save(_run("c1", "perfect", status="complete"))
    got = repo.get("c1")
    assert got is not None
    assert got.schema_version == 2
    assert got.run_status == "complete"
    assert got.execution_backend == "trusted-local"
    assert got.deterministic_metrics is not None
    assert got.deterministic_metrics.validated_case_rate == 1.0


def test_repository_leaderboard_excludes_partial_and_legacy(tmp_path):
    repo = RunRepository(tmp_path / "arena.db")
    repo.save(_run("c1", "perfect", status="complete", backend="docker", externally_verified=True))
    repo.save(_run("p1", "flaky", status="partial", case_rate=0.5, backend="docker"))
    repo.save(_run("l1", "old", status="complete", schema=1, backend="docker"))
    board = repo.leaderboard()
    assert {row["model"] for row in board} == {"perfect"}


def test_repository_leaderboard_excludes_trusted_local_by_default(tmp_path):
    repo = RunRepository(tmp_path / "arena.db")
    repo.save(
        _run("d1", "docker-run", status="complete", backend="docker", externally_verified=True)
    )
    repo.save(_run("t1", "local-run", status="complete", backend="trusted-local"))
    # Default: only the verified Docker run is comparable.
    assert {row["model"] for row in repo.leaderboard()} == {"docker-run"}
    # Opt in to see unverified runs too.
    both = {row["model"] for row in repo.leaderboard(include_unverified=True)}
    assert both == {"docker-run", "local-run"}


def test_repository_leaderboard_separates_runs_by_pack(tmp_path):
    # The same reviewer/model/mode measured on different packs are distinct
    # results and must each get their own leaderboard row and history count,
    # rather than one pack's run overwriting the other's.
    repo = RunRepository(tmp_path / "arena.db")
    repo.save(
        _run(
            "a1",
            "perfect",
            status="complete",
            backend="docker",
            externally_verified=True,
            benchmark_set="audit_v1",
        )
    )
    repo.save(
        _run(
            "a2",
            "perfect",
            status="complete",
            backend="docker",
            externally_verified=True,
            benchmark_set="audit_v2",
        )
    )
    board = repo.leaderboard()
    assert {row["benchmark_set"] for row in board} == {"audit_v1", "audit_v2"}
    assert len(board) == 2
    assert all(row["history_count"] == 1 for row in board)


def test_repository_leaderboard_requires_external_digest(tmp_path):
    # The centralization regression: a Docker, full-coverage run whose pack only
    # matched its own (regenerable) pack.sha256 is NOT externally verified, so the
    # database/API leaderboard must exclude it by default, exactly like the file
    # leaderboard. It must not reappear merely because it ran in Docker.
    repo = RunRepository(tmp_path / "arena.db")
    repo.save(_run("internal", "self-consistent", status="complete", backend="docker"))
    repo.save(
        _run(
            "external",
            "externally-verified",
            status="complete",
            backend="docker",
            externally_verified=True,
        )
    )
    assert {row["model"] for row in repo.leaderboard()} == {"externally-verified"}
    both = {row["model"] for row in repo.leaderboard(include_unverified=True)}
    assert both == {"self-consistent", "externally-verified"}


def test_concurrent_first_touch_migrates_exactly_once(tmp_path):
    """Several connections opening a brand-new database must all succeed.

    The migration read PRAGMA table_info and then issued ALTER TABLE with no
    lock, so two connections that both saw an unmigrated database each ran the
    full migration and the loser raised `duplicate column name`. That is a bare
    sqlite3.OperationalError, not an ArenaError, so it escaped the CLI's error
    handling and killed a finished run at save() time. Switching the journal
    mode raced the same way and surfaced as `database is locked`.
    """
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor

    from arena.storage.db import connect

    db_path = tmp_path / "concurrent.db"

    def touch(_: int) -> str | None:
        try:
            connection = connect(db_path)
            connection.execute("SELECT COUNT(*) FROM runs").fetchone()
            connection.close()
            return None
        except Exception as exc:  # noqa: BLE001 - the point is that none escape.
            return f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=8) as pool:
        errors = [error for error in pool.map(touch, range(8)) if error]

    assert errors == []

    # The database really is migrated once, and the journal mode still took.
    probe = sqlite3.connect(db_path)
    assert probe.execute("PRAGMA user_version").fetchone()[0] == 2
    assert probe.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    probe.close()


def test_list_runs_exposes_validity_so_the_dashboard_can_judge_trust(tmp_path):
    """An invalid run must not be indistinguishable from a clean one over the API.

    list_runs() selected only metric columns, so /runs handed the dashboard a
    row with no run_status or execution_backend and the page derived its badge
    from validated_case_rate alone -- rendering a run against a tampered pack,
    where the tampering is exactly what made every case pass, as "Validated".
    """
    repo = RunRepository(tmp_path / "validity.db")
    repo.save(_run("tampered", "perfect", status="invalid", case_rate=1.0))

    row = repo.list_runs()[0]

    assert row["run_status"] == "invalid"
    assert row["execution_backend"] == "trusted-local"
    assert row["validated_case_rate"] == 1.0  # the number is still there...
    assert "coverage_rate" in row  # ...but so is every signal needed to distrust it
    assert "failed_case_count" in row
    assert "schema_version" in row

    # The ranked surface still refuses it outright, with or without the flag.
    assert repo.leaderboard() == []
    assert repo.leaderboard(include_unverified=True) == []
