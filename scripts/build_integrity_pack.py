#!/usr/bin/env python3
"""Regenerate the derived artifacts of an integrity pack.

An integrity pair is authored as trees -- ``base/``, ``genuine/workspace/``,
``compromised/workspace/``, ``oracle/`` -- because trees are what a human auditor
can actually read. The diffs and repair patches are then *derived* from those
trees rather than hand-written, so an artifact can never drift from the tree it
claims to describe.

Derived per pair:

``<variant>/pr.diff``
    baseline -> variant. The reviewer-visible pull request.
``compromised/repairs/reference.patch``
    compromised -> genuine. A real repair: correct product behaviour while
    keeping the legitimate validation changes.
``compromised/repairs/test_only.patch``
    compromised -> compromised product with the genuine variant's validation
    artifacts. Restores the strict check and leaves the defect untouched.
``compromised/repairs/product_overfit.patch``
    compromised -> compromised with the authored ``compromised/overfit/``
    overrides applied. Keeps the visible surface green while the behaviour stays
    wrong. Skipped for a pair that ships no overrides.

Run from the repository root::

    python scripts/build_integrity_pack.py benchmark_sets/integrity_pilot_v0
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arena.integrity.loader import is_validation_path, load_pairs, relative_files  # noqa: E402
from arena.integrity.models import IntegrityPair  # noqa: E402


def tree_diff(left: Path, right: Path) -> str:
    """A unified ``a/`` -> ``b/`` diff between two trees, via git for exactness."""
    with tempfile.TemporaryDirectory(prefix="arena-int-diff-") as directory:
        root = Path(directory)
        shutil.copytree(left, root / "a")
        shutil.copytree(right, root / "b")
        result = subprocess.run(
            # --no-prefix keeps the literal a/ and b/ directory names as the only
            # path prefix, so the artifact is an ordinary "a/<path> -> b/<path>"
            # unified diff that git apply -p1 accepts.
            [
                "git",
                "diff",
                "--no-index",
                "--no-color",
                "--no-renames",
                "--no-prefix",
                "--",
                "a",
                "b",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    # git diff --no-index exits 1 when the trees differ; anything above that is
    # a real failure and must not be written out as an artifact.
    if result.returncode not in (0, 1):
        raise SystemExit(f"git diff failed for {left} -> {right}: {result.stderr.strip()}")
    return result.stdout


def _write(path: Path, text: str, *, dry_run: bool, allow_empty: bool = False) -> bool:
    """Write ``text`` unless it is already there; return True when it was stale."""
    if not text.strip() and not allow_empty:
        raise SystemExit(
            f"{path}: the derived artifact is empty. A declared repair that changes nothing "
            "is an authoring mistake -- remove the declaration instead."
        )
    previous = path.read_text(encoding="utf-8") if path.is_file() else None
    if previous == text:
        return False
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return True


def _hybrid(product_root: Path, validation_root: Path, paths: list[str], destination: Path) -> None:
    """Product from one tree, candidate-owned validation artifacts from another."""
    shutil.copytree(product_root, destination)
    for relative in relative_files(destination):
        if is_validation_path(relative, paths):
            (destination / relative).unlink()
    for relative in relative_files(validation_root):
        if is_validation_path(relative, paths):
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(validation_root / relative, target)


def _overridden(workspace: Path, overrides: Path, destination: Path) -> None:
    shutil.copytree(workspace, destination)
    for relative in relative_files(overrides):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(overrides / relative, target)


def build_pair(pair: IntegrityPair, *, dry_run: bool = False) -> list[str]:
    assert pair.pair_dir is not None
    root = pair.pair_dir
    base = root / pair.base_dir
    genuine = root / pair.genuine.id / pair.genuine.workspace_dir
    compromised = root / pair.compromised.id / pair.compromised.workspace_dir
    paths = list(pair.candidate_validation.paths)
    written: list[str] = []

    for variant, workspace in ((pair.genuine, genuine), (pair.compromised, compromised)):
        target = root / variant.id / variant.diff
        if _write(target, tree_diff(base, workspace), dry_run=dry_run):
            written.append(str(target))

    repairs = pair.compromised.repairs
    if repairs.reference:
        target = root / pair.compromised.id / repairs.reference
        if _write(target, tree_diff(compromised, genuine), dry_run=dry_run):
            written.append(str(target))

    with tempfile.TemporaryDirectory(prefix="arena-int-build-") as directory:
        work = Path(directory)
        if repairs.test_only:
            _hybrid(compromised, genuine, paths, work / "test_only")
            target = root / pair.compromised.id / repairs.test_only
            if _write(target, tree_diff(compromised, work / "test_only"), dry_run=dry_run):
                written.append(str(target))
        overrides = root / pair.compromised.id / "overfit"
        if repairs.product_overfit:
            if not overrides.is_dir():
                raise SystemExit(
                    f"{pair.id}: declares a product_overfit repair but ships no "
                    f"{overrides} override tree"
                )
            _overridden(compromised, overrides, work / "overfit")
            target = root / pair.compromised.id / repairs.product_overfit
            if _write(target, tree_diff(compromised, work / "overfit"), dry_run=dry_run):
                written.append(str(target))
    return written


def ensure_placeholders(pack: Path) -> None:
    """Create empty derived artifacts so a pack can be built from its trees alone.

    The loader requires every declared diff and repair artifact to exist, which
    would otherwise make the very first build impossible.
    """
    import yaml

    for pair_file in sorted(pack.glob("*/pair.yaml")):
        document = yaml.safe_load(pair_file.read_text(encoding="utf-8")) or {}
        root = pair_file.parent
        for key in ("genuine", "compromised"):
            variant = document.get(key) or {}
            variant_root = root / variant.get("id", key)
            targets = [variant.get("diff", "pr.diff")]
            targets += [
                value for value in (variant.get("repairs") or {}).values() if isinstance(value, str)
            ]
            for relative in targets:
                target = variant_root / relative
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack", type=Path, help="Integrity pack directory.")
    parser.add_argument("--check", action="store_true", help="Fail if anything is stale.")
    args = parser.parse_args()

    if not args.check:
        ensure_placeholders(args.pack)
    stale: list[str] = []
    for pair in load_pairs(args.pack):
        written = build_pair(pair, dry_run=args.check)
        stale += written
        for item in written:
            print(("STALE " if args.check else "wrote ") + item)
    if not stale:
        print(f"{args.pack}: derived artifacts already up to date")
        return 0
    if args.check:
        print(f"{len(stale)} derived artifact(s) are stale; rerun without --check", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
