import json
import os
import stat
import subprocess
import sys
import time
import tomllib

import pytest

from colony import setup
from colony.models import Grant, Scope
from colony.security import generate_keys, issue


@pytest.fixture
def project(tmp_path):
    repo = tmp_path / "project with spaces"
    repo.mkdir()
    home = tmp_path / "private home"
    home.mkdir()
    administrator = tmp_path / "administrator"
    generate_keys(administrator)
    grant = Grant(
        member_id="agent-a",
        name="Agent A",
        server="http://127.0.0.1:7331",
        expires_at=int(time.time()) + 3600,
        scopes=[Scope(namespace="team", pull=True, push=True)],
    )
    (home / "identity.json").write_text(
        issue(administrator / "issuer.pem", grant).model_dump_json()
    )
    (home / "issuer.pub").write_bytes((administrator / "issuer.pub").read_bytes())
    return repo, home


def install(project, **options):
    repo, home = project
    return setup.setup_codex(home, repo, "install", namespace="team", **options)


def tree(repo):
    return {p.relative_to(repo).as_posix(): p.read_bytes() for p in repo.rglob("*") if p.is_file()}


def test_preview_install_repeat_remove_without_home(project):
    repo, home = project
    preview = install(project, dry_run=True)
    assert preview["changed"] and not tree(repo)
    install(project)
    configured = tree(repo)
    config = tomllib.loads((repo / ".codex/config.toml").read_text())
    server = config["mcp_servers"]["colony"]
    assert server["command"] == sys.executable
    assert server["cwd"] == str(repo)
    assert server["args"] == [
        "-I",
        "-m",
        "colony.cli",
        "--home",
        str(home),
        "mcp",
        "--workflow-namespace",
        "team",
        "--workflow-repo",
        str(repo),
    ]
    assert not install(project)["changed"]
    assert tree(repo) == configured
    assert setup.setup_codex(repo / "missing", repo, "status")["installed"]
    assert tree(repo) == configured
    setup.setup_codex(repo / "missing", repo, "uninstall", dry_run=True)
    assert tree(repo) == configured
    setup.setup_codex(repo / "missing", repo, "uninstall")
    assert not tree(repo)
    assert not setup.setup_codex(home, repo, "uninstall")["changed"]


def test_unicode_project_paths_are_valid_toml(project):
    repo, home = project
    unicode_repo = repo.parent / "Project \U0001f41c"
    unicode_repo.mkdir()
    install((unicode_repo, home))
    config = tomllib.loads((unicode_repo / ".codex/config.toml").read_text(encoding="utf-8"))
    assert config["mcp_servers"]["colony"]["cwd"] == str(unicode_repo)


@pytest.mark.parametrize("ending", [b"", b"\n", b"\r\n"])
def test_preserves_existing_bytes_comments_and_mode(project, ending):
    repo, home = project
    (repo / ".codex").mkdir()
    config = repo / ".codex/config.toml"
    original_config = b'# user comment\n[mcp_servers.other]\ncommand = "other"' + ending
    original_agents = b"# Existing instructions" + ending
    config.write_bytes(original_config)
    (repo / "AGENTS.md").write_bytes(original_agents)
    if os.name != "nt":
        config.chmod(0o640)
    install(project)
    if os.name != "nt":
        assert stat.S_IMODE(config.stat().st_mode) == 0o640
    setup.setup_codex(home, repo, "uninstall")
    assert config.read_bytes() == original_config
    assert (repo / "AGENTS.md").read_bytes() == original_agents


def test_preserves_preexisting_empty_files(project):
    repo, home = project
    (repo / ".codex").mkdir()
    (repo / ".codex/config.toml").touch()
    (repo / "AGENTS.md").touch()
    install(project)
    setup.setup_codex(home, repo, "uninstall")
    assert tree(repo) == {".codex/config.toml": b"", "AGENTS.md": b""}


