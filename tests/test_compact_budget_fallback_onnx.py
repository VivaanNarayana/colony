"""Pinned offline ONNX controls, not provider-token or general retrieval evidence."""

import hashlib
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from colony import context, retrieval, store
from colony.client import Client
from colony.compact import PROFILE
from colony.context_budget import count_response, fit_response, serialize_response
from colony.models import ContextBudget, Grant, Scope, Trail
from colony.provision import model_environment
from colony.security import generate_keys, issue, private_write
from colony.server import create_app
from colony.validation import snapshot

QUESTION = "What is the production inventory request timeout?"
FINDING = "Production inventory requests use a timeout of 2200 milliseconds."
SETTINGS = "PRODUCTION_INVENTORY_TIMEOUT_MS = 2200\n"
NOTES = (
    "# Archive maintenance\n\n"
    "Archive exports preserve original filenames and timestamps. Operators review "
    "the exported manifest before moving old reports to long-term storage. The "
    "manifest records the report owner, calendar date, and destination directory. "
    "A completed export leaves the original report available for comparison.\n\n"
    "# Display settings\n\n"
    "The dashboard uses an indigo theme and a compact navigation bar. The theme "
    "does not modify stored reports. Display preferences belong to the current "
    "viewer and may be reset without changing the archive. Navigation labels "
    "are translated using the selected language pack.\n\n"
    "# Review checklist\n\n"
    "Reviewers compare exported filenames with the manifest and record any missing "
    "reports. The review notes identify the affected date and owner. A subsequent "
    "review appends a new note rather than editing earlier observations. Resolved "
    "items remain visible in the history so another reviewer can reconstruct the "
    "sequence of changes.\n\n"
    "# Retention\n\n"
    "Archive retention is ninety days. Reports older than the retention period "
    "are eligible for manual review. The archive does not remove a report while "
    "an export or review is active. Retention changes are recorded with their "
    "effective date, the previous value, and the person who approved the change.\n"
)


@pytest.fixture
def onnx_transport(tmp_path, monkeypatch):
    if not all(os.environ.get(name) for name in (
        "COLONY_EMBED_MODEL_DIR", "COLONY_RANK_MODEL_DIR",
    )):
        pytest.skip("Explicit pinned offline embedding and reranking snapshots required")
    # Validate pinned bytes before any model use; never allow cache/download fallback.
    monkeypatch.setenv("COLONY_OFFLINE", "1")
    model_environment(None, offline=True)
    assert store.models is retrieval.models and store.index_version is retrieval.index_version
    assert store.embed is retrieval.embed and store.embed_passages is retrieval.embed_passages
    assert store.rank is retrieval.rank and context.rank is retrieval.rank
    for model, variable in zip(retrieval.models(), (
        "COLONY_EMBED_MODEL_DIR", "COLONY_RANK_MODEL_DIR",
    ), strict=True):
        assert Path(model.model._model_dir).resolve() == Path(os.environ[variable]).resolve()
    home, repo = tmp_path / "home", tmp_path / "repo"
    repo.mkdir()
    generate_keys(home)
    grant = Grant(member_id="a", name="ONNX fixture", server="http://127.0.0.1:7331",
                  expires_at=int(time.time()) + 3600,
                  scopes=[Scope(namespace="team", pull=True, push=True)])
    private_write(home / "identity.json", issue(home / "issuer.pem", grant).model_dump_json().encode())
    app = create_app(tmp_path / "data", repo, home / "issuer.pub", repository_id="project")
    with TestClient(app) as http:
        def post(url, **kwargs):
            return http.post(urlsplit(str(url)).path, json=kwargs.get("json"),
                             headers=kwargs.get("headers"))

        monkeypatch.setattr(httpx, "post", post)
        client = Client(home)
        client.request("/join", {})
        yield client, repo, app.state.store


def publish(client, repo, *, question=QUESTION, content=FINDING, oversized_core=False):
    texts = {
        "settings.py": SETTINGS,
        "display.py": 'DISPLAY_THEME = "indigo"\n',
        "archive.py": "ARCHIVE_RETENTION_DAYS = 90\n",
        "archive-notes.md": NOTES,
    }
    if oversized_core:
        # One indivisible source line, not many units the packer could omit.
        texts["settings.py"] = SETTINGS.rstrip() + "  # " + "archive metadata " * 140 + "\n"
        texts["archive-notes.md"] = "Archive reports retain their original filenames.\n"
    assert sum(len(text.rstrip("\n")) for text in texts.values()) <= 2800
    for name, text in texts.items():
        (repo / name).write_bytes(text.encode())
    trail = Trail(id="a" * 32, namespace="team", question=question, content=content,
                  sources=[snapshot(repo, name) for name in texts], tainted=True)
    assert client.push(trail)["stored"] is True
    return trail


