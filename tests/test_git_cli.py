import json
import sys
import time

import httpx
import pytest
from cryptography.fernet import Fernet

from colony import cli, git_context
from colony.client import Client
from colony.models import GitContext, Grant, Scope
from colony.security import generate_keys, issue, private_write
from colony.validation import snapshot


@pytest.fixture
def publication(tmp_path, monkeypatch):
    home, repo = tmp_path / "private home", tmp_path / "repository"
    repo.mkdir()
    (repo / "guide.md").write_text("The build timeout is 30 seconds.\n")
    generate_keys(home)
    grant = Grant(
        member_id="fixture",
        name="Fixture",
        server="http://127.0.0.1:1",
        expires_at=int(time.time()) + 3600,
        scopes=[Scope(namespace="team", pull=True, push=True)],
    )
    private_write(
        home / "identity.json", issue(home / "issuer.pem", grant).model_dump_json().encode()
    )
    private_write(home / "queue.key", Fernet.generate_key())
    source = snapshot(repo, "guide.md")
    finding = tmp_path / "finding.json"
    calls = []

    def prepare(context, supplied):
        def capture(path, repository_id):
            assert path == repo and repository_id == "project"
            return context

        def request(client, route, payload):
            calls.append((route, payload))
            if route == "/capabilities":
                return {"git_context": {"version": context.version, "repository_id": "project"}}
            assert route == "/trails"
            return {"id": payload["id"], "stored": True}

        monkeypatch.setattr(git_context, "capture", capture)
        monkeypatch.setattr(Client, "request", request)
        finding.write_text(
            json.dumps(
                {
                    "id": "a" * 32,
                    "question": "Build timeout",
                    "content": "Timeout is 30 seconds.",
                    "sources": [source.model_dump()],
                    "git_context": supplied,
                }
            )
        )
        words = [
            "--home",
            str(home),
            "push",
            str(finding),
            "--ns",
            "team",
            "--repo",
            str(repo),
            "--repository-id",
            "project",
        ]
        return words

    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("Unexpected network"))
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **k: pytest.fail("Unexpected network"))
    return prepare, calls


def context(kind="git"):
    if kind == "non_git":
        return GitContext(repository_id="project", state="non_git")
    return GitContext(
        repository_id="project",
        state="git",
        commit="b" * 40,
        branch=None if kind == "detached" else "main",
        detached=kind == "detached",
        dirty=False,
    )


@pytest.mark.parametrize("kind", ["git", "non_git", "detached"])
@pytest.mark.parametrize("spelling", ["canonical", "omitted_defaults"])
def test_cli_push_normalizes_valid_git_context_defaults(publication, kind, spelling):
    prepare, calls = publication
    captured = context(kind)
    supplied = captured.model_dump()
    if spelling == "omitted_defaults":
        if kind == "git":
            del supplied["version"]
        elif kind == "non_git":
            supplied = {"repository_id": "project", "state": "non_git"}
        else:
            del supplied["branch"]
    words = prepare(captured, supplied)

    result = cli.execute(cli.parser().parse_args(words))

    assert result["stored"] is True
    assert [route for route, _ in calls] == ["/capabilities", "/trails"]
    assert calls[-1][1]["git_context"] == captured.model_dump()


@pytest.mark.parametrize(
    "change",
    [
        {"branch": "another"},
        {"commit": "c" * 40},
        {"repository_id": "different"},
        {"unknown": "synthetic-private-value"},
        {"branch": "password=synthetic-private-value"},
        {"state": "invalid-state"},
    ],
    ids=["branch", "commit", "repository", "unknown_key", "credential_shape", "invalid_state"],
)
def test_cli_push_refuses_changed_or_invalid_origin_without_echo(
    publication, monkeypatch, capsys, change
):
    prepare, calls = publication
    supplied = {**context().model_dump(), **change}
    words = prepare(context(), supplied)
    with pytest.raises(ValueError) as error:
        cli.execute(cli.parser().parse_args(words))
    assert str(error.value) == "Git context differs from the current publication context"
    assert calls == []

    monkeypatch.setattr(sys, "argv", ["colony", *words])
    with pytest.raises(SystemExit) as exit_error:
        cli.main()
    assert exit_error.value.code == 1 and calls == []
    output = capsys.readouterr()
    assert "synthetic-private-value" not in output.err and "input_value" not in output.err
