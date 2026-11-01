import asyncio
import json
import os
import subprocess
import time
import tomllib
import uuid
from urllib.parse import urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from colony import cli
from colony.client import Client
from colony.git_context import capture
from colony.mcp import create_mcp
from colony.models import Grant, Scope, Trail
from colony.security import generate_keys, issue, private_write
from colony.server import create_app
from colony.setup import setup_codex
from colony.validation import snapshot


def git(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.check_output(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.autocrlf=false",
            "-C",
            str(repo),
            *args,
        ],
        env=env,
        stderr=subprocess.PIPE,
        text=True,
    ).strip()


@pytest.fixture
def connected(tmp_path, monkeypatch):
    server_repo = tmp_path / "server-repo"
    server_repo.mkdir()
    git(server_repo, "init", "-b", "main")
    (server_repo / "guide.md").write_text(
        "# Build timeout configuration\n\nThe current build timeout configuration is 30 seconds.\n"
    )
    git(server_repo, "add", "guide.md")
    git(server_repo, "commit", "-m", "fixture")
    caller_repo = tmp_path / "caller-repo"
    git(tmp_path, "clone", "--no-hardlinks", str(server_repo), str(caller_repo))
    git(server_repo, "switch", "-c", "server-view")
    admin, home = tmp_path / "admin", tmp_path / "home"
    generate_keys(admin)
    grant = Grant(
        member_id="a",
        name="A",
        server="http://127.0.0.1:7331",
        expires_at=int(time.time()) + 3600,
        scopes=[Scope(namespace="team", pull=True, push=True)],
    )
    private_write(
        home / "identity.json", issue(admin / "issuer.pem", grant).model_dump_json().encode()
    )
    private_write(home / "issuer.pub", (admin / "issuer.pub").read_bytes())
    private_write(home / "queue.key", Fernet.generate_key())
    app = create_app(tmp_path / "data", server_repo, admin / "issuer.pub", repository_id="project")
    http = TestClient(app)

    def post(url, **kwargs):
        return http.post(
            urlsplit(str(url)).path, json=kwargs.get("json"), headers=kwargs.get("headers")
        )

    monkeypatch.setattr(httpx, "post", post)
    client = Client(home)
    trail = Trail(
        id=uuid.uuid4().hex,
        namespace="team",
        question="Build timeout configuration",
        content="SAVED-ANSWER-SENTINEL: The build timeout configuration is 30 seconds.",
        sources=[snapshot(caller_repo, "guide.md")],
    )
    assert client.push(trail, repo=caller_repo, repository_id="project")["stored"]
    return client, caller_repo, server_repo, trail, app, http


def test_native_git_http_caller_server_separation_and_live_branch_switch(connected):
    client, caller, server, trail, app, http = connected
    result = client.ask(trail.question, "team", repo=caller, repository_id="project")
    assert result["hit"] and result["status"] == "validated"
    assert result["git"]["compatibility"] == "matched"
    assert result["content"].startswith("SAVED-ANSWER-SENTINEL")
    inspected = client.memory_inspect(trail.id, "team")
    assert inspected["git"]["origin"]["branch"] == "main"
    assert inspected["git"]["server_observed"]["branch"] == "server-view"
    git(caller, "switch", "-c", "feature")
    result = client.ask(trail.question, "team", repo=caller, repository_id="project")
    assert "SAVED-ANSWER-SENTINEL" not in json.dumps(result)
    assert result["hit"] and result["match_kind"] == "evidence"
    assert result["git"]["compatibility"] == "branch_mismatch"
    # A branch name/commit match must not override a changed caller file.
    git(caller, "switch", "main")
    (caller / "guide.md").write_text("The current build timeout configuration is 60 seconds.\n")
    assert not client.ask(trail.question, "team", repo=caller, repository_id="project")["hit"]
    assert http.get("/health").json() == {"status": "ok"}
    assert http.post("/capabilities", json={}).status_code == 401


def test_mcp_preserves_tools_and_withholds_branch_mismatch_answer(connected):
    client, caller, server, trail, app, http = connected
    mcp = create_mcp(
        client.home,
        workflow_namespace="team",
        workflow_repo=caller,
        workflow_read_only=True,
        workflow_repository_id="project",
    )
    result = json.loads(asyncio.run(mcp.call_tool("ask", {"question": trail.question}))[0].text)
    assert result["status"] == "validated" and result["git"]["compatibility"] == "matched"
    assert result["current_repository_reads"][0]["path"] == "guide.md"
    git(caller, "switch", "-c", "feature")
    result = json.loads(asyncio.run(mcp.call_tool("ask", {"question": trail.question}))[0].text)
    assert "SAVED-ANSWER-SENTINEL" not in json.dumps(result)
    assert (
        result["match_kind"] == "evidence" and result["git"]["compatibility"] == "branch_mismatch"
    )
    assert {tool.name for tool in asyncio.run(mcp.list_tools())} == {"ask"}


def test_setup_and_cli_git_options_bind_explicit_repository(connected):
    client, caller, server, trail, app, http = connected
    setup_codex(client.home, caller, "install", namespace="team", repository_id="project")
    args = tomllib.loads((caller / ".codex/config.toml").read_text())["mcp_servers"]["colony"][
        "args"
    ]
    index = args.index("--workflow-repository-id")
    assert args[index + 1] == "project"
    setup_codex(client.home, caller, "uninstall")
    parsed = cli.parser().parse_args(
        [
            "--home",
            str(client.home),
            "ask",
            trail.question,
            "--ns",
            "team",
            "--repo",
            str(caller),
            "--repository-id",
            "project",
        ]
    )
    assert cli.execute(parsed)["git"]["compatibility"] == "matched"
    assert capture(caller, "project").dirty is False
