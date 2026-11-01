import json
import os
import stat
import subprocess
from pathlib import Path

import httpx
import pytest

from colony import onboarding
from colony.client import Client
from colony.models import Keyfile
from colony.security import token


@pytest.fixture
def paths(tmp_path):
    repo = tmp_path / "project with spaces"
    repo.mkdir()
    return tmp_path / "private team", repo


def tree(path):
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def test_bootstrap_private_grants_receipt_and_repeat(paths, monkeypatch):
    workspace, repo = paths
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("bootstrap must be local"))
    result = onboarding.initialize(workspace, repo)
    assert result["ok"] and result["status"] == "prepared" and not result["reused"]
    assert result["serve_args"] == [
        "serve",
        "--repo",
        str(repo),
        "--data",
        str(workspace / "data"),
        "--issuer",
        str(workspace / "administrator" / "issuer.pub"),
        "--host",
        "127.0.0.1",
        "--port",
        "7331",
    ]
    assert not (workspace / "data").exists() and not list(repo.iterdir())
    for label in ("a", "b"):
        home = Path(result["homes"][label])
        grant = Client(home).keyfile.grant
        assert grant.member_id == "agent-" + label and grant.capture == "none"
        assert grant.scopes[0].pull and grant.scopes[0].push == (label == "a")
        assert token(Client(home).keyfile) not in json.dumps(result)
        if os.name != "nt":
            assert stat.S_IMODE(home.stat().st_mode) == 0o700
            assert stat.S_IMODE((home / "identity.json").stat().st_mode) == 0o600
    before = tree(workspace)
    assert onboarding.initialize(workspace, repo)["reused"]
    assert onboarding.initialize(workspace, repo, dry_run=True)["reused"]
    assert tree(workspace) == before
    with pytest.raises(ValueError, match="conflicts"):
        onboarding.initialize(workspace, repo, namespace="different")
    assert tree(workspace) == before


def test_new_dry_run_writes_nothing_even_when_configuring(paths, monkeypatch):
    workspace, repo = paths
    before = tree(workspace.parent)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("preview network"))
    result = onboarding.initialize(workspace, repo, dry_run=True, configure_codex=True)
    assert result["dry_run"] and result["codex"]["status"] == "preview"
    assert tree(workspace.parent) == before
    assert not workspace.exists() and not onboarding._stage(workspace, "bootstrap").exists()


@pytest.mark.parametrize(
    "server",
    [
        "https://example.com",
        "http://example.com",
        "http://user:secret@localhost:7331",
        "http://localhost:7331?",
        "http://localhost:7331#",
        "http://localhost:7331/path",
        "http://127.0.0.1:0",
        "http://localhost:999999",
        "http://localhost:7331\n",
    ],
)
def test_bootstrap_rejects_unsafe_server_before_writes(paths, server):
    workspace, repo = paths
    with pytest.raises(ValueError):
        onboarding.initialize(workspace, repo, server=server)
    assert not workspace.exists()


@pytest.mark.parametrize("namespace", ["", "../oops", "a" * 81, "contains space"])
def test_bootstrap_rejects_invalid_namespace(paths, namespace):
    with pytest.raises(ValueError):
        onboarding.initialize(*paths, namespace=namespace)
    assert not paths[0].exists()


def test_unowned_existing_and_repo_nested_destinations_are_preserved(paths):
    workspace, repo = paths
    workspace.mkdir()
    (workspace / "my-key").write_bytes(b"retain")
    before = tree(workspace)
    with pytest.raises(ValueError):
        onboarding.initialize(workspace, repo)
    with pytest.raises(ValueError):
        onboarding.initialize(repo / "credentials", repo)
    with pytest.raises(ValueError):
        onboarding.initialize(Path("relative"), repo)
    assert tree(workspace) == before and not list(repo.iterdir())


