"""Phase 1C: immutable pack snapshot traversal, mutation detection, and rewiring."""

from __future__ import annotations

import os
import shutil
import stat

import pytest

import arena.benchmark.snapshot as snap_mod
from arena.benchmark.pack_hash import pack_checksum, stored_checksum
from arena.benchmark.snapshot import snapshot_pack
from arena.core import limits
from arena.core.errors import SnapshotError
from tests.conftest import pack_case_count

PACKS = ("v1", "audit_v1", "audit_v2")


def _pack(tmp_path):
    pack = tmp_path / "pack"
    shutil.copytree("benchmark_sets/audit_v2", pack)
    return pack


def _fs_keeps_distinct(directory, name_a, name_b) -> bool:
    """Whether this filesystem keeps two case/normalization-variant names distinct."""
    a, b = directory / name_a, directory / name_b
    try:
        a.write_text("a")
        b.write_text("b")
    except OSError:
        return False
    return a.exists() and b.exists() and a.read_text() == "a" and b.read_text() == "b"


# --------------------------------------------------------------------------- #
# Normal behavior                                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", PACKS)
def test_shipped_pack_snapshots_validates_and_checksum_unchanged(name):
    from pathlib import Path

    source = Path("benchmark_sets") / name
    with snapshot_pack(source) as snap:
        cases = snap.load_and_validate()
        assert len(cases) == pack_case_count(name)
        assert snap.checksum == pack_checksum(source) == stored_checksum(source)
        assert str(cases[0].case_dir).startswith(str(snap.root))
        snap.verify()


def test_source_modification_after_snapshot_does_not_affect_cases(tmp_path):
    pack = _pack(tmp_path)
    target = pack / "money_discount_rounding_001" / "after"
    a_file = next(p for p in target.rglob("*") if p.is_file())
    original = a_file.read_text(encoding="utf-8", errors="replace")
    with snapshot_pack(pack) as snap:
        cases = snap.load()
        case = next(c for c in cases if c.id == "money_discount_rounding_001")
        snap_file = case.case_dir / a_file.relative_to(pack / "money_discount_rounding_001")
        # Mutate (and then delete) the source after the snapshot was taken.
        a_file.write_text("TAMPERED = 1\n")
        assert snap_file.read_text(encoding="utf-8", errors="replace") == original
        shutil.rmtree(pack)
        assert snap_file.read_text(encoding="utf-8", errors="replace") == original
        snap.verify()  # source gone, snapshot intact


def test_cleanup_after_success_and_exception(tmp_path):
    pack = _pack(tmp_path)
    with snapshot_pack(pack) as snap:
        root = snap.root
        assert root.exists()
    assert not root.exists()  # cleaned on success

    with pytest.raises(RuntimeError):
        with snapshot_pack(pack) as snap:
            root = snap.root
            raise RuntimeError("boom")
    assert not root.exists()  # cleaned on exception too


def test_hidden_and_pycache_files_are_in_the_manifest(tmp_path):
    pack = _pack(tmp_path)
    (pack / ".hidden.py").write_text("X = 1\n")
    cache = pack / "money_discount_rounding_001" / "__pycache__"
    cache.mkdir()
    (cache / "x.pyc").write_bytes(b"\x00")
    with snapshot_pack(pack) as snap:
        paths = {entry.path for entry in snap.manifest}
    assert ".hidden.py" in paths
    assert "money_discount_rounding_001/__pycache__/x.pyc" in paths


# --------------------------------------------------------------------------- #
# Filesystem attacks                                                          #
# --------------------------------------------------------------------------- #


def _reason(excinfo) -> str:
    return excinfo.value.reason


def test_root_symlink_rejected(tmp_path):
    pack = _pack(tmp_path)
    link = tmp_path / "link"
    link.symlink_to(pack, target_is_directory=True)
    with pytest.raises(SnapshotError) as e:
        with snapshot_pack(link):
            pass
    assert _reason(e) == "root_symlink"


def test_missing_source_rejected(tmp_path):
    with pytest.raises(SnapshotError) as e:
        with snapshot_pack(tmp_path / "nope"):
            pass
    assert _reason(e) == "source_missing"


def test_file_symlink_rejected(tmp_path):
    pack = _pack(tmp_path)
    (pack / "evil.py").symlink_to("/etc/passwd")
    with pytest.raises(SnapshotError) as e:
        with snapshot_pack(pack):
            pass
    assert _reason(e) == "symlink_found"