def test_root_override_and_later_shadowing(project):
    repo, home = project
    (repo / "AGENTS.override.md").write_text("# Override\n")
    install(project)
    assert not (repo / "AGENTS.md").exists()
    setup.setup_codex(home, repo, "uninstall")
    (repo / "AGENTS.override.md").unlink()
    install(project)
    (repo / "AGENTS.override.md").write_text("# New override\n")
    assert setup.setup_codex(home, repo, "status")["shadowed"]
    before = tree(repo)
    with pytest.raises(ValueError, match="shadowed"):
        install(project)
    assert tree(repo) == before
    setup.setup_codex(home, repo, "uninstall")
    assert (repo / "AGENTS.override.md").read_text() == "# New override\n"


@pytest.mark.parametrize(
    "content",
    [
        b"not valid TOML !",
        b'[mcp_servers.colony]\ncommand="mine"',
        b"# colony:setup:v1 begin broken\n",
    ],
)
def test_conflicts_preflight_without_changes(project, content):
    repo, _ = project
    (repo / ".codex").mkdir()
    (repo / ".codex/config.toml").write_bytes(content)
    before = tree(repo)
    with pytest.raises(ValueError):
        install(project)
    assert tree(repo) == before


@pytest.mark.parametrize(
    "extension",
    [
        '\nenv = { COLONY_ONLY = "private-extension-sentinel" }\n',
        "\nstartup_timeout_sec = 30\n",
        '\n[mcp_servers.colony.env]\nCOLONY_ONLY = "private-extension-sentinel"\n',
    ],
)
def test_external_colony_settings_refused_without_reparenting_or_secret_echo(project, extension):
    repo, home = project
    config = repo / ".codex/config.toml"
    config.parent.mkdir()
    config.write_text('[mcp_servers.other]\ncommand = "other-server"\n')
    install(project)
    with config.open("a", encoding="utf-8") as stream:
        stream.write(extension)
    before = tree(repo)
    for action in ("status", "install", "uninstall"):
        with pytest.raises(ValueError, match="outside the managed block") as error:
            setup.setup_codex(home, repo, action, namespace="team")
        assert "private-extension-sentinel" not in str(error.value)
        assert tree(repo) == before
    servers = tomllib.loads(config.read_text(encoding="utf-8"))["mcp_servers"]
    assert "env" not in servers["other"]


@pytest.mark.parametrize("with_nan", [False, True])
def test_unrelated_toml_additions_keep_their_semantic_owners(project, with_nan):
    repo, home = project
    config = repo / ".codex/config.toml"
    config.parent.mkdir()
    original = '[mcp_servers.other]\ncommand = "other-server"\n'
    if with_nan:
        original += "custom_value = nan\n"
    config.write_text(original)
    install(project)
    extension = '\n[mcp_servers.third]\ncommand = "third-server"\nenv = { THIRD = "value" }\n'
    with config.open("a", encoding="utf-8") as stream:
        stream.write(extension)
    assert not install(project)["changed"]
    setup.setup_codex(home, repo, "uninstall")
    assert config.read_text() == original + extension
    servers = tomllib.loads(config.read_text())["mcp_servers"]
    assert "colony" not in servers and servers["third"]["env"] == {"THIRD": "value"}


def test_valid_config_with_external_extension_reports_boundary_not_parse_error(project):
    repo, home = project
    config = repo / ".codex/config.toml"
    config.parent.mkdir()
    config.write_text('[mcp_servers.other]\ncommand = "other"\nenv = { OTHER = "value" }\n')
    install(project)
    with config.open("a", encoding="utf-8") as stream:
        stream.write('\nenv = { COLONY_ONLY = "private-extension-sentinel" }\n')
    assert "colony" in tomllib.loads(config.read_text())["mcp_servers"]
    before = tree(repo)
    with pytest.raises(ValueError, match="outside the managed block"):
        setup.setup_codex(home, repo, "uninstall")
    assert tree(repo) == before