def test_interrupted_bootstrap_resumes_without_replacing_issuer(paths, monkeypatch):
    workspace, repo = paths
    original = onboarding._prepared_home
    monkeypatch.setattr(
        onboarding, "_prepared_home", lambda *a, **k: (_ for _ in ()).throw(OSError())
    )
    with pytest.raises(ValueError, match="Bootstrap incomplete"):
        onboarding.initialize(workspace, repo)
    stage = onboarding._stage(workspace, "bootstrap")
    assert (
        json.loads((stage / ".colony-bootstrap-plan.json").read_text())["status"] == "initializing"
    )
    assert not (stage / ".colony-bootstrap.json").exists()
    before = (stage / "administrator" / "issuer.pem").read_bytes()
    assert not workspace.exists()
    monkeypatch.setattr(onboarding, "_prepared_home", original)
    assert onboarding.initialize(workspace, repo)["ok"]
    assert (workspace / "administrator" / "issuer.pem").read_bytes() == before
    assert not stage.exists()


def test_complete_staging_publication_resumes(paths, monkeypatch):
    workspace, repo = paths
    original = onboarding.publish_directory
    monkeypatch.setattr(
        onboarding, "publish_directory", lambda *a: (_ for _ in ()).throw(OSError())
    )
    with pytest.raises(ValueError):
        onboarding.initialize(workspace, repo)
    stage = onboarding._stage(workspace, "bootstrap")
    before = tree(stage)
    monkeypatch.setattr(onboarding, "publish_directory", original)
    onboarding.initialize(workspace, repo)
    assert tree(workspace) == before


def test_directory_publication_never_replaces_existing_empty_target(tmp_path):
    from colony.onboard_io import private_directory, publish_directory

    stage, target = tmp_path / "stage", tmp_path / "target"
    private_directory(stage)
    (stage / "retain").write_bytes(b"source")
    target.mkdir()
    with pytest.raises((OSError, ValueError)):
        publish_directory(stage, target)
    assert (stage / "retain").read_bytes() == b"source" and not list(target.iterdir())


def test_reuse_refuses_missing_or_changed_credentials_without_repairs(paths):
    workspace, repo = paths
    result = onboarding.initialize(workspace, repo)
    issuer = workspace / "administrator" / "issuer.pub"
    issuer.unlink()
    before = tree(workspace)
    for dry_run in (True, False):
        with pytest.raises(ValueError):
            onboarding.initialize(workspace, repo, dry_run=dry_run)
        assert tree(workspace) == before
    assert result["ok"]


def test_output_hardlink_refused(paths):
    workspace, repo = paths
    onboarding.initialize(workspace, repo)
    identity = workspace / "agent-a" / "identity.json"
    os.link(identity, workspace.parent / "linked identity")
    before = tree(workspace)
    with pytest.raises(ValueError, match="hard links"):
        onboarding.initialize(workspace, repo)
    assert tree(workspace) == before


def test_symlink_parent_refused(paths):
    workspace, repo = paths
    link = workspace.parent / "directory link"
    try:
        link.symlink_to(repo, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks require privilege on this platform")
    with pytest.raises(ValueError, match="links|reparse"):
        onboarding.initialize(link / "new", repo)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction")
def test_windows_junction_parent_refused(paths):
    workspace, repo = paths
    link = workspace.parent / "junction"
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(repo)], capture_output=True, check=False
    )
    if completed.returncode:
        pytest.skip("Junction creation unavailable")
    try:
        with pytest.raises(ValueError, match="links|reparse"):
            onboarding.initialize(link / "new", repo)
        assert not list(repo.iterdir())
    finally:
        link.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL")
def test_new_windows_workspace_has_protected_owner_system_acl(paths):
    import ctypes
    from ctypes import wintypes

    workspace, repo = paths
    onboarding.initialize(workspace, repo)
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    get = api.GetFileSecurityW
    get.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    get.restype = wintypes.BOOL
    convert = api.ConvertSecurityDescriptorToStringSecurityDescriptorW
    convert.argtypes = [
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(wintypes.DWORD),
    ]
    convert.restype = wintypes.BOOL
    for path in (workspace, workspace / "administrator", workspace / "agent-a" / "identity.json"):
        needed = wintypes.DWORD()
        get(str(path), 4, None, 0, ctypes.byref(needed))
        descriptor = ctypes.create_string_buffer(needed.value)
        assert get(str(path), 4, descriptor, len(descriptor), ctypes.byref(needed))
        value = wintypes.LPWSTR()
        assert convert(descriptor, 1, 4, ctypes.byref(value), None)
        try:
            acl = value.value
            assert ";;;OW)" in acl and ";;;SY)" in acl
            assert ";;;BU)" not in acl and ";;;WD)" not in acl and ";;;AU)" not in acl
            if path.is_dir():
                assert acl.startswith("D:P")
        finally:
            free = ctypes.WinDLL("kernel32").LocalFree
            free.argtypes = [wintypes.LPVOID]
            free(value)