def test_directory_symlink_rejected(tmp_path):
    pack = _pack(tmp_path)
    (tmp_path / "outside").mkdir()
    (pack / "linkdir").symlink_to(tmp_path / "outside", target_is_directory=True)
    with pytest.raises(SnapshotError) as e:
        with snapshot_pack(pack):
            pass
    assert _reason(e) == "symlink_found"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no mkfifo")
def test_fifo_rejected(tmp_path):
    pack = _pack(tmp_path)
    os.mkfifo(pack / "pipe")
    with pytest.raises(SnapshotError) as e:
        with snapshot_pack(pack):
            pass
    assert _reason(e) == "unsafe_file_type"


@pytest.mark.skipif(not hasattr(os, "link"), reason="no hardlinks")
def test_hardlink_alias_rejected(tmp_path):
    pack = _pack(tmp_path)
    a = pack / "a.py"
    a.write_text("X = 1\n")
    try:
        os.link(a, pack / "b.py")
    except OSError:
        pytest.skip("filesystem does not support hardlinks")
    with pytest.raises(SnapshotError) as e:
        with snapshot_pack(pack):
            pass
    assert _reason(e) == "hardlink_found"


def test_casefold_collision_rejected(tmp_path):
    pack = _pack(tmp_path)
    collide = pack / "collide"
    collide.mkdir()
    if not _fs_keeps_distinct(collide, "Foo.txt", "foo.txt"):
        pytest.skip("case-insensitive filesystem cannot hold both names")
    with pytest.raises(SnapshotError) as e:
        with snapshot_pack(pack):
            pass
    assert _reason(e) == "path_collision"


def test_nfc_collision_rejected(tmp_path):
    pack = _pack(tmp_path)
    collide = pack / "collide"
    collide.mkdir()
    composed, decomposed = "é.py", "é.py"  # é vs e + combining acute
    if not _fs_keeps_distinct(collide, composed, decomposed):
        pytest.skip("normalization-insensitive filesystem cannot hold both names")
    with pytest.raises(SnapshotError) as e:
        with snapshot_pack(pack):
            pass
    assert _reason(e) == "path_collision"


def test_excessive_file_count_rejected(tmp_path, monkeypatch):
    pack = _pack(tmp_path)
    monkeypatch.setattr(limits, "SNAPSHOT_MAX_FILES", 5)
    with pytest.raises(SnapshotError) as e:
        with snapshot_pack(pack):
            pass
    assert _reason(e) == "file_count_exceeded"


def test_excessive_directory_count_rejected(tmp_path, monkeypatch):
    pack = _pack(tmp_path)
    monkeypatch.setattr(limits, "SNAPSHOT_MAX_DIRS", 2)
    with pytest.raises(SnapshotError) as e:
        with snapshot_pack(pack):
            pass
    assert _reason(e) == "directory_count_exceeded"


def test_excessive_total_bytes_rejected(tmp_path, monkeypatch):
    pack = _pack(tmp_path)
    monkeypatch.setattr(limits, "SNAPSHOT_MAX_TOTAL_BYTES", 100)
    with pytest.raises(SnapshotError) as e:
        with snapshot_pack(pack):
            pass
    assert _reason(e) == "total_bytes_exceeded"


def test_excessive_depth_rejected(tmp_path, monkeypatch):
    pack = _pack(tmp_path)
    monkeypatch.setattr(limits, "SNAPSHOT_MAX_DEPTH", 1)
    with pytest.raises(SnapshotError) as e:
        with snapshot_pack(pack):
            pass
    assert _reason(e) == "path_too_deep"


class _FakeStat:
    """A mutable copy of an os.stat_result for deterministic race injection."""

    def __init__(self, st, *, mtime_bump=0):
        self.st_mode = st.st_mode
        self.st_dev = st.st_dev
        self.st_ino = st.st_ino
        self.st_size = st.st_size
        self.st_mtime_ns = st.st_mtime_ns + mtime_bump
        self.st_nlink = st.st_nlink


def _fstat_bumping(predicate):
    """Wrap os.fstat, bumping the mtime of the stat for which predicate(state) is true."""
    real = os.fstat
    counters = {"reg": 0, "dir": 0}

    def fake(fd):
        st = real(fd)
        if stat.S_ISREG(st.st_mode):
            counters["reg"] += 1
        elif stat.S_ISDIR(st.st_mode):
            counters["dir"] += 1
        return _FakeStat(st, mtime_bump=1) if predicate(st, counters) else st

    return fake


