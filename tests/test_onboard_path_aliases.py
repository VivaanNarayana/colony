import os
from pathlib import Path

import httpx
import pytest

from colony import onboarding

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Native Windows path aliases")


def windows_alias(path, kind):
    if kind == "extended":
        text = str(path)
        return Path("\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text)

    import ctypes
    from ctypes import wintypes

    get_short = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
    get_short.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    get_short.restype = wintypes.DWORD
    needed = get_short(str(path), None, 0)
    if not needed:
        pytest.skip("Windows short path names unavailable")
    buffer = ctypes.create_unicode_buffer(needed)
    written = get_short(str(path), buffer, needed)
    assert 0 < written < needed
    alias = Path(buffer.value)
    if alias == path:
        pytest.skip("No distinct 8.3 alias on this volume")
    assert alias.samefile(path)
    return alias


@pytest.mark.parametrize("kind", ["extended", "short"])
@pytest.mark.parametrize("relation", ["inside", "same", "ancestor"])
@pytest.mark.parametrize("alias_side", ["home", "repo"])
@pytest.mark.parametrize("dry_run", [True, False], ids=["preview", "apply"])
@pytest.mark.parametrize("operation", ["bootstrap", "enrollment"])
def test_windows_alias_overlap_rejected_before_writes_or_enrollment(
    tmp_path, monkeypatch, kind, relation, alias_side, dry_run, operation
):
    parent = tmp_path / "long project parent"
    repo = parent / "evidence repository"
    repo.mkdir(parents=True)
    (repo / "source.txt").write_bytes(b"preserve project")
    alias = windows_alias(repo if relation != "ancestor" else parent, kind)
    if relation == "inside":
        home = (alias if alias_side == "home" else repo) / "credentials"
    elif relation == "same":
        home = alias if alias_side == "home" else repo
    else:
        home = alias if alias_side == "home" else parent
    if alias_side == "repo":
        repo = alias if relation != "ancestor" else alias / "evidence repository"

    before = {
        p.relative_to(tmp_path): p.read_bytes() if p.is_file() else None
        for p in tmp_path.rglob("*")
    }
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("overlap must be local"))
    with pytest.raises(ValueError, match="credentials must be outside the project"):
        if operation == "bootstrap":
            onboarding.initialize(home, repo, dry_run=dry_run)
        else:
            # Reject the path before even trying to read invitation credentials.
            onboarding.enroll(
                home,
                tmp_path / "missing key",
                tmp_path / "missing issuer",
                repo=repo,
                dry_run=dry_run,
            )
    after = {
        p.relative_to(tmp_path): p.read_bytes() if p.is_file() else None
        for p in tmp_path.rglob("*")
    }
    assert after == before


@pytest.mark.parametrize("kind", ["extended", "short"])
@pytest.mark.parametrize("alias_side", ["home", "repo"])
def test_windows_alias_disjoint_workspace_remains_usable(tmp_path, kind, alias_side):
    repo = tmp_path / "evidence repository"
    parent = tmp_path / "private workspace parent"
    repo.mkdir()
    parent.mkdir()
    if alias_side == "home":
        parent = windows_alias(parent, kind)
    else:
        repo = windows_alias(repo, kind)
    workspace = parent / "credentials"
    assert onboarding.initialize(workspace, repo, dry_run=True)["ok"]
    assert not workspace.exists()
    assert onboarding.initialize(workspace, repo)["ok"]
    assert onboarding.initialize(workspace, repo)["reused"]
    assert (workspace / "administrator" / "issuer.pem").is_file()
    assert len(list(workspace.glob("agent-*/identity.json"))) == 2
    assert not list(repo.iterdir())
