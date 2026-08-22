"""Git-authoritative patch application (Phase 1D).

Patch security and evidence previously depended on handwritten parsing of
reviewer diff text. This module instead treats the actual post-application Git
tree as authoritative: a candidate (or reference) patch is applied inside an
isolated Git repository, and what *actually changed* is read from Git
(`git diff --raw -z` between the baseline and result trees, plus the resulting
index), never from the patch text. The handwritten parser remains only for
non-authoritative diagnostics.

One shared transaction is used for every patch class (candidate repairs, the
canonical reference.patch, certification/determinism reference solutions, and any
patch-based mutation input), so a fix here protects them all.

Byte-exactness is proven, not assumed: every baseline index blob is required to
equal the exact worktree bytes (raw, filters disabled) and so is every final index
blob, so an attribute/encoding/eol/ident/clean-filter conversion cannot slip a
different tree past `git status`. Git subprocess output is bounded WHILE it is read
(the process group is killed at the ceiling), not after buffering. Protected-path
matching is portable and case-insensitive. Git metadata is removed fail-closed and
its removal verified before the workspace is returned.

Scope: Git determines what actually changed; handwritten parsing is diagnostic
only. This does NOT isolate the reviewer process and does not make a self-reported
run official; later run-validity, execution and attestation phases remain pending.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from arena.core import limits
from arena.security.paths import _relative_path_error  # portable relative-path policy

GIT = "git"
_DIAG_LEN = 2048
_READ_CHUNK = 65536

# Regular-file modes Arena accepts in a result tree. 120000 is a symlink, 160000 a
# gitlink/submodule; both are rejected, as is any other/unknown mode.
_ALLOWED_MODES = frozenset({"100644", "100755"})
_SYMLINK_MODE = "120000"
_GITLINK_MODE = "160000"
# Form-level validity for Git machine output (policy is applied separately).
_OCTAL_MODE = re.compile(r"\A[0-7]{6}\Z")
_KNOWN_MODES = frozenset({"000000", "100644", "100755", "120000", "160000"})
_RAW_STATUS = frozenset({"A", "M", "D", "T", "R", "C"})

# Files that influence test collection or execution regardless of location; a patch
# may never create, modify, rename into/out of, or delete them. Git metadata control
# files are included so a patch can never introduce repository control content.
PROTECTED_BASENAMES = frozenset(
    {
        "conftest.py",
        # Every name pytest itself searches for a config in, in its own discovery
        # order (_pytest.config.findpaths.locate_config). The dotted and .toml
        # spellings outrank pytest.ini/pyproject.toml/tox.ini/setup.cfg, so leaving
        # any of them unprotected lets a patch install `addopts` (for example
        # `-p <plugin>`, which explicit -p loads even under
        # PYTEST_DISABLE_PLUGIN_AUTOLOAD) and force a green exit code while the
        # seeded bug is untouched and the suite still reports failures.
        "pytest.toml",
        ".pytest.toml",
        "pytest.ini",
        ".pytest.ini",
        "tox.ini",
        "setup.cfg",
        "pyproject.toml",
        # The pinned test command is `<interpreter> -m pytest`, which puts the
        # workspace root first on sys.path, so a root-level `pytest.py` module or
        # `pytest/` package shadows the real runner and can exit 0 without ever
        # collecting a test. Interpreter-startup shadowing is already covered by
        # sitecustomize/usercustomize below.
        "pytest.py",
        "pytest",
        "sitecustomize.py",
        "usercustomize.py",
        ".git",
        ".gitmodules",
        ".gitattributes",
    }
)


class _GitRunError(Exception):
    """Internal: a Git invocation failed in a way that maps to a stable reason code."""

    def __init__(self, reason: str, diagnostic: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.diagnostic = diagnostic[:_DIAG_LEN]


@dataclass(frozen=True)
class GitChange:
    """One authoritative change derived from Git (rename detection disabled)."""

    status: str  # A, M, D, T (R/C only if rename detection were enabled)
    old_mode: str
    new_mode: str
    old_sha: str
    new_sha: str
    old_path: str | None
    new_path: str


@dataclass
class GitPatchResult:
    """Structured, authoritative evidence for one patch transaction."""

    applied: bool
    reason: str | None = None
    diagnostic: str | None = None
    patch_sha256: str | None = None
    git_version: str | None = None
    object_format: str | None = None
    baseline_tree: str | None = None
    result_tree: str | None = None
    changes: tuple[GitChange, ...] = ()
    touched_files: tuple[str, ...] = ()
    added: tuple[str, ...] = ()
    modified: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()
    mode_changes: tuple[str, ...] = ()
    protected_violations: tuple[str, ...] = ()
    unsafe_paths: tuple[str, ...] = ()
    duration_ms: int = 0
    workspace: Path | None = None


# --------------------------------------------------------------------------- #
# Isolated, bounded Git invocation                                            #
# --------------------------------------------------------------------------- #


def _git_env(home: Path, empty_config: Path, ceiling: Path) -> dict[str, str]:
    env: dict[str, str] = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home),
        "GIT_CONFIG_GLOBAL": str(empty_config),
        "GIT_CONFIG_SYSTEM": str(empty_config),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
        "GIT_EDITOR": "true",
        "GIT_ASKPASS": "true",
        "GIT_ALLOW_PROTOCOL": "",
        "GIT_CEILING_DIRECTORIES": str(ceiling),
        "LC_ALL": "C",
        "LANG": "C",
    }
    # Keep the minimum host vars Git needs to run, never user Git configuration.
    for key in ("PATH", "SYSTEMROOT", "SystemRoot", "WINDIR", "TEMP", "TMP", "PATHEXT"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def _git_config(hooks_dir: Path) -> list[str]:
    """Deterministic repository config passed as ``-c`` flags (highest precedence)."""
    pairs = [
        f"core.hooksPath={hooks_dir}",
        "core.pager=",
        "core.autocrlf=false",
        "core.safecrlf=false",
        "core.fsmonitor=false",
        "core.attributesfile=",
        "commit.gpgsign=false",
        "gc.auto=0",
        "protocol.allow=never",
        "core.editor=true",
        "advice.detachedHead=false",
    ]
    flags: list[str] = []
    for pair in pairs:
        flags.extend(["-c", pair])
    return flags


@dataclass
class _GitContext:
    cwd: Path
    env: dict[str, str]
    config: list[str]
    timeout: float
    scrub: tuple[str, ...] = ()  # private absolute paths to strip from diagnostics


def _run_git(ctx: _GitContext, args: list[str], *, stdin: bytes = b"") -> tuple[int, bytes, bytes]:
    """Run one isolated Git command with output bounded WHILE it is read.

    stdout/stderr are consumed by reader threads that retain at most
    ``GIT_OUTPUT_BYTES + 1`` bytes each; when either stream exceeds the ceiling the
    whole process group is terminated immediately rather than after buffering. A
    writer thread delivers the exact stdin bytes (no pipe deadlock). Distinguishes
    git_timeout, git_output_too_large and a normal nonzero exit.
    """
    command = [GIT, *ctx.config, *args]
    popen_kwargs: dict[str, object] = {
        "cwd": str(ctx.cwd),
        "env": ctx.env,
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "bufsize": 0,
    }
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(command, **popen_kwargs)  # type: ignore[call-overload]
    except FileNotFoundError as exc:
        raise _GitRunError("git_unavailable", "git executable not found") from exc

    cap = limits.GIT_OUTPUT_BYTES
    buffers: dict[int, bytearray] = {1: bytearray(), 2: bytearray()}
    overflow = threading.Event()
    lock = threading.Lock()

    def reader(stream: Any, key: int) -> None:
        try:
            while True:
                chunk = stream.read(_READ_CHUNK)
                if not chunk:
                    return
                with lock:
                    buffers[key].extend(chunk)
                    too_big = len(buffers[key]) > cap or len(buffers[1]) + len(buffers[2]) > cap
                if too_big:
                    overflow.set()
                    _terminate(process)  # kill the group; stop accepting output now
                    return
        except (OSError, ValueError):
            return
        finally:
            try:
                stream.close()
            except OSError:
                pass

    def writer() -> None:
        try:
            if stdin:
                process.stdin.write(stdin)
        except (OSError, ValueError):
            pass
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass

    threads = [
        threading.Thread(target=reader, args=(process.stdout, 1), daemon=True),
        threading.Thread(target=reader, args=(process.stderr, 2), daemon=True),
        threading.Thread(target=writer, daemon=True),
    ]
    for thread in threads:
        thread.start()
    try:
        process.wait(timeout=ctx.timeout)
    except subprocess.TimeoutExpired as exc:
        _terminate(process)
        for thread in threads:
            thread.join(2)
        raise _GitRunError("git_timeout", "git command timed out") from exc
    for thread in threads:
        thread.join(5)
    if overflow.is_set():
        raise _GitRunError("git_output_too_large", "git produced too much output")
    with lock:
        out, err = bytes(buffers[1]), bytes(buffers[2])
    if len(out) > cap or len(err) > cap:
        raise _GitRunError("git_output_too_large", "git produced too much output")
    return process.returncode, out, err


def _terminate(process: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(process.pid), 9)
        else:
            process.kill()
    except (ProcessLookupError, OSError):
        pass


def _git_checked(ctx: _GitContext, args: list[str], reason: str, *, stdin: bytes = b"") -> bytes:
    rc, out, err = _run_git(ctx, args, stdin=stdin)
    if rc != 0:
        raise _GitRunError(reason, _scrub(ctx, _text(err) or _text(out)))
    return out


def _text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").strip()


def _scrub(ctx: _GitContext, text: str) -> str:
    """Strip private absolute paths (workspace, HOME, Git metadata) from diagnostics."""
    cleaned = text
    for secret in ctx.scrub:
        if secret:
            cleaned = cleaned.replace(secret, "<private>")
    return cleaned[:_DIAG_LEN]


# --------------------------------------------------------------------------- #
# Workspace preparation and baseline path validation                         #
# --------------------------------------------------------------------------- #


def _prepare_workspace(source_dir: Path, destination: Path) -> None:
    """Copy the snapshot subtree into a fresh destination and verify it is safe.

    Rejects symlinks, special files, and any ``.git`` entry before Git touches it.
    """
    if destination.exists():
        shutil.rmtree(destination, ignore_errors=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_dir, destination, symlinks=True)
    for current, dirnames, filenames in os.walk(destination):
        rel_root = Path(current)
        for name in [*dirnames, *filenames]:
            if name.casefold() == ".git":
                raise _GitRunError("baseline_index_failed", "source contains a .git entry")
            info = (rel_root / name).lstat()
            if stat.S_ISLNK(info.st_mode):
                raise _GitRunError("baseline_index_failed", "source contains a symlink")
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise _GitRunError("baseline_index_failed", "source contains a special file")


def _validate_baseline_paths(destination: Path) -> None:
    """Reject any unsafe baseline path (incl. dot-prefixed control files) before Git add.

    Validating the complete baseline path set first means a ``.gitattributes`` or other
    repository-control file is rejected before it could influence Git.
    """
    for current, _dirnames, filenames in os.walk(destination):
        for name in filenames:
            relative = (Path(current) / name).relative_to(destination).as_posix()
            if _relative_path_error(relative) is not None:
                raise _GitRunError("baseline_index_failed", "baseline contains an unsafe path")


# --------------------------------------------------------------------------- #
# Strict parsing of Git machine output                                        #
# --------------------------------------------------------------------------- #


def _sha_len(object_format: str) -> int:
    return 64 if object_format == "sha256" else 40


def _is_hex_sha(value: str, length: int) -> bool:
    return len(value) == length and all(c in "0123456789abcdef" for c in value)


def _decode_path(raw: bytes) -> str:
    try:
        path = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _GitRunError("unsafe_result_path", "git reported an undecodable path") from exc
    if not path:
        raise _GitRunError("git_output_invalid", "git reported an empty path")
    return path


def _split_z(data: bytes) -> list[bytes]:
    if data and not data.endswith(b"\0"):
        raise _GitRunError("git_output_invalid", "git output is not NUL-terminated")
    parts = data.split(b"\0")
    if parts and parts[-1] == b"":
        parts.pop()
    return parts


def _parse_raw_z(data: bytes, object_format: str = "sha1") -> list[GitChange]:
    """Parse ``git diff --raw -z`` records strictly (rename detection disabled upstream)."""
    parts = _split_z(data)
    changes: list[GitChange] = []
    seen_new: set[str] = set()
    index = 0
    while index < len(parts):
        meta = parts[index]
        if not meta.startswith(b":"):
            raise _GitRunError("git_output_invalid", "unexpected raw-diff record")
        try:
            fields = meta[1:].decode("ascii").split(" ")
        except UnicodeDecodeError as exc:
            raise _GitRunError("git_output_invalid", "non-ascii raw-diff metadata") from exc
        if len(fields) != 5:
            raise _GitRunError("git_output_invalid", "malformed raw-diff metadata")
        old_mode, new_mode, old_sha, new_sha, status = fields
        if not (_OCTAL_MODE.match(old_mode) and _OCTAL_MODE.match(new_mode)):
            raise _GitRunError("git_output_invalid", "malformed mode in raw diff")
        # `git diff --raw` abbreviates object ids (and --full-index does not expand them
        # in raw mode), so validate they are hex but not a fixed length; the authoritative
        # full-length object ids come from `git ls-files -s` (checked there).
        for sha in (old_sha, new_sha):
            if not sha or any(c not in "0123456789abcdef" for c in sha):
                raise _GitRunError("git_output_invalid", "malformed object id in raw diff")
        letter = status[:1]
        if letter not in _RAW_STATUS:
            raise _GitRunError("git_output_invalid", f"unknown raw-diff status {status!r}")
        if letter in {"R", "C"}:
            if index + 2 >= len(parts):
                raise _GitRunError("git_output_invalid", "truncated rename record")
            change = GitChange(
                letter,
                old_mode,
                new_mode,
                old_sha,
                new_sha,
                _decode_path(parts[index + 1]),
                _decode_path(parts[index + 2]),
            )
            index += 3
        else:
            if index + 1 >= len(parts):
                raise _GitRunError("git_output_invalid", "truncated raw-diff record")
            change = GitChange(
                letter, old_mode, new_mode, old_sha, new_sha, None, _decode_path(parts[index + 1])
            )
            index += 2
        if change.new_path in seen_new:
            raise _GitRunError("git_output_invalid", "duplicate change record")
        seen_new.add(change.new_path)
        changes.append(change)
    return changes


def _parse_ls_files_z(data: bytes, object_format: str = "sha1") -> list[tuple[str, str, str]]:
    """Parse ``git ls-files -s -z`` into (mode, sha, path); reject unmerged/duplicate."""
    sha_len = _sha_len(object_format)
    entries: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for record in _split_z(data):
        try:
            meta, raw_path = record.split(b"\t", 1)
        except ValueError as exc:
            raise _GitRunError("git_output_invalid", "malformed ls-files record") from exc
        try:
            mode, sha, stage = meta.decode("ascii").split()
        except (UnicodeDecodeError, ValueError) as exc:
            raise _GitRunError("git_output_invalid", "malformed ls-files metadata") from exc
        if not _OCTAL_MODE.match(mode) or mode not in _KNOWN_MODES:
            raise _GitRunError("git_output_invalid", "malformed ls-files mode")
        if not _is_hex_sha(sha, sha_len):
            raise _GitRunError("git_output_invalid", "malformed ls-files object id")
        if stage != "0":
            raise _GitRunError("unmerged_index", "the index has unmerged stages")
        path = _decode_path(raw_path)
        if path in seen:
            raise _GitRunError("git_output_invalid", "duplicate index path")
        seen.add(path)
        entries.append((mode, sha, path))
    return entries


# --------------------------------------------------------------------------- #
# Result policy: portable protected paths, modes, collisions                  #
# --------------------------------------------------------------------------- #


def _norm(component: str) -> str:
    return unicodedata.normalize("NFC", component).casefold()


def is_protected(path: str, protected: list[str]) -> bool:
    """Portable, case-insensitive, normalization-aware, component-wise protection."""
    parts = [_norm(part) for part in path.replace("\\", "/").split("/") if part]
    basenames = {_norm(name) for name in PROTECTED_BASENAMES}
    if any(part in basenames for part in parts):
        return True
    for rule in protected:
        rule_parts = [_norm(part) for part in rule.replace("\\", "/").strip("/").split("/") if part]
        if rule_parts and parts[: len(rule_parts)] == rule_parts:
            return True
    return False


def _normalized_key(path: str) -> str:
    return _norm(path)


def _changed_paths(change: GitChange) -> list[str]:
    paths = [change.new_path]
    if change.old_path is not None:
        paths.append(change.old_path)
    return paths


def _validate_result(
    changes: list[GitChange], index: list[tuple[str, str, str]], protected: list[str]
) -> tuple[list[str], list[str], list[str], list[str], list[str]]:
    """Apply path, mode, protection and collision policy; return categorized paths."""
    unsafe: list[str] = []
    protected_hits: list[str] = []
    added: list[str] = []
    modified: list[str] = []
    deleted: list[str] = []
    mode_changes: list[str] = []

    for change in changes:
        for mode in (change.old_mode, change.new_mode):
            if mode == _SYMLINK_MODE:
                raise _GitRunError("unsafe_result_mode", "patch result introduces a symlink")
            if mode == _GITLINK_MODE:
                raise _GitRunError("unsafe_result_mode", "patch result introduces a gitlink")
            if mode != "000000" and mode not in _ALLOWED_MODES:
                raise _GitRunError("unsafe_result_mode", f"unsupported mode {mode}")
        for path in _changed_paths(change):
            if _relative_path_error(path) is not None:
                unsafe.append(path)
            if is_protected(path, protected):
                protected_hits.append(path)
        if change.status == "A":
            added.append(change.new_path)
        elif change.status == "D":
            deleted.append(change.old_path or change.new_path)
        elif change.status == "T":
            mode_changes.append(change.new_path)
            modified.append(change.new_path)
        else:
            modified.append(change.new_path)
        if change.old_mode != change.new_mode and "000000" not in (
            change.old_mode,
            change.new_mode,
        ):
            mode_changes.append(change.new_path)

    if unsafe:
        raise _GitRunError("unsafe_result_path", "; ".join(sorted(set(unsafe)))[:_DIAG_LEN])
    if protected_hits:
        raise _GitRunError(
            "protected_path_changed", "; ".join(sorted(set(protected_hits)))[:_DIAG_LEN]
        )

    # The COMPLETE resulting index must be path-safe, mode-safe and collision-free.
    seen: dict[str, str] = {}
    for mode, _sha, path in index:
        if mode == _SYMLINK_MODE or mode == _GITLINK_MODE or mode not in _ALLOWED_MODES:
            raise _GitRunError("unsafe_result_mode", f"index entry has unsupported mode {mode}")
        if _relative_path_error(path) is not None:
            raise _GitRunError("unsafe_result_path", "index contains an unsafe path")
        if is_protected(path, protected) and any(
            path == c.new_path or path == c.old_path for c in changes
        ):
            raise _GitRunError("protected_path_changed", "index change touches a protected path")
        key = _normalized_key(path)
        if key in seen and seen[key] != path:
            raise _GitRunError("path_collision", "index paths collide under normalization")
        seen[key] = path

    return added, modified, deleted, mode_changes, sorted({c.new_path for c in changes})


# --------------------------------------------------------------------------- #
# Exact byte-equivalence and filesystem scan                                  #
# --------------------------------------------------------------------------- #


def _raw_blob_id(ctx: _GitContext, relative: str) -> str:
    """Git blob object id of the exact worktree bytes, with all filters disabled."""
    out = _git_checked(
        ctx, ["hash-object", "--no-filters", "--", relative], "index_worktree_mismatch"
    )
    return _text(out)


def _verify_index_bytes(
    ctx: _GitContext, index: list[tuple[str, str, str]], object_format: str, reason: str
) -> None:
    """Require every index blob to equal the exact (no-filter) worktree bytes + mode.

    Independent of ``git status`` (which can use the same check-in conversions as the
    index), so an attribute/eol/ident/clean-filter transform cannot pass unnoticed.
    """
    sha_len = _sha_len(object_format)
    for mode, sha, path in index:
        if mode not in _ALLOWED_MODES:
            raise _GitRunError(reason, f"index entry has unsupported mode {mode}")
        if _relative_path_error(path) is not None:
            raise _GitRunError(reason, "index contains an unsafe path")
        if not _is_hex_sha(sha, sha_len):
            raise _GitRunError(reason, "index object id has the wrong length/format")
        worktree_file = ctx.cwd / path
        info = worktree_file.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise _GitRunError(reason, "index entry is not a regular worktree file")
        expected_exec = mode == "100755"
        actual_exec = bool(info.st_mode & stat.S_IXUSR)
        if os.name == "posix" and expected_exec != actual_exec:
            raise _GitRunError(reason, "worktree mode disagrees with the index")
        if _raw_blob_id(ctx, path) != sha:
            raise _GitRunError(reason, "worktree bytes differ from the index blob")


def _scan_workspace_files(destination: Path) -> set[str]:
    """Securely scan the workspace: regular files only, no symlink/special/.git/hardlink."""
    found: set[str] = set()
    for current, dirnames, filenames in os.walk(destination, followlinks=False):
        for dirname in dirnames:
            if dirname.casefold() == ".git":
                raise _GitRunError("unsafe_result_entry", "git metadata remains in the workspace")
            info = (Path(current) / dirname).lstat()
            if stat.S_ISLNK(info.st_mode):
                raise _GitRunError("unsafe_result_entry", "result contains a symlinked directory")
            if not stat.S_ISDIR(info.st_mode):
                raise _GitRunError("unsafe_result_entry", "result contains a special directory")
        for name in filenames:
            if name.casefold() == ".git":
                raise _GitRunError("unsafe_result_entry", "git metadata remains in the workspace")
            path = Path(current) / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise _GitRunError("unsafe_result_entry", "result contains a symlink")
            if not stat.S_ISREG(info.st_mode):
                raise _GitRunError("unsafe_result_entry", "result contains a special file")
            if info.st_nlink > 1:
                raise _GitRunError("unsafe_result_entry", "result contains a hardlink alias")
            if _relative_path_error(path.relative_to(destination).as_posix()) is not None:
                raise _GitRunError("unsafe_result_path", "result contains an unsafe path")
            found.add(path.relative_to(destination).as_posix())
    return found


def _remove_git_metadata(destination: Path) -> None:
    """Remove .git fail-closed and verify no .git (any case) remains."""
    git_dir = destination / ".git"
    try:
        if git_dir.exists():
            shutil.rmtree(git_dir)
    except OSError as exc:
        raise _GitRunError("git_metadata_cleanup_failed", "could not remove git metadata") from exc
    for entry in destination.iterdir():
        if entry.name.casefold() == ".git":
            raise _GitRunError("git_metadata_cleanup_failed", "git metadata still present")


# --------------------------------------------------------------------------- #
# The transaction                                                             #
# --------------------------------------------------------------------------- #


def apply_patch(
    *,
    source_dir: Path,
    patch_text: str,
    protected_paths: list[str],
    destination: Path,
    timeout: float = limits.GIT_TIMEOUT_SECONDS,
) -> GitPatchResult:
    """Apply a patch to a private copy of ``source_dir``, authoritatively.

    On success the returned ``workspace`` is ``destination`` with the resulting
    source tree and no ``.git``. On any failure the destination is removed and the
    result carries a stable ``reason`` plus a bounded ``diagnostic``. Patch-policy
    failures are results, not raised exceptions, so a run never crashes on a bad
    patch. ``timeout`` bounds each Git invocation and is range-checked.
    """
    if not (limits.GIT_TIMEOUT_SECONDS_MIN <= timeout <= limits.GIT_TIMEOUT_SECONDS_MAX):
        raise ValueError(
            f"git timeout must be in [{limits.GIT_TIMEOUT_SECONDS_MIN}, "
            f"{limits.GIT_TIMEOUT_SECONDS_MAX}] seconds, got {timeout}"
        )
    started = time.perf_counter()
    if not patch_text or not patch_text.strip():
        return GitPatchResult(applied=False, reason="no_patch_provided", duration_ms=_ms(started))
    patch_bytes = patch_text.encode("utf-8")
    if len(patch_bytes) > limits.PATCH_BYTES:
        return GitPatchResult(applied=False, reason="patch_too_large", duration_ms=_ms(started))
    patch_sha = hashlib.sha256(patch_bytes).hexdigest()

    meta = Path(tempfile.mkdtemp(prefix="arena-gitmeta-"))
    home = meta / "home"
    hooks = meta / "hooks"
    home.mkdir()
    hooks.mkdir()
    (meta / "empty.gitconfig").write_text("", encoding="utf-8")
    ctx = _GitContext(
        cwd=destination,
        env=_git_env(home, meta / "empty.gitconfig", destination.parent),
        config=_git_config(hooks),
        timeout=timeout,
        scrub=(str(destination), str(meta), str(home), str(destination.parent)),
    )
    git_version: str | None = None
    object_format: str | None = None
    try:
        _prepare_workspace(source_dir, destination)
        _validate_baseline_paths(destination)
        git_version = _text(_git_checked(ctx, ["version"], "git_unavailable"))
        _git_checked(ctx, ["init", "-q"], "git_initialization_failed")
        object_format = _text(
            _git_checked(ctx, ["rev-parse", "--show-object-format"], "git_initialization_failed")
        )
        _git_checked(ctx, ["add", "-A"], "baseline_index_failed")
        # Prove the baseline tree is the EXACT accepted bytes (no filter conversion).
        baseline_index = _parse_ls_files_z(
            _git_checked(ctx, ["ls-files", "-s", "-z"], "baseline_index_failed"), object_format
        )
        _verify_index_bytes(ctx, baseline_index, object_format, "baseline_index_failed")
        baseline_tree = _text(_git_checked(ctx, ["write-tree"], "baseline_index_failed"))

        preflight_rc, _o, preflight_err = _run_git(
            ctx, ["apply", "--check", "--index", "--whitespace=nowarn", "-"], stdin=patch_bytes
        )
        if preflight_rc != 0:
            raise _GitRunError("patch_preflight_failed", _scrub(ctx, _text(preflight_err)))
        apply_rc, _o2, apply_err = _run_git(
            ctx, ["apply", "--index", "--whitespace=nowarn", "-"], stdin=patch_bytes
        )
        if apply_rc != 0:
            raise _GitRunError("patch_apply_failed", _scrub(ctx, _text(apply_err)))

        _check_status_clean(ctx)
        result_tree = _text(_git_checked(ctx, ["write-tree"], "result_tree_verification_failed"))
        raw = _git_checked(
            ctx,
            ["diff", "--raw", "-z", "--no-renames", "--full-index", baseline_tree, result_tree],
            "git_output_invalid",
        )
        changes = _parse_raw_z(raw, object_format)
        if len(changes) > limits.GIT_MAX_CHANGED_FILES:
            raise _GitRunError("git_output_too_large", "too many changed files")
        index = _parse_ls_files_z(
            _git_checked(ctx, ["ls-files", "-s", "-z"], "git_output_invalid"), object_format
        )
        if len(index) > limits.GIT_MAX_CHANGED_FILES:
            raise _GitRunError("git_output_too_large", "index too large")

        added, modified, deleted, mode_changes, touched = _validate_result(
            changes, index, protected_paths
        )
        # Prove the result tree is the EXACT worktree bytes (independent of git status).
        _verify_index_bytes(ctx, index, object_format, "index_worktree_mismatch")
        if (
            _text(_git_checked(ctx, ["write-tree"], "result_tree_verification_failed"))
            != result_tree
        ):
            raise _GitRunError("result_tree_verification_failed", "result tree changed")

        # Remove Git metadata (fail closed, verified), then securely rescan and require
        # the workspace file set to equal the index path set.
        index_paths = {path for _m, _s, path in index}
        _remove_git_metadata(destination)
        if _scan_workspace_files(destination) != index_paths:
            raise _GitRunError("index_worktree_mismatch", "workspace and index file sets differ")

        return GitPatchResult(
            applied=True,
            patch_sha256=patch_sha,
            git_version=git_version,
            object_format=object_format,
            baseline_tree=baseline_tree,
            result_tree=result_tree,
            changes=tuple(changes),
            touched_files=tuple(touched),
            added=tuple(added),
            modified=tuple(modified),
            deleted=tuple(deleted),
            mode_changes=tuple(dict.fromkeys(mode_changes)),
            workspace=destination,
            duration_ms=_ms(started),
        )
    except _GitRunError as exc:
        shutil.rmtree(destination, ignore_errors=True)
        return GitPatchResult(
            applied=False,
            reason=exc.reason,
            diagnostic=exc.diagnostic or None,
            patch_sha256=patch_sha,
            git_version=git_version,
            object_format=object_format,
            protected_violations=(
                tuple(exc.diagnostic.split("; ")) if exc.reason == "protected_path_changed" else ()
            ),
            unsafe_paths=(
                tuple(exc.diagnostic.split("; ")) if exc.reason == "unsafe_result_path" else ()
            ),
            duration_ms=_ms(started),
        )
    finally:
        shutil.rmtree(meta, ignore_errors=True)


def _check_status_clean(ctx: _GitContext) -> None:
    """Defense in depth: reject untracked files or worktree/index divergence.

    Byte-exact equivalence is proven separately by _verify_index_bytes; this catches
    untracked/unmerged entries that the index manifest would not list.
    """
    out = _git_checked(
        ctx, ["status", "--porcelain", "-z", "--untracked-files=all"], "git_output_invalid"
    )
    for record in out.split(b"\0"):
        if not record:
            continue
        code = record[:2]
        if code == b"??":
            raise _GitRunError("untracked_output", "patch produced an untracked file")
        if b"U" in code:
            raise _GitRunError("unmerged_index", "the index has unmerged stages")
        if len(code) >= 2 and code[1:2] != b" ":
            raise _GitRunError("index_worktree_mismatch", "worktree differs from the index")


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