def test_file_changed_before_read_detected(tmp_path, monkeypatch):
    # The first regular-file fstat (identity-before-read) reports a changed mtime:
    # the copy must fail before any byte of the replacement is accepted.
    pack = _pack(tmp_path)
    fake = _fstat_bumping(lambda st, c: stat.S_ISREG(st.st_mode) and c["reg"] == 1)
    monkeypatch.setattr(snap_mod.os, "fstat", fake)
    with pytest.raises(SnapshotError) as e:
        with snapshot_pack(pack):
            pass
    assert _reason(e) == "file_changed_during_copy"


def test_file_changed_during_copy_detected(tmp_path, monkeypatch):
    # The second regular-file fstat (post-read) reports a changed mtime.
    pack = _pack(tmp_path)
    fake = _fstat_bumping(lambda st, c: stat.S_ISREG(st.st_mode) and c["reg"] == 2)
    monkeypatch.setattr(snap_mod.os, "fstat", fake)
    with pytest.raises(SnapshotError) as e:
        with snapshot_pack(pack):
            pass
    assert _reason(e) == "file_changed_during_copy"


@pytest.mark.skipif(not snap_mod._DIR_FD, reason="descriptor-anchored traversal only")
def test_root_changed_during_traversal_detected(tmp_path, monkeypatch):
    # The opened root descriptor's identity no longer matches the discovered root.
    pack = _pack(tmp_path)
    fake = _fstat_bumping(lambda st, c: stat.S_ISDIR(st.st_mode) and c["dir"] == 1)
    monkeypatch.setattr(snap_mod.os, "fstat", fake)
    with pytest.raises(SnapshotError) as e:
        with snapshot_pack(pack):
            pass
    assert _reason(e) == "tree_changed_during_copy"


@pytest.mark.skipif(not snap_mod._DIR_FD, reason="descriptor-anchored traversal only")
def test_directory_identity_changed_during_traversal_detected(tmp_path, monkeypatch):
    # The first child directory opened relative to its parent fd has a changed identity.
    pack = _pack(tmp_path)
    fake = _fstat_bumping(lambda st, c: stat.S_ISDIR(st.st_mode) and c["dir"] == 2)
    monkeypatch.setattr(snap_mod.os, "fstat", fake)
    with pytest.raises(SnapshotError) as e:
        with snapshot_pack(pack):
            pass
    assert _reason(e) == "tree_changed_during_copy"


def _copy_tree_then(pack, mutate):
    """Monkeypatch wrapper: run the real copy, then mutate the source tree."""
    real = snap_mod._copy_tree

    def wrapper(source, dst_root, root_info):
        result = real(source, dst_root, root_info)
        mutate(pack)
        return result

    return wrapper


@pytest.mark.parametrize(
    "mutate",
    [
        lambda pack: (pack / "sneaked_in.py").write_text("X = 1\n"),  # file added
        lambda pack: (pack / "manifest.yaml").unlink(),  # file removed
        lambda pack: (pack / "empty_added").mkdir(),  # empty dir added
        lambda pack: __import__("shutil").rmtree(
            pack / "money_discount_rounding_001"
        ),  # dir removed
    ],
    ids=["file_added", "file_removed", "empty_dir_added", "dir_removed"],
)
def test_source_tree_change_during_copy_detected(tmp_path, monkeypatch, mutate):
    pack = _pack(tmp_path)
    monkeypatch.setattr(snap_mod, "_copy_tree", _copy_tree_then(pack, mutate))
    with pytest.raises(SnapshotError) as e:
        with snapshot_pack(pack):
            pass
    assert _reason(e) == "tree_changed_during_copy"


def test_empty_directory_is_in_the_manifest_and_compared(tmp_path):
    # An empty directory is part of the full seal: it appears in the manifest.
    pack = _pack(tmp_path)
    (pack / "empty_dir").mkdir()
    with snapshot_pack(pack) as snap:
        dirs = {e.path for e in snap.manifest if e.kind == "dir"}
    assert "empty_dir" in dirs


# -- write handling and destination verification -------------------------------


def test_partial_writes_are_completed(tmp_path, monkeypatch):
    # A short os.write (one byte at a time) must be looped to completion, not lost.
    pack = _pack(tmp_path)
    real_write = os.write

    def one_byte(fd, data):
        return real_write(fd, bytes(data[:1]))

    monkeypatch.setattr(snap_mod.os, "write", one_byte)
    with snapshot_pack(pack) as snap:
        snap.verify()  # every file copied completely despite short writes


