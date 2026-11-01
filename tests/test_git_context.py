import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from colony import git_context
from colony.models import GitContext


def git(directory, *arguments, check=True):
    environment = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return subprocess.run(
        [
            "git",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            *map(str, arguments),
        ],
        cwd=directory,
        env=environment,
        capture_output=True,
        check=check,
        timeout=10,
    )


@pytest.fixture
def repository(tmp_path):
    path = tmp_path / "repository with spaces"
    path.mkdir()
    git(path, "init", "-q", "--initial-branch=main")
    (path / "fact.txt").write_text("TIMEOUT = 30\n")
    git(path, "add", "fact.txt")
    git(path, "commit", "-qm", "fixture")
    return path


def test_native_clean_dirty_and_untracked(repository):
    clean = git_context.capture(repository, "team-project")
    assert clean.state == "git" and clean.branch == "main"
    assert clean.commit == git(repository, "rev-parse", "HEAD").stdout.decode().strip()
    assert clean.detached is False and clean.dirty is False
    (repository / "fact.txt").write_text("TIMEOUT = 60\n")
    assert git_context.capture(repository, "team-project").dirty is True
    git(repository, "add", "fact.txt")
    git(repository, "commit", "-qm", "change")
    (repository / "untracked.txt").write_text("new\n")
    assert git_context.capture(repository, "team-project").dirty is True


def test_same_commit_unchanged_files_on_another_branch_are_not_matched(repository):
    before = git_context.capture(repository, "project")
    contents = (repository / "fact.txt").read_bytes()
    git(repository, "switch", "-qc", "different-branch")
    after = git_context.capture(repository, "project")
    assert before.commit == after.commit and (repository / "fact.txt").read_bytes() == contents
    assert before.dirty is after.dirty is False
    assert git_context.compare(before, after) == "branch_mismatch"


def test_credential_shaped_valid_git_branch_is_not_published(repository):
    secret_branch = "password=synthetic-sentinel"
    git(repository, "switch", "-qc", secret_branch)
    result = git_context.capture(repository, "project")
    assert result.state == "unavailable" and result.branch is None
    assert "synthetic-sentinel" not in result.model_dump_json()


def test_detached_head_and_linked_worktree(repository, tmp_path):
    git(repository, "switch", "--detach", "HEAD")
    detached = git_context.capture(repository, "project")
    assert detached.state == "git" and detached.detached is True and detached.branch is None
    linked = tmp_path / "linked worktree"
    git(repository, "worktree", "add", "-qb", "linked", linked)
    context = git_context.capture(linked, "project")
    assert context.state == "git" and context.branch == "linked" and context.dirty is False
    assert context.commit == detached.commit
    assert git_context.compare(detached, context) == "branch_mismatch"


def test_shallow_local_clone(repository, tmp_path):
    shallow = tmp_path / "shallow"
    git(tmp_path, "clone", "-q", "--depth=1", repository.as_uri(), shallow)
    assert git(shallow, "rev-parse", "--is-shallow-repository").stdout.strip() == b"true"
    context = git_context.capture(shallow, "project")
    assert context.state == "git" and context.dirty is False
    assert repository.as_uri() not in context.model_dump_json()


def test_missing_git_non_git_unborn_and_invalid_paths(tmp_path, monkeypatch):
    assert git_context.capture(tmp_path, "project").state == "non_git"
    unborn = tmp_path / "unborn"
    unborn.mkdir()
    git(unborn, "init", "-q")
    assert git_context.capture(unborn, "project").state == "unavailable"
    assert git_context.capture(tmp_path / "missing", "project").state == "unavailable"
    monkeypatch.setattr(shutil, "which", lambda *args: None)
    assert git_context.capture(tmp_path, "project").state == "unavailable"


def test_bare_repository_is_not_misrepresented_as_non_git(tmp_path):
    git(tmp_path, "init", "--bare", "-q", "bare")
    assert git_context.capture(tmp_path / "bare", "project").state == "unavailable"