def test_bootstrap_codex_is_opt_in_and_reversible(paths):
    workspace, repo = paths
    result = onboarding.initialize(workspace, repo, configure_codex=True)
    assert result["codex"]["status"] == "configured"
    config = (repo / ".codex" / "config.toml").read_text()
    assert "agent-a" in config and "identity.json" not in config
    assert onboarding.setup_codex(workspace / "agent-a", repo, "uninstall")["changed"]
    assert not tree(repo)


@pytest.fixture
def invitation(paths):
    workspace, repo = paths
    onboarding.initialize(workspace, repo)
    return (
        workspace.parent / "joined home",
        workspace / "agent-a" / "identity.json",
        (workspace / "administrator" / "issuer.pub"),
    )


def joined_post(calls):
    def post(url, **kwargs):
        calls.append(url)
        assert kwargs["headers"]["Authorization"].startswith("Bearer ")
        return httpx.Response(
            200, json={"member_id": "agent-a"}, request=httpx.Request("POST", url)
        )

    return post


def test_join_preview_no_network_or_writes_and_apply_reuses(invitation, monkeypatch):
    home, keyfile, issuer = invitation
    calls = []
    monkeypatch.setattr(httpx, "post", joined_post(calls))
    before = tree(home.parent)
    assert onboarding.enroll(home, keyfile, issuer, dry_run=True)["status"] == "planned"
    assert not calls and tree(home.parent) == before
    result = onboarding.enroll(home, keyfile, issuer)
    assert result["status"] == "enrolled" and len(calls) == 1
    assert token(Client(home).keyfile) not in json.dumps(result)
    after = tree(home)
    assert onboarding.enroll(home, keyfile, issuer)["status"] == "locally_verified"
    assert onboarding.enroll(home, keyfile, issuer, dry_run=True)["reused"]
    assert len(calls) == 1 and tree(home) == after


def test_join_separately_trusted_issuer_required(invitation, paths, monkeypatch):
    home, keyfile, issuer = invitation
    other = paths[0].parent / "other"
    other.mkdir()
    from colony.security import generate_keys

    generate_keys(other)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("must verify before network"))
    with pytest.raises(ValueError, match="trusted issuer"):
        onboarding.enroll(home, keyfile, other / "issuer.pub")
    assert not home.exists()
    assert issuer.exists()


def test_join_offline_retry_does_not_publish_partial_home(invitation, monkeypatch):
    home, keyfile, issuer = invitation
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("secret"))
    )
    with pytest.raises(ValueError, match="remote membership") as error:
        onboarding.enroll(home, keyfile, issuer)
    assert "secret" not in str(error.value) and not home.exists()
    stage = onboarding._stage(home, "enrollment")
    assert stage.exists()
    calls = []
    monkeypatch.setattr(httpx, "post", joined_post(calls))
    assert onboarding.enroll(home, keyfile, issuer)["ok"]
    assert len(calls) == 1 and not stage.exists()


def test_join_https_remote_uses_existing_client(invitation, monkeypatch):
    from colony.security import issue

    home, keyfile, issuer = invitation
    key = Keyfile.model_validate_json(keyfile.read_text())
    remote = keyfile.parent.parent.parent / "remote invitation.json"
    grant = key.grant.model_copy(update={"server": "https://colony.example.test/colony"})
    remote.write_text(issue(issuer.with_name("issuer.pem"), grant).model_dump_json())
    calls = []
    monkeypatch.setattr(httpx, "post", joined_post(calls))
    result = onboarding.enroll(home, remote, issuer)
    assert result["ok"] and calls == ["https://colony.example.test/colony/join"]