def test_zero_progress_write_fails(tmp_path, monkeypatch):
    pack = _pack(tmp_path)
    monkeypatch.setattr(snap_mod.os, "write", lambda fd, data: 0)
    with pytest.raises(SnapshotError) as e:
        with snapshot_pack(pack):
            pass
    assert _reason(e) == "destination_write_failed"


def test_destination_truncation_detected(tmp_path, monkeypatch):
    pack = _pack(tmp_path)
    real_write_all = snap_mod._write_all

    def drop_last(fd, data):
        real_write_all(fd, data[:-1] if len(data) > 1 else data)

    monkeypatch.setattr(snap_mod, "_write_all", drop_last)
    with pytest.raises(SnapshotError) as e:
        with snapshot_pack(pack):
            pass
    assert _reason(e) == "destination_verification_failed"


def test_destination_content_mismatch_detected(tmp_path, monkeypatch):
    pack = _pack(tmp_path)
    real_write_all = snap_mod._write_all

    def corrupt(fd, data):
        real_write_all(fd, bytes(b ^ 0x01 for b in data))

    monkeypatch.setattr(snap_mod, "_write_all", corrupt)
    with pytest.raises(SnapshotError) as e:
        with snapshot_pack(pack):
            pass
    assert _reason(e) == "destination_verification_failed"


def test_destination_mode_mismatch_detected(tmp_path, monkeypatch):
    pack = _pack(tmp_path)
    real_chmod = os.chmod

    def wrong_mode(path, mode):
        # Corrupt only file modes; directories keep 0o755 so traversal still works.
        return real_chmod(path, mode if os.path.isdir(path) else 0o600)

    monkeypatch.setattr(snap_mod.os, "chmod", wrong_mode)
    with pytest.raises(SnapshotError) as e:
        with snapshot_pack(pack):
            pass
    assert _reason(e) == "destination_verification_failed"


# -- full snapshot seal verification -------------------------------------------


@pytest.mark.parametrize(
    "tamper",
    [
        lambda root: (root / "injected.py").write_text("X = 1\n"),  # file added
        lambda root: (root / "manifest.yaml").unlink(),  # file removed
        lambda root: (root / "manifest.yaml").write_text("name: x\ncases: []\n"),  # file modified
        lambda root: (root / "empty_added").mkdir(),  # empty dir added
        lambda root: (root / "manifest.yaml").chmod(0o600),  # mode changed
    ],
    ids=["add_file", "remove_file", "modify_file", "add_empty_dir", "mode_change"],
)
def test_full_seal_detects_snapshot_tampering(tmp_path, tamper):
    pack = _pack(tmp_path)
    with pytest.raises(SnapshotError) as e:
        with snapshot_pack(pack) as snap:
            tamper(snap.root)
    assert _reason(e) == "snapshot_changed_after_sealing"


def test_full_seal_detects_root_checksum_modification(tmp_path):
    pack = _pack(tmp_path)
    with pytest.raises(SnapshotError) as e:
        with snapshot_pack(pack) as snap:
            # The root pack.sha256 is excluded from the PUBLIC checksum but is part of
            # the full seal, so modifying it must still be detected.
            (snap.root / "pack.sha256").write_text("deadbeef\n")
    assert _reason(e) == "snapshot_changed_after_sealing"


# -- bounded enumeration -------------------------------------------------------


def test_enumeration_stops_at_the_entry_bound(tmp_path, monkeypatch):
    # A fake scandir that yields far more entries than the cap; traversal must stop
    # at the bound rather than consuming the whole (here, counted) iterator.
    pack = _pack(tmp_path)
    monkeypatch.setattr(limits, "SNAPSHOT_MAX_ENTRIES", 5)
    consumed = {"n": 0}

    class _Entry:
        def __init__(self, name):
            self.name = name

        def stat(self, follow_symlinks=True):
            st = os.stat(__file__)
            return _FakeStat(st)

    class _It:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            return self

        def __next__(self):
            consumed["n"] += 1
            if consumed["n"] > 100000:
                raise AssertionError("iterator consumed past the bound")
            return _Entry(f"f{consumed['n']:06d}.py")

    monkeypatch.setattr(snap_mod.os, "scandir", lambda arg: _It())
    with pytest.raises(SnapshotError) as e:
        with snapshot_pack(pack):
            pass
    assert _reason(e) == "entry_count_exceeded"
    assert consumed["n"] <= limits.SNAPSHOT_MAX_ENTRIES + 1


# --------------------------------------------------------------------------- #
# Integration                                                                 #
# --------------------------------------------------------------------------- #