def test_sha256_repository_when_supported(tmp_path):
    result = git(tmp_path, "init", "-q", "--object-format=sha256", "sha256", check=False)
    if result.returncode:
        pytest.skip("Installed Git does not support SHA-256 repositories")
    repo = tmp_path / "sha256"
    git(repo, "commit", "--allow-empty", "-qm", "fixture")
    context = git_context.capture(repo, "project")
    assert context.state == "git" and len(context.commit) == 64


def test_inherited_git_overrides_are_cleared(repository, tmp_path, monkeypatch):
    hostile = {
        "GIT_DIR": str(tmp_path / "wrong"),
        "GIT_WORK_TREE": str(tmp_path),
        "GIT_COMMON_DIR": str(tmp_path / "wrong"),
        "GIT_INDEX_FILE": str(tmp_path / "unintended-index"),
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.fsmonitor",
        "GIT_CONFIG_VALUE_0": "untrusted-command",
        "GIT_CONFIG_PARAMETERS": "invalid",
        "GIT_TRACE": str(tmp_path / "unintended-trace"),
        "GIT_CONFIG_GLOBAL": str(tmp_path / "untrusted-global"),
    }
    for key, value in hostile.items():
        monkeypatch.setenv(key, value)
    context = git_context.capture(repository, "project")
    assert context.state == "git" and context.branch == "main" and context.dirty is False
    assert not (tmp_path / "unintended-index").exists()
    assert not (tmp_path / "unintended-trace").exists()


def test_fsmonitor_and_filters_never_execute_or_refresh_index(repository, tmp_path):
    marker = tmp_path / "executed"
    callback = tmp_path / "callback.py"
    callback.write_text(
        "import pathlib,sys\npathlib.Path(" + repr(str(marker)) + ").write_text('executed')\n"
        "sys.stdout.write(sys.stdin.read())\n"
    )
    command = shlex.join([Path(sys.executable).as_posix(), callback.as_posix()])
    (repository / ".gitattributes").write_text("*.txt filter=tripwire\n")
    git(repository, "add", ".gitattributes")
    git(repository, "commit", "-qm", "attributes")
    git(repository, "config", "core.fsmonitor", command)
    git(repository, "config", "filter.tripwire.clean", command)
    git(repository, "config", "filter.tripwire.process", command)
    git(repository, "config", "filter.tripwire.required", "true")
    git(repository, "config", "remote.origin.url", "https://user:private@invalid.example/repo")
    (repository / "fact.txt").write_text("changed\n")
    index = repository / ".git" / "index"
    before = index.read_bytes(), index.stat().st_mtime_ns
    result = git_context.capture(repository, "project")
    assert result.state == "git" and result.dirty is True
    assert not marker.exists()
    assert (index.read_bytes(), index.stat().st_mtime_ns) == before
    rendered = result.model_dump_json()
    assert "private" not in rendered and str(repository) not in rendered


def test_gitlinks_fail_closed_without_recursing(repository):
    commit = git(repository, "rev-parse", "HEAD").stdout.decode().strip()
    git(repository, "update-index", "--add", "--cacheinfo", "160000," + commit + ",submodule")
    assert git_context.capture(repository, "project").state == "unavailable"


@pytest.mark.parametrize("scope", ["include", "worktree"])
def test_active_included_and_worktree_filter_keys_are_disabled(repository, tmp_path, scope):
    marker = tmp_path / "executed"
    callback = tmp_path / "included_callback.py"
    callback.write_text(
        "import pathlib,sys\npathlib.Path(" + repr(str(marker)) + ").write_text('executed')\n"
        "sys.stdout.write(sys.stdin.read())\n"
    )
    command = shlex.join([Path(sys.executable).as_posix(), callback.as_posix()])
    (repository / ".gitattributes").write_text("*.txt filter=included\n")
    git(repository, "add", ".gitattributes")
    git(repository, "commit", "-qm", "attributes")
    if scope == "include":
        config = tmp_path / "included.config"
        git(tmp_path, "config", "--file", config, "filter.included.clean", command)
        git(repository, "config", "include.path", config)
    else:
        git(repository, "config", "extensions.worktreeConfig", "true")
        git(repository, "config", "--worktree", "filter.included.clean", command)
    (repository / "fact.txt").write_text("changed\n")
    context = git_context.capture(repository, "project")
    assert context.state == "git" and context.dirty is True
    assert not marker.exists()


