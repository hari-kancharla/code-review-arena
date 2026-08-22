from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, HTTPException

from arena.benchmark.benchmark_runner import run_benchmark
from arena.benchmark.case_loader import build_context
from arena.benchmark.snapshot import snapshot_pack
from arena.core.config import benchmark_root, database_path, resolve_benchmark_set
from arena.core.errors import ReviewerError
from arena.core.registry import create_reviewer
from arena.server.auth import (
    require_api_token,
    reviewer_needs_execution_optin,
    server_local_execution_enabled,
)
from arena.server.jobs import job_queue
from arena.server.schemas import CreateRunRequest
from arena.storage.repository import RunRepository

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("")
def runs() -> list[dict[str, object]]:
    return RunRepository(database_path()).list_runs()


def _normalize_benchmark_set(value: str) -> str:
    """Accept the legacy "benchmark_sets/<name>" form by reducing it to <name>."""
    parts = PurePosixPath(value.replace("\\", "/")).parts
    if len(parts) == 2 and parts[0] == benchmark_root().name:
        return parts[1]
    return value


@router.post("", status_code=202, dependencies=[Depends(require_api_token)])
def create_run(request: CreateRunRequest) -> dict[str, object]:
    benchmark_dir = resolve_benchmark_set(_normalize_benchmark_set(request.benchmark_set))
    if benchmark_dir is None:
        raise HTTPException(status_code=400, detail="Unknown benchmark set")
    if request.allow_local_execution and not server_local_execution_enabled():
        raise HTTPException(
            status_code=403,
            detail="Local execution over HTTP is disabled; set "
            "ARENA_SERVER_ALLOW_LOCAL_EXECUTION=1 on the server to opt in.",
        )
    # Resolve the spec first so an unknown or malformed reviewer still reports a
    # helpful 400 rather than being masked by the 403 below. Constructing a
    # reviewer is inert -- every implementation only stores its configuration, and
    # nothing is spawned or fetched until the job runs.
    try:
        reviewer = create_reviewer(request.reviewer, command=request.command)
    except ReviewerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Then gate the reviewer spec itself, not just the pack-execution flag. A
    # custom-command reviewer spawns a caller-supplied process, and an
    # openai:/http: reviewer makes the server fetch a caller-supplied URL; both
    # are things an unauthenticated POST must not be able to do while
    # allow_local_execution is still false.
    if (
        reviewer_needs_execution_optin(request.reviewer, request.command)
        and not server_local_execution_enabled()
    ):
        raise HTTPException(
            status_code=403,
            detail="This reviewer runs a local command or calls an external URL, which is "
            "disabled over HTTP; set ARENA_SERVER_ALLOW_LOCAL_EXECUTION=1 on the server "
            "to opt in.",
        )

    def execute() -> str:
        return run_benchmark(
            benchmark_dir,
            reviewer,
            mode=request.mode,
            beta=request.beta,
            allow_local_execution=request.allow_local_execution,
            max_wall_seconds=request.max_wall_seconds,
            max_cost=request.max_cost,
        ).run_id

    job = job_queue().submit(execute)
    if job is None:
        raise HTTPException(status_code=429, detail="Run queue is full; retry later")
    return {"job_id": job.id, "status": job.status, "status_url": f"/runs/jobs/{job.id}"}


@router.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, object]:
    job = job_queue().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.model_dump(mode="json")


@router.get("/{run_id}")
def run_detail(run_id: str) -> dict[str, object]:
    run = RunRepository(database_path()).get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run.model_dump(mode="json")


@router.get("/{run_id}/cases/{case_id}")
def run_case_detail(run_id: str, case_id: str) -> dict[str, object]:
    run = RunRepository(database_path()).get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    for result in run.case_results:
        if result.case_id == case_id:
            benchmark_dir = resolve_benchmark_set(run.benchmark_set)
            if benchmark_dir is None:
                return result.model_dump(mode="json")
            # Diff/ground-truth context comes from the immutable snapshot -- but
            # of the pack as it is NOW, while the score beside it was measured
            # against the pack as it was THEN. Editing a pack in place therefore
            # used to render a stored score next to a completely different bug,
            # presented as that run's evidence. Serve the context only when the
            # pack still hashes to what the run recorded.
            recorded = run.metadata.pack_checksum
            with snapshot_pack(benchmark_dir) as snapshot:
                if recorded is not None and snapshot.checksum != recorded:
                    return {
                        **result.model_dump(mode="json"),
                        "pack_drifted": True,
                        "recorded_pack_checksum": recorded,
                        "current_pack_checksum": snapshot.checksum,
                    }
                for case in snapshot.load():
                    if case.id == case_id:
                        context = build_context(case)
                        return {
                            **result.model_dump(mode="json"),
                            "pack_drifted": False,
                            "diff": context.diff,
                            "ground_truth": case.ground_truth.model_dump(mode="json"),
                            "stack": case.stack,
                        }
            return result.model_dump(mode="json")
    raise HTTPException(status_code=404, detail="Case result not found")