@pytest.mark.parametrize("shadow_kind", ["module", "package"])
def test_generated_command_ignores_project_and_pythonpath_shadowing(project, tmp_path, shadow_kind):
    repo, _ = project
    marker = repo / "unexpected-project-code"
    code = f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
    if shadow_kind == "module":
        (repo / "colony.py").write_text(code)
    else:
        package = repo / "colony"
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "cli.py").write_text(code)
    injected = tmp_path / "pythonpath"
    injected.mkdir()
    (injected / "colony.py").write_text(code)
    install(project)
    server = tomllib.loads((repo / ".codex/config.toml").read_text())["mcp_servers"]["colony"]
    result = subprocess.run(
        [server["command"], *server["args"], "--help"],
        cwd=server["cwd"],
        env={**os.environ, "PYTHONPATH": str(injected)},
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0 and "--workflow-namespace" in result.stdout
    assert not marker.exists()


def test_modified_owned_block_refused_but_outside_edits_retained(project):
    repo, home = project
    install(project)
    agents = repo / "AGENTS.md"
    agents.write_bytes(agents.read_bytes() + b"\nUser addition\n")
    setup.setup_codex(home, repo, "uninstall")
    assert agents.read_bytes() == b"\nUser addition\n"
    install(project)
    agents.write_bytes(agents.read_bytes().replace(b"Colony workflow", b"Edited workflow"))
    before = tree(repo)
    for action in ("install", "uninstall", "status"):
        with pytest.raises(ValueError, match="edited"):
            setup.setup_codex(home, repo, action, namespace="team")
        assert tree(repo) == before


def test_permissions_read_only_and_missing_identity(project, monkeypatch):
    repo, home = project

    class PullOnly:
        def __init__(self, home):
            self.keyfile = type(
                "Identity",
                (),
                {
                    "grant": Grant(
                        member_id="puller",
                        name="Puller",
                        server="http://127.0.0.1:7331",
                        expires_at=int(time.time()) + 100,
                        scopes=[Scope(namespace="team", pull=True)],
                    )
                },
            )()

    monkeypatch.setattr(setup, "Client", PullOnly)
    with pytest.raises(ValueError, match="permissions"):
        install(project)
    install(project, read_only=True)
    assert "--workflow-read-only" in (repo / ".codex/config.toml").read_text()
    setup.setup_codex(home, repo, "uninstall")
    monkeypatch.undo()
    with pytest.raises(ValueError, match="identity"):
        setup.setup_codex(repo / "missing", repo, "install", namespace="team")
    assert not tree(repo)


def test_no_credentials_or_existing_config_in_diagnostics(project):
    repo, _ = project
    (repo / ".codex").mkdir()
    config = repo / ".codex/config.toml"
    sentinel = "secret-value-must-never-appear"
    config.write_text(f'password = "{sentinel}"\n')
    result = install(project)
    assert sentinel not in json.dumps(result)
    assert sentinel not in (repo / "AGENTS.md").read_text()
    assert config.read_text().count(sentinel) == 1
    config.write_text(f'password = "{sentinel}"\ninvalid !')
    with pytest.raises(ValueError) as caught:
        install(project)
    assert sentinel not in str(caught.value)


def test_expired_identity_does_not_trap_status_or_removal(project, monkeypatch):
    repo, home = project
    install(project)
    monkeypatch.setattr("colony.security.time.time", lambda: 10**12)
    with pytest.raises(ValueError, match="identity"):
        install(project)
    assert setup.setup_codex(home, repo, "status")["installed"]
    setup.setup_codex(home, repo, "uninstall")
    assert not tree(repo)


def test_marker_metadata_edits_are_detected(project):
    repo, home = project
    install(project)
    path = repo / "AGENTS.md"
    path.write_bytes(path.read_bytes().replace(b"created=1", b"created=0"))
    with pytest.raises(ValueError, match="edited"):
        setup.setup_codex(home, repo, "uninstall")


def test_atomic_failure_rolls_back_without_overwriting_concurrent_edit(project, monkeypatch):
    repo, _ = project
    original = setup._atomic
    calls = 0

    def fail_second(path, content, mode, expected):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated failure")
        original(path, content, mode, expected)

    monkeypatch.setattr(setup, "_atomic", fail_second)
    with pytest.raises(ValueError, match="rolled back"):
        install(project)
    assert not tree(repo)
    calls = 0

    def concurrent_edit(path, content, mode, expected):
        nonlocal calls
        calls += 1
        if calls == 2:
            (repo / ".codex/config.toml").write_bytes(b"# concurrent edit\n")
            raise OSError("simulated failure")
        original(path, content, mode, expected)

    monkeypatch.setattr(setup, "_atomic", concurrent_edit)
    with pytest.raises(ValueError, match="rollback incomplete"):
        install(project)
    assert (repo / ".codex/config.toml").read_bytes() == b"# concurrent edit\n"


def test_uninstall_failure_restores_removed_files(project, monkeypatch):
    repo, home = project
    install(project)
    before = tree(repo)
    original = setup._recovery_path
    calls = 0

    def fail_second(path):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("Cannot stage second deletion")
        return original(path)

    monkeypatch.setattr(setup, "_recovery_path", fail_second)
    with pytest.raises(ValueError, match="rolled back"):
        setup.setup_codex(home, repo, "uninstall")
    assert tree(repo) == before


@pytest.mark.parametrize("existing", [False, True])
def test_concurrent_update_or_creation_during_staging_is_preserved(project, monkeypatch, existing):
    repo, _ = project
    (repo / ".codex").mkdir()
    config = repo / ".codex/config.toml"
    if existing:
        config.write_bytes(b"# original\n")
    original = setup.os.fsync

    def concurrent_write(fd):
        original(fd)
        config.write_bytes(b"# concurrent write during staging\n")

    monkeypatch.setattr(setup.os, "fsync", concurrent_write)
    with pytest.raises(ValueError, match="rolled back"):
        install(project)
    assert tree(repo) == {".codex/config.toml": b"# concurrent write during staging\n"}


def test_new_file_creation_between_final_check_and_publication_is_preserved(project, monkeypatch):
    repo, _ = project
    config = repo / ".codex/config.toml"
    original = setup.os.link

    def concurrent_create(source, destination):
        if destination == config:
            config.write_bytes(b"# concurrently created\n")
        return original(source, destination)

    monkeypatch.setattr(setup.os, "link", concurrent_create)
    with pytest.raises(ValueError, match="rolled back"):
        install(project)
    assert tree(repo) == {".codex/config.toml": b"# concurrently created\n"}


def test_new_file_publication_fails_safe_without_hardlink_support(project, monkeypatch):
    repo, _ = project

    def unavailable(*args):
        raise OSError("Hardlinks unsupported")

    monkeypatch.setattr(setup.os, "link", unavailable)
    with pytest.raises(ValueError, match="rolled back"):
        install(project)
    assert not tree(repo)


@pytest.mark.parametrize("timing", ["before_move", "before_cleanup"])
def test_uninstall_preserves_concurrent_edit_to_removed_file(project, monkeypatch, timing):
    repo, home = project
    install(project)
    config = repo / ".codex/config.toml"
    agents_before = (repo / "AGENTS.md").read_bytes()
    changed = b"# concurrent uninstall edit\n"
    if timing == "before_move":
        original = setup.os.replace

        def replace(source, destination):
            if source == config:
                config.write_bytes(changed)
            return original(source, destination)

        monkeypatch.setattr(setup.os, "replace", replace)
    else:
        original = setup._recovery_path

        def next_recovery(path):
            if path == repo / "AGENTS.md":
                recovery = next(config.parent.glob(".colony-recovery-*"))
                recovery.write_bytes(changed)
            return original(path)

        monkeypatch.setattr(setup, "_recovery_path", next_recovery)
    with pytest.raises(ValueError, match="rolled back"):
        setup.setup_codex(home, repo, "uninstall")
    assert config.read_bytes() == changed
    assert (repo / "AGENTS.md").read_bytes() == agents_before
    assert not list(repo.rglob(".colony-recovery-*"))


def test_symlink_directory_refused(project, tmp_path):
    repo, _ = project
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (repo / ".codex").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symbolic-link creation not available")
    with pytest.raises(ValueError, match="links"):
        install(project)
    assert not list(outside.iterdir())


@pytest.mark.parametrize("failure", ["unchanged", "moved", "concurrent"])
def test_windows_replacement_failure_keeps_original_or_recovery(project, monkeypatch, failure):
    repo, _ = project
    (repo / ".codex").mkdir()
    config = repo / ".codex/config.toml"
    original = b"# private original\n"
    config.write_bytes(original)
    monkeypatch.setattr(setup, "_WINDOWS", True)
    monkeypatch.setattr(setup, "_windows_dacl", lambda source, destination: None)

    def failed_replace(path, replacement, backup):
        if failure != "unchanged":
            os.replace(path, backup)
        if failure == "concurrent":
            path.write_bytes(b"# concurrent edit\n")
        raise OSError("Native replacement failed")

    monkeypatch.setattr(setup, "_win_replace_primitive", failed_replace)
    with pytest.raises(
        ValueError, match="incomplete" if failure == "concurrent" else "rolled back"
    ):
        install(project)
    assert config.read_bytes() == (b"# concurrent edit\n" if failure == "concurrent" else original)
    recoveries = list(config.parent.glob(".colony-recovery-*"))
    if failure == "concurrent":
        assert len(recoveries) == 1 and recoveries[0].read_bytes() == original
    else:
        assert not recoveries
    assert not list(config.parent.glob(".colony-setup-*"))
    assert not (repo / "AGENTS.md").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows DACL preservation")
def test_windows_staging_is_protected_before_content(project, monkeypatch):
    repo, _ = project
    (repo / ".codex").mkdir()
    (repo / ".codex/config.toml").write_bytes(b"# existing private bytes\n")
    native = setup._windows_dacl
    checked = []

    def protect(source, destination):
        assert destination.read_bytes() == b""
        native(source, destination)
        checked.append(source)

    monkeypatch.setattr(setup, "_windows_dacl", protect)
    install(project)
    assert checked == [repo / ".codex/config.toml"]


@pytest.mark.skipif(os.name != "nt", reason="Windows custom DACL preservation")
def test_windows_custom_protected_dacl_survives_install_and_uninstall(project):
    import ctypes
    from ctypes import wintypes

    repo, home = project
    (repo / ".codex").mkdir()
    config = repo / ".codex/config.toml"
    config.write_bytes(b"# private configuration\n")
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    convert = api.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.DWORD),
    ]
    convert.restype = wintypes.BOOL
    descriptor = wintypes.LPVOID()
    # Owner Rights: only the current file owner receives full access; no inherited grants.
    assert convert("D:P(A;;FA;;;OW)", 1, ctypes.byref(descriptor), None)
    put = api.SetFileSecurityW
    put.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID]
    put.restype = wintypes.BOOL
    free = ctypes.WinDLL("kernel32").LocalFree
    free.argtypes = [wintypes.LPVOID]
    free.restype = wintypes.LPVOID
    try:
        assert put(str(config), 4 | 0x80000000, descriptor)
    finally:
        free(descriptor)

    def descriptor_bytes():
        get = api.GetFileSecurityW
        get.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        get.restype = wintypes.BOOL
        needed = wintypes.DWORD()
        get(str(config), 4, None, 0, ctypes.byref(needed))
        buffer = ctypes.create_string_buffer(needed.value)
        assert get(str(config), 4, buffer, len(buffer), ctypes.byref(needed))
        get_dacl = api.GetSecurityDescriptorDacl
        get_dacl.argtypes = [
            wintypes.LPVOID,
            ctypes.POINTER(wintypes.BOOL),
            ctypes.POINTER(wintypes.LPVOID),
            ctypes.POINTER(wintypes.BOOL),
        ]
        get_dacl.restype = wintypes.BOOL
        present, defaulted, dacl = wintypes.BOOL(), wintypes.BOOL(), wintypes.LPVOID()
        assert get_dacl(buffer, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted))
        assert present.value and dacl.value
        length = ctypes.c_ushort.from_address(dacl.value + 2).value
        # Windows may normalize auto-inheritance bookkeeping while preserving protected ACLs.
        protected = bool(int.from_bytes(buffer.raw[2:4], "little") & 0x1000)
        return protected, ctypes.string_at(dacl, length)

    original = descriptor_bytes()
    install(project)
    assert descriptor_bytes() == original
    setup.setup_codex(home, repo, "uninstall")
    assert descriptor_bytes() == original
    assert config.read_bytes() == b"# private configuration\n"
