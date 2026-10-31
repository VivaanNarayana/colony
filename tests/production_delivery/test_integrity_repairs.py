"""Fake metadata/filesystem controls only; no worker, inference or benchmark collection."""

import json
import stat
import sys
import types
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import production_delivery as driver  # noqa: E402
import production_delivery_core as core  # noqa: E402
from test_contract import sample_plan  # noqa: E402


def reordered_plan():
    original = sample_plan()
    core.validate_plan(original)
    reordered = json.loads(json.dumps(original, sort_keys=True))
    assert list(original["arms"]) == ["mvp", "candidate"]
    assert list(reordered["arms"]) == ["candidate", "mvp"]
    assert reordered["plan_sha256"] == core.digest(
        {k: v for k, v in reordered.items() if k != "plan_sha256"}
    )
    return reordered


def test_canonical_hash_cannot_hide_changed_arm_order():
    with pytest.raises(ValueError, match="comparison arm order"):
        core.validate_plan(reordered_plan())


@pytest.mark.parametrize("entrypoint", ["collect_local", "report"])
def test_reordered_plan_rejected_before_collection_or_replay(tmp_path, monkeypatch, entrypoint):
    plan = tmp_path / "reordered-plan.json"
    core.put(plan, reordered_plan())
    output = tmp_path / "never-created"
    monkeypatch.setattr(
        driver, "verify_environment", lambda _: pytest.fail("No environment/model/worker setup")
    )
    monkeypatch.setattr(
        driver,
        "read",
        lambda path: (
            core.read(path) if path == plan else pytest.fail("Do not read runtime observations")
        ),
    )
    if entrypoint == "collect_local":
        with pytest.raises(ValueError, match="comparison arm order"):
            driver.collect_local(driver.ROOT, plan, output)
    else:
        with pytest.raises(ValueError, match="comparison arm order"):
            driver.report(plan, output, tmp_path / "never-written.json")
    assert not output.exists() and not (tmp_path / "never-written.json").exists()


def test_vanished_enumerated_file_does_not_abort_runtime_accounting(tmp_path, monkeypatch):
    (tmp_path / "stable.txt").write_bytes(b"stable")
    transient = tmp_path / "colony.db-wal"
    transient.write_bytes(b"FAKE transient bytes")
    lstat = Path.lstat

    def vanish(path, *args, **kwargs):
        if path == transient:
            raise FileNotFoundError("Fake WAL disappeared after enumeration")
        return lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", vanish)
    assert core.disk_bytes(tmp_path) == 6
    # Identity verification must not share runtime disappearance tolerance.
    with pytest.raises(FileNotFoundError):
        core.file_manifest(tmp_path)


def test_vanished_child_directory_does_not_abort_runtime_accounting(tmp_path, monkeypatch):
    (tmp_path / "stable.txt").write_bytes(b"stable")
    transient = tmp_path / "disappeared-child"
    transient.mkdir()
    (transient / "fake.db-shm").write_bytes(b"FAKE transient bytes")
    iterdir = Path.iterdir

    def vanish(path):
        if path == transient:
            raise FileNotFoundError("Fake child disappeared before traversal")
        return iterdir(path)

    monkeypatch.setattr(Path, "iterdir", vanish)
    assert core.disk_bytes(tmp_path) == 6
    with pytest.raises(FileNotFoundError):
        core.file_manifest(tmp_path)


def test_type_and_size_use_one_lstat_snapshot_without_followup_stat(tmp_path, monkeypatch):
    transient = tmp_path / "colony.db-wal"
    transient.write_bytes(b"snapshot")
    real_stat, following_calls = Path.stat, []

    def vanish_before_size(path, *, follow_symlinks=True):
        if path == transient and follow_symlinks:
            following_calls.append(path)
            # The old walk stats type twice, then size; the file vanishes in that gap.
            if len(following_calls) == 3:
                raise FileNotFoundError("Fake WAL disappeared before separate size stat")
        return real_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", vanish_before_size)
    assert core.disk_bytes(tmp_path) == 8
    assert not following_calls


@pytest.mark.parametrize("where", ["missing", "enumeration"])
def test_missing_runtime_root_remains_an_error(tmp_path, monkeypatch, where):
    if where == "missing":
        root = tmp_path / "never-created"
    else:
        root, iterdir = tmp_path, Path.iterdir

        def vanish(path):
            if path == root:
                raise FileNotFoundError("Fake root disappeared before enumeration")
            return iterdir(path)

        monkeypatch.setattr(Path, "iterdir", vanish)
    with pytest.raises(FileNotFoundError):
        core.disk_bytes(root)


@pytest.mark.parametrize("failure", [PermissionError, OSError])
def test_other_runtime_inspection_errors_still_propagate(tmp_path, monkeypatch, failure):
    child = tmp_path / "not-readable"
    child.write_bytes(b"private")
    lstat = Path.lstat

    def denied(path, *args, **kwargs):
        if path == child:
            raise failure("Fake denied metadata")
        return lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", denied)
    with pytest.raises(failure, match="Fake denied metadata"):
        core.disk_bytes(tmp_path)


@pytest.mark.parametrize("kind", ["symlink", "reparse", "linked_root"])
def test_runtime_link_and_reparse_guards_remain_strict(tmp_path, monkeypatch, kind):
    child = tmp_path / "link-placeholder"
    child.write_bytes(b"data")
    target = tmp_path if kind == "linked_root" else child
    lstat = Path.lstat

    def linked(path, *args, **kwargs):
        if path == target:
            return types.SimpleNamespace(
                st_mode=stat.S_IFLNK if kind != "reparse" else stat.S_IFREG,
                st_size=4,
                st_file_attributes=0x400 if kind == "reparse" else 0,
            )
        return lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", linked)
    with pytest.raises(ValueError, match="Linked"):
        core.disk_bytes(tmp_path)
    with pytest.raises(ValueError, match="Linked"):
        core.file_manifest(tmp_path)


def test_live_files_and_nested_directories_still_count(tmp_path):
    (tmp_path / "one").write_bytes(b"abc")
    child = tmp_path / "nested"
    child.mkdir()
    (child / "two").write_bytes(b"12345")
    assert core.disk_bytes(tmp_path) == 8
    assert core.file_manifest(tmp_path) == {
        "one": core.sha(tmp_path / "one"),
        "nested/two": core.sha(child / "two"),
    }