def original_packet(database, trail, budget):
    answer = database.find_answer(trail.question, ["team"], 604800, 0.60,
                                  context_budget=budget, compact_bundle=True)
    assert answer["hit"] and answer["status"] == "validated" and answer["id"] == trail.id
    assert len(answer["evidence"]) == 4
    assert all(item["complete_file"] for item in answer["evidence"])
    return fit_response({**answer, "response_profile": PROFILE}, budget, question=trail.question)


def ask(client, trail, budget):
    result = client.ask(trail.question, "team", context_budget=budget, response_profile=PROFILE)
    assert result["context_budget"]["used"] == count_response(result, budget) <= budget.max_tokens
    return result


def selected_evidence(database, trail, budget):
    return database.find_evidence(
        trail.question, ["team"], 604800, context_budget=budget,
        restricted_trail_id=trail.id,
        required_source_identities=frozenset((s.ref, s.content_hash) for s in trail.sources),
    )


def test_real_onnx_budget_fallback_returns_current_timeout_evidence(onnx_transport, monkeypatch):
    client, repo, database = onnx_transport
    trail = publish(client, repo)
    budget = ContextBudget(max_tokens=2048)
    before = original_packet(database, trail, budget)
    assert not before["hit"] and before["reason"] == "budget_limited"
    calls = []
    select = database.find_evidence

    def observed(*args, **kwargs):
        calls.append(kwargs)
        return select(*args, **kwargs)

    monkeypatch.setattr(database, "find_evidence", observed)
    result = ask(client, trail, budget)
    assert len(calls) == 1 and calls[0]["restricted_trail_id"] == trail.id
    assert result["hit"] and result["status"] == "validated" and result["id"] == trail.id
    assert result["match_kind"] == "evidence" and "content" not in result
    assert result["context_budget"]["limited"] is True
    assert len(result["sources"]) == 4
    identities = {s["ref"]: s["content_hash"] for s in result["sources"]}
    assert identities == {s.ref: s.content_hash for s in trail.sources}
    reads = result["current_repository_reads"]
    timeout = next(r for r in reads if r["path"] == "settings.py")
    assert "PRODUCTION_INVENTORY_TIMEOUT_MS = 2200" in timeout["content"]
    assert timeout["start_line"] == 1 and timeout["complete_file"] is False
    for read in reads:
        raw = (repo / read["path"]).read_bytes()
        assert read["sha256"] == identities[read["path"]] == hashlib.sha256(raw).hexdigest()
        lines = raw.decode().splitlines()
        start = read["start_line"] - 1
        assert read["content"] == "\n".join(lines[start:start + len(read["content"].splitlines())])


def test_real_onnx_insufficient_sources_keep_original_budget_miss(onnx_transport):
    client, repo, database = onnx_transport
    trail = publish(client, repo,
                    question="What is Apollo 17 lunar sample 76535's measured mineral composition?",
                    content="This note mentions Apollo 17 sample 76535 but contains no measured mineral composition.")
    budget = ContextBudget(max_tokens=2048)
    before = original_packet(database, trail, budget)
    assert not before["hit"] and before["reason"] == "budget_limited"
    assert selected_evidence(database, trail, budget) is None
    result = ask(client, trail, budget)
    assert serialize_response(result) == serialize_response(before)
    assert "content" not in result and "current_repository_reads" not in result


def test_real_onnx_nonfitting_selected_core_is_not_clipped(onnx_transport):
    client, repo, database = onnx_transport
    trail = publish(client, repo, oversized_core=True)
    budget = ContextBudget(max_tokens=2048)
    before = original_packet(database, trail, budget)
    assert not before["hit"] and before["reason"] == "budget_limited"
    selected = selected_evidence(database, trail, budget)
    assert selected is not None and selected["match_kind"] == "evidence"
    core = next(item for item in selected["evidence"] if item["ref"] == "settings.py")
    assert len(core["text"].encode()) > budget.max_tokens
    assert core["text"] == (repo / "settings.py").read_text().rstrip("\n")
    result = ask(client, trail, budget)
    assert serialize_response(result) == serialize_response(before)
    assert "content" not in result and "current_repository_reads" not in result


def test_real_onnx_fitting_bundle_is_unchanged(onnx_transport, monkeypatch):
    client, repo, database = onnx_transport
    trail = publish(client, repo)
    budget = ContextBudget(max_tokens=8192)
    before = original_packet(database, trail, budget)
    assert before["hit"] is True

    def forbidden(*args, **kwargs):
        pytest.fail("An already-fitting bundle must not enter the new fallback")

    monkeypatch.setattr(database, "find_evidence", forbidden)
    result = ask(client, trail, budget)
    assert serialize_response(result) == serialize_response(before)
    assert len(result["current_repository_reads"]) == 4
    assert all(read["complete_file"] for read in result["current_repository_reads"])
