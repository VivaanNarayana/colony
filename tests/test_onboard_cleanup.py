import ctypes
import json
import os
from contextlib import suppress
from pathlib import Path

import httpx
import pytest

from colony import onboard_io, onboarding


def deny_temporary_cleanup(monkeypatch):
    original = Path.unlink
    denied = []

    def unlink(path, *args, **kwargs):
        if path.name.startswith(".colony-write-"):
            denied.append(path)
            raise PermissionError("simulated Windows sharing violation")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    return original, denied


def test_bootstrap_cleanup_denial_keeps_publication_and_retry_valid(tmp_path, monkeypatch):
    workspace, repo = tmp_path / "team", tmp_path / "repo"
    repo.mkdir()
    original, denied = deny_temporary_cleanup(monkeypatch)
    assert onboarding.initialize(workspace, repo)["ok"]
    assert denied
    before = {p.relative_to(workspace): p.read_bytes() for p in workspace.rglob("*") if p.is_file()}
    assert all(p.stat().st_nlink == 1 for p in workspace.rglob("*") if p.is_file())
    monkeypatch.setattr(Path, "unlink", original)
    assert onboarding.initialize(workspace, repo)["reused"]
    after = {p.relative_to(workspace): p.read_bytes() for p in workspace.rglob("*") if p.is_file()}
    assert before == after
    onboard_io.check_tree(workspace)


def test_cleanup_never_masks_original_publication_error(tmp_path, monkeypatch):
    primary = OSError("original publication failure")

    def fail(*args):
        raise primary

    monkeypatch.setattr(onboard_io, "_publish_exclusive", fail)
    _, denied = deny_temporary_cleanup(monkeypatch)
    destination = tmp_path / "new-file"
    with pytest.raises(OSError) as error:
        onboard_io.write_new(destination, b"private fixture")
    assert error.value is primary and not destination.exists() and denied
    assert all(path.stat().st_nlink == 1 for path in denied if path.exists())
    onboard_io.check_tree(tmp_path)


def test_new_file_publication_never_replaces_existing_target(tmp_path):
    destination = tmp_path / "existing"
    destination.write_bytes(b"canary")
    with pytest.raises((OSError, ValueError)):
        onboard_io.write_new(destination, b"replacement")
    assert destination.read_bytes() == b"canary"


def test_external_hardlink_guard_is_not_relaxed(tmp_path):
    outside, target = tmp_path / "outside", tmp_path / "target"
    outside.write_bytes(b"canary")
    os.link(outside, target)
    with pytest.raises(ValueError, match="hard links"):
        onboard_io.write_new(target, b"replacement")
    assert outside.read_bytes() == target.read_bytes() == b"canary"


def test_enrollment_cleanup_denial_preserves_credentials_and_reuse(tmp_path, monkeypatch):
    repo, workspace = tmp_path / "repo", tmp_path / "team"
    repo.mkdir()
    onboarding.initialize(workspace, repo)
    home, keyfile, issuer = (
        tmp_path / "joined",
        workspace / "agent-a/identity.json",
        workspace / "administrator/issuer.pub",
    )
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        return httpx.Response(
            200, json={"member_id": "agent-a"}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx, "post", post)
    original, denied = deny_temporary_cleanup(monkeypatch)
    assert onboarding.enroll(home, keyfile, issuer)["ok"] and denied
    identity = (home / "identity.json").read_bytes()
    assert all(p.stat().st_nlink == 1 for p in home.rglob("*") if p.is_file())
    monkeypatch.setattr(Path, "unlink", original)
    assert onboarding.enroll(home, keyfile, issuer)["reused"]
    assert (home / "identity.json").read_bytes() == identity and len(calls) == 1


@pytest.mark.skipif(os.name != "nt", reason="Native Windows sharing-lock regression")
def test_native_windows_lock_after_publication_allows_bootstrap_retry(tmp_path, monkeypatch):
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    open_file = kernel.CreateFileW
    open_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    open_file.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handles = []

    def lock_plan(source, target):
        if Path(target).name == ".colony-bootstrap-plan.json" and not handles:
            # Omit FILE_SHARE_DELETE, using an actual OS handle, not an unlink mock.
            # Lock the staging name if publication left one behind (the original
            # bug), otherwise lock the sole published name after atomic rename.
            path = source if Path(source).exists() else target
            handle = open_file(str(path), 0x80000000, 3, None, 3, 0x80, None)
            assert handle not in (None, ctypes.c_void_p(-1).value), ctypes.get_last_error()
            handles.append(handle)

    real_rename, real_link = onboard_io.os.rename, onboard_io.os.link

    def rename(source, target, *args, **kwargs):
        real_rename(source, target, *args, **kwargs)
        lock_plan(source, target)

    def link(source, target, *args, **kwargs):
        real_link(source, target, *args, **kwargs)
        lock_plan(source, target)

    monkeypatch.setattr(onboard_io.os, "rename", rename)
    monkeypatch.setattr(onboard_io.os, "link", link)
    workspace, repo = tmp_path / "team", tmp_path / "repo"
    repo.mkdir()
    try:
        # An open descendant can transiently prevent final directory publication.
        with suppress(ValueError):
            onboarding.initialize(workspace, repo)
        assert handles
        location = workspace if workspace.exists() else onboarding._stage(workspace, "bootstrap")
        assert (location / ".colony-bootstrap-plan.json").stat().st_nlink == 1
        onboard_io.check_tree(location)
        issuer = (location / "administrator/issuer.pem").read_bytes()
    finally:
        for handle in handles:
            assert kernel.CloseHandle(handle)
    monkeypatch.setattr(onboard_io.os, "rename", real_rename)
    monkeypatch.setattr(onboard_io.os, "link", real_link)
    assert onboarding.initialize(workspace, repo)["ok"]
    assert (workspace / "administrator/issuer.pem").read_bytes() == issuer
    assert json.loads((workspace / ".colony-bootstrap.json").read_bytes())["status"] == "prepared"