def test_global_config_preserves_builtin_crlf_and_excludes_without_callbacks(
    repository, tmp_path, monkeypatch
):
    home = tmp_path / "isolated user home"
    home.mkdir()
    xdg = home / "xdg"
    xdg.mkdir()
    marker = tmp_path / "global-callback-executed"
    callback = tmp_path / "global_callback.py"
    callback.write_text(
        "import pathlib,sys\npathlib.Path(" + repr(str(marker)) + ").write_text('executed')\n"
        "sys.stdout.write(sys.stdin.read())\n"
    )
    command = shlex.join([Path(sys.executable).as_posix(), callback.as_posix()])
    excludes = home / "excluded"
    excludes.write_text("ignored-globally.txt\n")
    (repository / ".gitattributes").write_text("*.txt filter=global-tripwire\n")
    git(repository, "add", ".gitattributes")
    git(repository, "commit", "-qm", "attributes")
    config = home / ".gitconfig"
    for key, value in [
        ("core.autocrlf", "true"),
        ("core.excludesFile", str(excludes)),
        ("core.fsmonitor", command),
        ("filter.global-tripwire.clean", command),
        ("filter.global-tripwire.process", command),
        ("filter.global-tripwire.required", "true"),
    ]:
        git(tmp_path, "config", "--file", config, key, value)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    (repository / "fact.txt").write_bytes(b"TIMEOUT = 30\r\n")
    (repository / "ignored-globally.txt").write_text("ignored\n")
    index = repository / ".git/index"
    before = index.read_bytes(), index.stat().st_mtime_ns
    context = git_context.capture(repository, "project")
    assert context.state == "git" and context.dirty is False
    assert not marker.exists()
    assert (index.read_bytes(), index.stat().st_mtime_ns) == before


@pytest.mark.parametrize("bound", ["keys", "bytes"])
def test_large_filter_configuration_fails_closed_before_status(repository, monkeypatch, bound):
    with (repository / ".git/config").open("a") as config:
        for number in range(33):
            config.write(f'\n[filter "fixture-{number}"]\n\tclean = private-value-sentinel\n')
    if bound == "bytes":
        monkeypatch.setattr(git_context, "_OUTPUT_LIMIT", 128)
    original = git_context._Git.checked

    def checked(probe, *arguments):
        if arguments[0] == "status":
            pytest.fail("Unbounded filter configuration reached status")
        return original(probe, *arguments)

    monkeypatch.setattr(git_context._Git, "checked", checked)
    result = git_context.capture(repository, "project")
    assert result.state == "unavailable"
    assert "private-value-sentinel" not in result.model_dump_json()


@pytest.mark.parametrize("change", ["branch", "commit"])
def test_snapshot_rechecks_commit_and_branch(repository, monkeypatch, change):
    original = git_context._Git.head
    calls = 0

    def head(probe):
        nonlocal calls
        calls += 1
        if calls == 2:
            if change == "branch":
                git(repository, "switch", "-qc", "changed-during-snapshot")
            else:
                git(repository, "commit", "--allow-empty", "-qm", "changed-during-snapshot")
        return original(probe)

    monkeypatch.setattr(git_context._Git, "head", head)
    assert git_context.capture(repository, "project").state == "unavailable"


