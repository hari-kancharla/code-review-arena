from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from arena.benchmark.artifacts import load_reference_patch
from arena.benchmark.case_loader import build_context
from arena.benchmark.snapshot import snapshot_pack
from arena.core.config import resolve_benchmark_set
from arena.core.errors import ValidationError
from arena.core.limits import BENCHMARK_SET_NAME_LEN

router = APIRouter(prefix="/cases", tags=["cases"])
# Packs are resolved dynamically rather than hardcoded: a Literal list silently
# excluded every pack added after it was written (realfix_seed_v0 shipped, is
# validated and certified in CI, and was still rejected here with a 422). The
# name is only length-bounded; resolve_benchmark_set does the real validation --
# it rejects path separators and `..` and 404s an unknown pack, which is the same
# policy POST /runs already uses.
BenchmarkSet = Annotated[str, Query(max_length=BENCHMARK_SET_NAME_LEN)]


def _benchmark_path(benchmark_set: str) -> Path:
    # Resolve against the configured benchmark root (not the process cwd) so the
    # server works wherever it is launched from; 404 on an unknown pack.
    path = resolve_benchmark_set(benchmark_set)
    if path is None:
        raise HTTPException(status_code=404, detail=f"Unknown benchmark set: {benchmark_set}")
    return path


@router.get("")
def cases(benchmark_set: BenchmarkSet = "v1") -> list[dict[str, object]]:
    # Read case metadata from the immutable snapshot, not the mutable source.
    with snapshot_pack(_benchmark_path(benchmark_set)) as snapshot:
        return [
            {
                "id": case.id,
                "benchmark_set": benchmark_set,
                "title": case.title,
                "category": case.category,
                "severity": case.severity,
                "stack": case.stack,
                "ground_truth_summary": case.ground_truth.primary_bug.summary,
                "validation": case.validation.model_dump(mode="json"),
                "execution": case.execution.model_dump(mode="json"),
            }
            for case in snapshot.load()
        ]


@router.get("/{case_id}")
def case_detail(case_id: str, benchmark_set: BenchmarkSet = "v1") -> dict[str, object]:
    # Reviewer-visible files, the diff, and the reference patch all come from the
    # accepted snapshot content.
    with snapshot_pack(_benchmark_path(benchmark_set)) as snapshot:
        for case in snapshot.load():
            if case.id == case_id:
                context = build_context(case)
                reference_patch_path = case.case_dir / "reference.patch" if case.case_dir else None
                reference_patch: str | None = None
                if reference_patch_path is not None and reference_patch_path.is_file():
                    try:
                        reference_patch = load_reference_patch(reference_patch_path)
                    except ValidationError:
                        # Oversized, unsafe, or non-UTF-8 artifact: omit rather than 500.
                        reference_patch = None
                return {
                    **case.model_dump(mode="json"),
                    "benchmark_set": benchmark_set,
                    "diff": context.diff,
                    "relevant_files": context.relevant_files,
                    "reference_patch": reference_patch,
                }
    raise HTTPException(status_code=404, detail="Case not found")
