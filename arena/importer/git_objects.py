"""Isolated, bounded, read-only access to committed Git objects.

Reuses the Phase 1D Git subprocess isolation (private empty HOME/config, no hooks/
pager/editor/credentials/network, fixed locale, bounded incremental output, timeout
and process-group cleanup) rather than duplicating a weaker version. Reads only
committed objects (ls-tree / cat-file / diff between commits); it never runs
checkout/clone/fetch, repository code, or reads the mutable working tree.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from arena.core import limits
from arena.core.errors import ImportFixError
from arena.patching.git_pipeline import (
    _git_config,
    _git_env,
    _GitContext,
    _GitRunError,
    _run_git,
    _text,
)
from arena.security.paths import _relative_path_error

_FULL_OID = {"sha1": re.compile(r"\A[0-9a-f]{40}\Z"), "sha256": re.compile(r"\A[0-9a-f]{64}\Z")}
_ALLOWED_TREE_MODES = frozenset({"100644", "100755"})

# Source reads must depend only on committed objects: ignore replacement refs, never
# lazily fetch a missing object over the network, and treat pathspecs literally.
_SOURCE_ENV = {
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_NO_LAZY_FETCH": "1",
    "GIT_LITERAL_PATHSPECS": "1",
}


@dataclass
class Repo:
    """An opened source repository plus its isolated Git context."""

    ctx: _GitContext
    object_format: str


def _git(repo: Repo, args: list[str], reason: str) -> bytes:
    try:
        rc, out, err = _run_git(repo.ctx, args)
    except _GitRunError as exc:
        raise ImportFixError(reason, exc.diagnostic or reason) from exc
    if rc != 0:
        raise ImportFixError(reason, _text(err) or _text(out) or reason)
    return out


@contextmanager
def open_repo(repo_path: Path) -> Iterator[Repo]:
    """Open ``repo_path`` (worktree or bare) under an isolated, bounded Git context."""
    repo_path = Path(repo_path)
    if not repo_path.exists():
        raise ImportFixError("repository_missing", f"repository path does not exist: {repo_path}")
    meta = Path(tempfile.mkdtemp(prefix="arena-import-git-"))
    home = meta / "home"
    hooks = meta / "hooks"
    home.mkdir()
    hooks.mkdir()
    (meta / "empty.gitconfig").write_text("", encoding="utf-8")
    ctx = _GitContext(
        cwd=repo_path,
        env={**_git_env(home, meta / "empty.gitconfig", repo_path), **_SOURCE_ENV},
        config=_git_config(hooks),
        timeout=limits.GIT_TIMEOUT_SECONDS,
        scrub=(str(meta), str(home)),
    )
    try:
        brc, _bo, _be = _run_git(ctx, ["rev-parse", "--git-dir"])
        if brc != 0:
            raise ImportFixError("not_a_git_repository", f"not a Git repository: {repo_path}")
        probe = Repo(ctx, "sha1")
        object_format = _text(_git(probe, ["rev-parse", "--show-object-format"], "git_failed"))
        if object_format not in _FULL_OID:
            raise ImportFixError(
                "unsupported_object_format", f"unsupported format {object_format!r}"
            )
        # Truncated or rewritten ancestry cannot support deterministic range admission.
        if _text(_git(probe, ["rev-parse", "--is-shallow-repository"], "git_failed")) == "true":
            raise ImportFixError("shallow_repository", "shallow repositories are not supported")
        grafts_rel = _text(_git(probe, ["rev-parse", "--git-path", "info/grafts"], "git_failed"))
        grafts = Path(grafts_rel) if os.path.isabs(grafts_rel) else repo_path / grafts_rel
        if grafts.is_file() and grafts.read_text(encoding="utf-8", errors="replace").strip():
            raise ImportFixError("repository_history_override", "grafted history is not supported")
        yield Repo(ctx, object_format)
    except _GitRunError as exc:
        raise ImportFixError("git_failed", exc.diagnostic or "git failed") from exc
    finally:
        shutil.rmtree(meta, ignore_errors=True)


def resolve_commit(repo: Repo, oid: str) -> str:
    """Require a full object id (per the repo format) that names a commit object."""
    if not _FULL_OID[repo.object_format].match(oid):
        raise ImportFixError(
            "abbreviated_commit_id",
            f"commit id must be a full {repo.object_format} object id",
        )
    out = _git(repo, ["cat-file", "-t", oid], "invalid_commit_object")
    if _text(out) != "commit":
        raise ImportFixError("invalid_commit_object", "object id does not name a commit")
    # The verified id must equal the supplied id (no ref/peel resolution).
    verified = _text(
        _git(repo, ["rev-parse", "--verify", f"{oid}^{{commit}}"], "invalid_commit_object")
    )
    if verified != oid:
        raise ImportFixError("invalid_commit_object", "commit id did not resolve to itself")
    return oid


def commit_dates(repo: Repo, oid: str) -> tuple[str, str]:
    """(author_date, committer_date) of a commit, ISO-8601 normalized to UTC.

    A commit's own dates are part of the commit object and are therefore already
    covered by the object id recorded in provenance. They are NOT the volatile
    wall-clock, host or user data that Provenance bans -- that ban is about the
    import RUN -- so reading them leaves the importer byte-deterministic for a
    given commit pair.

    They are, however, self-reported: settable through GIT_COMMITTER_DATE and
    rewritten by rebase. They bound when the commit OBJECT existed, not when the
    fix became publicly readable, which is why callers take the earlier of the
    two signals and why the recorded basis names which signal won.
    """
    out = _text(_git(repo, ["show", "-s", "--format=%aI%n%cI", oid], "git_failed"))
    lines = [line for line in out.splitlines() if line.strip()]
    if len(lines) != 2:
        raise ImportFixError("invalid_commit_date", "commit did not report both of its dates")
    rendered: list[str] = []
    for line in lines:
        try:
            parsed = datetime.fromisoformat(line.strip())
        except ValueError as exc:
            raise ImportFixError("invalid_commit_date", "commit date is not ISO-8601") from exc
        if parsed.tzinfo is None:
            raise ImportFixError("invalid_commit_date", "commit date carries no offset")
        rendered.append(f"{parsed.astimezone(UTC):%Y-%m-%dT%H:%M:%S}+00:00")
    return rendered[0], rendered[1]


def is_ancestor(repo: Repo, ancestor: str, descendant: str) -> bool:
    rc, _o, _e = _run_git(repo.ctx, ["merge-base", "--is-ancestor", ancestor, descendant])
    return rc == 0


def merge_base(repo: Repo, a: str, b: str) -> str | None:
    rc, out, _e = _run_git(repo.ctx, ["merge-base", a, b])
    return _text(out) if rc == 0 and _text(out) else None


def read_tree(repo: Repo, commit: str, prefixes: list[str]) -> dict[str, tuple[str, str]]:
    """Map repo-relative path -> (mode, blob oid) for regular files under ``prefixes``.

    Rejects symlink/gitlink/submodule/unknown modes and unsafe paths.
    """
    out = _git(repo, ["ls-tree", "-rz", "--full-tree", commit, "--", *prefixes], "git_failed")
    entries: dict[str, tuple[str, str]] = {}
    for record in out.split(b"\0"):
        if not record:
            continue
        try:
            meta, raw_path = record.split(b"\t", 1)
            mode, obj_type, oid = meta.decode("ascii").split(" ")
            path = raw_path.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ImportFixError("git_output_invalid", "malformed ls-tree record") from exc
        if obj_type != "blob" or mode not in _ALLOWED_TREE_MODES:
            raise ImportFixError(
                "unsupported_tree_mode", f"unsupported tree entry mode {mode} ({obj_type})"
            )
        if not _FULL_OID[repo.object_format].match(oid):
            raise ImportFixError("git_output_invalid", "malformed object id in tree")
        if _relative_path_error(path) is not None:
            raise ImportFixError("unsafe_tree_path", "tree contains an unsafe path")
        entries[path] = (mode, oid)
    return entries


def classify_tree_path(repo: Repo, commit: str, path: str) -> str:
    """Classify an exact committed path: 'file', 'dir', 'missing', or 'other'.

    Uses ``cat-file -t`` on ``<commit>:<path>`` so an exact path resolves to a blob
    (regular file), a tree (directory), or nothing (missing); a gitlink/symlink at
    the exact path reports 'other'. Used to prove a dot-prefixed source selector
    resolves to exactly one regular file rather than a hidden directory.
    """
    rc, out, _e = _run_git(repo.ctx, ["cat-file", "-t", f"{commit}:{path}"])
    if rc != 0:
        return "missing"
    kind = _text(out)
    if kind == "blob":
        return "file"
    if kind == "tree":
        return "dir"
    return "other"


def cat_blob(repo: Repo, oid: str) -> bytes:
    """Exact blob bytes (bounded by the Git runner's output ceiling)."""
    return _git(repo, ["cat-file", "blob", oid], "file_too_large")


def changed_tree_paths(repo: Repo, old: str, new: str) -> set[str]:
    """Every repo-relative path changed between two commit trees (renames off).

    Uses ``git diff-tree --raw`` -- a tree-object comparison (status/mode/object id),
    not a textual diff -- so repository attributes, local diff configuration and
    replacement refs cannot affect the classification authority.
    """
    from arena.patching.git_pipeline import _parse_raw_z

    out = _git(repo, ["diff-tree", "--raw", "-z", "--no-renames", "-r", old, new], "git_failed")
    try:
        changes = _parse_raw_z(out, repo.object_format)
    except _GitRunError as exc:
        raise ImportFixError("git_output_invalid", exc.diagnostic or "bad diff output") from exc
    paths: set[str] = set()
    for change in changes:
        paths.add(change.new_path)
        if change.old_path is not None:
            paths.add(change.old_path)
    return paths