def test_validate_cli_rejects_symlinked_pack(tmp_path):
    import typer

    from arena.cli.commands.validate import validate

    pack = _pack(tmp_path)
    (pack / "evil.py").symlink_to("/etc/passwd")
    with pytest.raises(typer.Exit) as e:
        validate(pack)
    assert e.value.exit_code == 1


def test_run_records_snapshot_metadata_and_round_trips(tmp_path):
    from pathlib import Path

    from arena.benchmark.benchmark_runner import run_benchmark
    from arena.core.models import RunResult
    from arena.core.registry import create_reviewer
    from arena.storage.repository import RunRepository

    run = run_benchmark(
        Path("benchmark_sets/audit_v1"),
        create_reviewer("control:perfect"),
        output_dir=tmp_path / "runs",
        db_path=tmp_path / "arena.db",
        mode="review",
    )
    assert run.metadata.snapshot_file_count == 84
    assert run.metadata.snapshot_integrity_verified is True
    assert run.metadata.snapshot_total_bytes and run.metadata.snapshot_total_bytes > 0
    # No temporary snapshot path leaks into persisted evidence.
    assert "arena-snapshot-" not in run.model_dump_json()
    repo = RunRepository(tmp_path / "arena.db")
    loaded = repo.get(run.run_id)
    assert isinstance(loaded, RunResult)
    assert loaded.metadata.snapshot_file_count == 84


def test_old_run_without_snapshot_metadata_loads():
    from datetime import datetime

    from arena.core.models import RunMetadata, RunResult

    run = RunResult(
        run_id="r",
        benchmark_set="v1",
        reviewer="control",
        model="perfect",
        started_at=datetime.now(),
        completed_at=datetime.now(),
        metadata=RunMetadata(prompt_version="v1", benchmark_version="v1"),
        case_results=[],
        total_score=0.0,
        bugs_found=0,
        correct_files=0,
        correct_lines=0,
        false_positives=0,
        total_cost=0.0,
        total_latency_ms=0,
    )
    assert run.metadata.snapshot_file_count is None
    assert run.metadata.snapshot_integrity_verified is None


# --------------------------------------------------------------------------- #
# Secure checksum writing (snapshot-verified, atomic, with rollback)          #
# --------------------------------------------------------------------------- #


def test_write_checksum_success_and_idempotent(tmp_path):
    from arena.benchmark.pack_hash import stored_checksum, write_checksum

    pack = _pack(tmp_path)
    (pack / "pack.sha256").unlink()  # start with no stored checksum
    written = write_checksum(pack)
    assert stored_checksum(pack) == written
    with snapshot_pack(pack) as snap:
        assert snap.checksum == written
    assert write_checksum(pack) == written  # idempotent


def test_write_checksum_rolls_back_to_prior_on_source_change(tmp_path, monkeypatch):
    import arena.benchmark.pack_hash as ph

    pack = _pack(tmp_path)
    prior = (pack / "pack.sha256").read_text()
    real = ph._atomic_write

    def mutate_after_write(target, data):
        real(target, data)
        (pack / "snuck_in.py").write_text("Y = 1\n")  # non-checksum source change

    monkeypatch.setattr(ph, "_atomic_write", mutate_after_write)
    with pytest.raises(SnapshotError) as e:
        ph.write_checksum(pack)
    assert _reason(e) == "source_changed_before_checksum_write"
    assert (pack / "pack.sha256").read_text() == prior  # prior artifact restored


def test_write_checksum_removes_new_artifact_on_source_change(tmp_path, monkeypatch):
    import arena.benchmark.pack_hash as ph

    pack = _pack(tmp_path)
    (pack / "pack.sha256").unlink()  # no prior artifact
    real = ph._atomic_write

    def mutate_after_write(target, data):
        real(target, data)
        (pack / "snuck_in.py").write_text("Y = 1\n")

    monkeypatch.setattr(ph, "_atomic_write", mutate_after_write)
    with pytest.raises(SnapshotError) as e:
        ph.write_checksum(pack)
    assert _reason(e) == "source_changed_before_checksum_write"
    assert not (pack / "pack.sha256").exists()  # newly created stale artifact removed


def test_manifest_digest_is_deterministic_and_distinct_from_checksum():
    from pathlib import Path

    source = Path("benchmark_sets/audit_v2")
    with snapshot_pack(source) as a:
        digest_a, checksum_a = a.manifest_digest, a.checksum
    with snapshot_pack(source) as b:
        assert b.manifest_digest == digest_a  # deterministic
        assert b.checksum == checksum_a
    assert digest_a != checksum_a  # full seal is a distinct identity