@pytest.mark.parametrize("target,limit", [("keyfile", 65536), ("issuer", 16384)])
def test_join_oversized_inputs_fail_before_network_or_home_writes(
    invitation, monkeypatch, target, limit
):
    home, keyfile, issuer = invitation
    oversized = home.parent / ("oversized " + target)
    oversized.write_bytes(b"x" * (limit + 1))
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("oversized input network"))
    with pytest.raises(ValueError, match="valid keyfile and separately trusted issuer"):
        onboarding.enroll(
            home,
            oversized if target == "keyfile" else keyfile,
            oversized if target == "issuer" else issuer,
        )
    assert not home.exists() and not onboarding._stage(home, "enrollment").exists()


def test_partial_local_join_is_preserved_and_refused(invitation, monkeypatch):
    home, keyfile, issuer = invitation

    def partial(destination, keyfile, trusted):
        (destination / "issuer.pub").write_bytes(trusted.read_bytes())
        raise OSError("sensitive failure")

    monkeypatch.setattr(onboarding.Client, "join", partial)
    with pytest.raises(ValueError, match="remote membership"):
        onboarding.enroll(home, keyfile, issuer)
    stage = onboarding._stage(home, "enrollment")
    before = tree(stage)
    assert not home.exists()
    with pytest.raises(ValueError, match="conflicting or incomplete"):
        onboarding.enroll(home, keyfile, issuer)
    assert tree(stage) == before


def test_join_complete_stage_resumes_without_network(invitation, monkeypatch):
    home, keyfile, issuer = invitation
    calls = []
    monkeypatch.setattr(httpx, "post", joined_post(calls))
    original = onboarding.publish_directory
    monkeypatch.setattr(
        onboarding, "publish_directory", lambda *a: (_ for _ in ()).throw(OSError())
    )
    with pytest.raises(ValueError):
        onboarding.enroll(home, keyfile, issuer)
    assert len(calls) == 1 and not home.exists()
    monkeypatch.setattr(onboarding, "publish_directory", original)
    assert onboarding.enroll(home, keyfile, issuer)["ok"]
    assert len(calls) == 1


def test_conflicting_join_home_never_calls_server(invitation, monkeypatch):
    home, keyfile, issuer = invitation
    home.mkdir()
    (home / "identity.json").write_text("user content")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("conflict must be local"))
    with pytest.raises(ValueError, match="conflicting or incomplete"):
        onboarding.enroll(home, keyfile, issuer)
    assert (home / "identity.json").read_text() == "user content"


def test_setup_failure_preserves_successful_join_for_rerun(invitation, paths, monkeypatch):
    home, keyfile, issuer = invitation
    calls = []
    monkeypatch.setattr(httpx, "post", joined_post(calls))
    setup = onboarding.setup_codex
    monkeypatch.setattr(
        onboarding, "setup_codex", lambda *a, **k: (_ for _ in ()).throw(ValueError("secret"))
    )
    result = onboarding.enroll(
        home, keyfile, issuer, repo=paths[1], namespace="team", configure_codex=True
    )
    assert not result["ok"] and result["status"] == "enrolled"
    assert home.exists() and "secret" not in json.dumps(result)
    monkeypatch.setattr(onboarding, "setup_codex", setup)
    assert onboarding.enroll(
        home, keyfile, issuer, repo=paths[1], namespace="team", configure_codex=True
    )["ok"]
    assert len(calls) == 1


def test_reader_requires_read_only_when_configuring(paths, monkeypatch):
    workspace, repo = paths
    onboarding.initialize(workspace, repo)
    home, keyfile, issuer = (
        workspace.parent / "reader",
        workspace / "agent-b" / "identity.json",
        workspace / "administrator" / "issuer.pub",
    )
    calls = []
    monkeypatch.setattr(httpx, "post", joined_post(calls))
    with pytest.raises(ValueError, match="read_only"):
        onboarding.enroll(home, keyfile, issuer, repo=repo, namespace="team", configure_codex=True)
    result = onboarding.enroll(
        home, keyfile, issuer, repo=repo, namespace="team", read_only=True, configure_codex=True
    )
    assert result["ok"] and len(calls) == 1
    assert "--workflow-read-only" in (repo / ".codex" / "config.toml").read_text()
    assert Keyfile.model_validate_json((home / "identity.json").read_text()).grant.capture == "none"
