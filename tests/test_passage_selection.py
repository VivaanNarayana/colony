import json
import uuid
from pathlib import Path

import pytest
from conftest import auth

from colony import store
from colony.compact import PROFILE
from colony.context_budget import count_response
from colony.models import ContextBudget, Trail
from colony.validation import snapshot


@pytest.fixture
def gateway(team):
    _, api, repo, _, key = team
    fixtures = Path(__file__).parent / "token_evaluations" / "fixtures"
    catalog = json.loads((fixtures / "task-adapter-catalog.json").read_bytes())
    workload = next(w for w in catalog["workloads"] if w["id"] == "gateway")
    questions = {task["id"]: task["question"] for task in workload["tasks"]}
    paths = ["client.py", "handler.py", "settings.py"]
    for path in paths:
        (repo / path).write_bytes((fixtures / "gateway" / "v1" / path).read_bytes())

    def publish(question, content, paths):
        trail = Trail(id=uuid.uuid4().hex, namespace="team", question=question, content=content,
                      sources=[snapshot(repo, path) for path in paths])
        api.post("/trails", headers=auth(key), json=trail.model_dump()).raise_for_status()
        return trail.id

    def ask(limit=3072):
        budget = ContextBudget(max_tokens=limit)
        response = api.post("/ask", headers=auth(key), json={
            "question": questions["useful_hit"], "namespace": "team", "response_profile": PROFILE,
            "context_budget": budget.model_dump(),
        })
        response.raise_for_status()
        result = response.json()
        assert result["context_budget"]["used"] == count_response(result, budget) <= limit
        return result

    original = publish(questions["cold_learning"], "See the gateway configuration and request loop.", paths)
    baseline = ask()
    assert baseline["id"] == original and baseline["match_kind"] == "evidence"
    assert baseline["evidence_scope"] == "cited_files"
    assert {r["path"] for r in baseline["current_repository_reads"]} == set(paths)
    return repo, publish, ask, baseline, questions["useful_hit"]


def test_over_budget_passage_leaves_room_for_a_fitting_compound_finding(gateway):
    repo, publish, ask, baseline, question = gateway
    # The selected evidence unit for a shell script is the entire script.
    (repo / "guide.sh").write_text(
        "# " + question + "\n# Inventory requests use the configured timeout.\n\n"
        + "# Release notes for the deployment.\n" * 95
    )
    guide = publish("Who owns the release schedule?", "The release manager.", ["guide.sh"])
    wide = ask(8192)
    assert wide["id"] == guide and wide["match_kind"] == "evidence"
    assert wide["context_budget"]["used"] > 3072
    assert [r["path"] for r in wide["current_repository_reads"]] == ["guide.sh"]
    assert ask() == baseline
    assert ask(256)["reason"] == "budget_limited"


def test_duplicate_passages_share_rank_slots_but_keep_original_citations(gateway, monkeypatch):
    repo, publish, ask, baseline, _ = gateway
    for i in range(1, 24):
        path = f"v{i:02}/client.py"
        (repo / path).parent.mkdir()
        (repo / path).write_bytes((repo / "client.py").read_bytes())
        publish("Who owns release scheduling?", "The release manager.", [path])

    ranked = []
    real_rank = store.rank

    def rank(query, texts):
        if any("def fetch(" in text for text in texts):
            ranked.append(list(texts))
        return real_rank(query, texts)

    monkeypatch.setattr(store, "rank", rank)
    assert ask() == baseline
    assert len(ranked) == 1
    assert len(ranked[0]) == len(set(ranked[0])) <= 48
    # The copies cannot supply a replacement citation for the changed original.
    (repo / "client.py").write_text("# Inventory requests are disabled.\n")
    assert ask().get("status") != "validated"


@pytest.mark.parametrize("profile", [PROFILE, None], ids=["compact", "ordinary"])
def test_over_budget_duplicate_uses_the_fitting_passages_own_path_and_line(team, monkeypatch, profile):
    app, api, repo, _, key = team
    question = "Which setting controls archive retention?"
    text = "Archive retention is controlled by ARCHIVE_DAYS."
    (repo / "guide.txt").write_text("Notes\n\n" + text)
    (repo / "deploy.sh").write_text(text + "\n\n" + "# A deployment note.\n" * 150)
    # Tie the dense scores so that the newer shell script's passage ranks first.
    vector = store.embed(question, query=True)
    monkeypatch.setattr(store, "embed_passages", lambda values: [vector] * len(values))
    monkeypatch.setattr(store, "rank", lambda q, values: [5.0 if v == text else -9.0 for v in values])
    ids = {}
    for path in ["guide.txt", "deploy.sh"]:
        trail = Trail(id=uuid.uuid4().hex, namespace="team", question="Who owns release scheduling?",
                      content="The release manager.", sources=[snapshot(repo, path)])
        api.post("/trails", headers=auth(key), json=trail.model_dump()).raise_for_status()
        ids[path] = trail.id

    def ask(limit):
        budget = ContextBudget(max_tokens=limit)
        result = app.state.store.ask(question, ["team"], 604800, 0.81,
                                     context_budget=budget, response_profile=profile)
        assert result["context_budget"]["used"] == count_response(result, budget) <= limit
        return result

    assert ask(8192)["id"] == ids["deploy.sh"]
    tight = ask(2600)
    assert tight["id"] == ids["guide.txt"] and tight["match_kind"] == "evidence"
    if profile:
        read, = tight["current_repository_reads"]
        assert (read["path"], read["start_line"], read["content"]) == ("guide.txt", 3, text)
    else:
        read, = tight["evidence"]
        assert (read["ref"], read["line"], read["text"]) == ("guide.txt", 3, text)
    (repo / "guide.txt").write_text("The guidance changed.")
    assert ask(2600)["reason"] == "budget_limited"
    with app.state.store.connect() as db:
        hits = db.execute("SELECT trail_id FROM validations WHERE validator='repo-evidence-v1'").fetchall()
    assert sorted(row[0] for row in hits) == sorted(ids.values())


@pytest.mark.parametrize("short_logit", [-4.0, 5.0], ids=["separate_passage", "near_tie"])
def test_over_budget_passage_preserves_near_ties_within_a_finding(team, monkeypatch, short_logit):
    app, api, repo, _, key = team
    question = "What is archive retention?"
    long = "Archive retention is controlled by ARCHIVE_DAYS."
    short = "Archive retention defaults to 90 days."
    (repo / "deploy.sh").write_text(long + "\n\n" + "# Deployment note.\n" * 180)
    (repo / "guide.txt").write_text(short)
    vector = store.embed(question, query=True)
    monkeypatch.setattr(store, "embed_passages", lambda values: [vector] * len(values))
    logits = {long: 5.0, short: short_logit}
    monkeypatch.setattr(store, "rank", lambda q, values: [logits.get(v, -9.0) for v in values])
    trail = Trail(id=uuid.uuid4().hex, namespace="team", question="Who owns release scheduling?",
                  content="The release manager.", sources=[snapshot(repo, p) for p in ("deploy.sh", "guide.txt")])
    api.post("/trails", headers=auth(key), json=trail.model_dump()).raise_for_status()
    value = app.state.store.ask(question, ["team"], 604800, 0.81, response_profile=PROFILE,
                                context_budget=ContextBudget(max_tokens=2600))
    if short_logit == 5.0:
        assert value["reason"] == "budget_limited"
    else:
        assert value["id"] == trail.id and value["match_kind"] == "evidence"
        read, = value["current_repository_reads"]
        assert (read["path"], read["content"]) == ("guide.txt", short)