@pytest.mark.parametrize("behavior", ["timeout", "output"])
def test_total_deadline_and_output_cap_fail_closed(tmp_path, monkeypatch, behavior):
    real_popen = subprocess.Popen
    script = "import time;time.sleep(5)" if behavior == "timeout" else "print('x'*100000)"

    def fake_git(command, **options):
        return real_popen([sys.executable, "-c", script], **options)

    monkeypatch.setattr(git_context.subprocess, "Popen", fake_git)
    monkeypatch.setattr(git_context, "_TOTAL_TIMEOUT", 0.5)
    monkeypatch.setattr(git_context, "_OUTPUT_LIMIT", 256)
    started = time.monotonic()
    context = git_context.capture(tmp_path, "project")
    assert time.monotonic() - started < 1.5
    assert context.state == "unavailable"
    assert "xxxxx" not in context.model_dump_json()


def test_budget_is_shared_between_commands(tmp_path, monkeypatch):
    real_popen = subprocess.Popen

    def fake_git(command, **options):
        return real_popen([sys.executable, "-c", "print('x'*80,end='')"], **options)

    monkeypatch.setattr(git_context.subprocess, "Popen", fake_git)
    monkeypatch.setattr(git_context, "_OUTPUT_LIMIT", 100)
    probe = git_context._Git(tmp_path)
    assert len(probe.run("first")[1]) == 80
    with pytest.raises(git_context._Unavailable):
        probe.run("second")


def test_total_time_budget_is_not_restarted_for_each_command(tmp_path, monkeypatch):
    real_popen = subprocess.Popen

    def fake_git(command, **options):
        return real_popen(
            [sys.executable, "-c", "import time;time.sleep(0.25);print('ok')"], **options
        )

    monkeypatch.setattr(git_context.subprocess, "Popen", fake_git)
    monkeypatch.setattr(git_context, "_TOTAL_TIMEOUT", 0.65)
    probe = git_context._Git(tmp_path)
    started = time.monotonic()
    assert probe.run("first")[1].strip() == b"ok"
    with pytest.raises(git_context._Unavailable):
        probe.run("second")
    assert time.monotonic() - started < 1.2


def test_invalid_identifier_has_fixed_error_without_echoing_input(tmp_path):
    with pytest.raises(ValueError, match="^A valid repository identifier is required.$"):
        git_context.capture(tmp_path, "https://password=private.invalid")


def test_credential_shaped_repository_identifier_is_rejected_without_echo(tmp_path):
    with pytest.raises(ValueError, match="^A valid repository identifier is required.$") as error:
        git_context.capture(tmp_path, "ghp_" + "a" * 24)
    assert "ghp_" not in str(error.value)


def context(**changes):
    return GitContext(
        repository_id="project",
        state="git",
        commit="a" * 40,
        branch="main",
        detached=False,
        dirty=False,
    ).model_copy(update=changes)


@pytest.mark.parametrize(
    "saved,requested,expected",
    [
        (None, context(), "legacy"),
        (context(), context(), "matched"),
        (context(), context(repository_id="other"), "repository_mismatch"),
        (context(), context(branch="another"), "branch_mismatch"),
        (context(), context(branch=None, detached=True), "branch_mismatch"),
        (context(), context(commit="b" * 40), "commit_mismatch"),
        (context(dirty=True), context(dirty=True), "dirty"),
        (context(), context(dirty=True), "dirty"),
        (context(), GitContext(repository_id="project", state="unavailable"), "unavailable"),
        (context(), GitContext(repository_id="project", state="non_git"), "unavailable"),
        (
            GitContext(repository_id="project", state="non_git"),
            GitContext(repository_id="project", state="non_git"),
            "not_applicable",
        ),
    ],
)
def test_compatibility_contract(saved, requested, expected):
    assert git_context.compare(saved, requested) == expected
    assert set(json.loads(requested.model_dump_json())) <= {
        "version",
        "repository_id",
        "state",
        "commit",
        "branch",
        "detached",
        "dirty",
    }
