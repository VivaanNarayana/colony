"""Caller-visible evidence through task MCP, the HTTP client/server and pinned offline models."""
import asyncio
import hashlib
import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest
from test_compact_budget_fallback_onnx import onnx_transport as onnx_transport

from colony import context, store
from colony.compact import PROFILE
from colony.context_budget import count_response, fit_response
from colony.models import ContextBudget, Trail
from colony.task_mcp import TaskMCP
from colony.validation import snapshot
from colony.workflow import Workflow


def publish(client, repo, question, content, paths):
    trail = Trail(id=uuid.uuid4().hex, namespace="team", question=question, content=content,
                  sources=[snapshot(repo, path) for path in paths])
    assert client.push(trail)["stored"]
    return trail.id


def research(client, repo, question, limit, encoding="utf8_bytes"):
    budget = ContextBudget(max_tokens=limit, encoding=encoding)
    task = TaskMCP(Workflow(client, "team", repo, read_only=True), question, context_budget=budget)
    try:
        block, = asyncio.run(task.call_tool("research", {}))
    finally:
        task.evidence.close()
    if encoding == "utf8_bytes":
        assert len(block.text.encode("utf-8")) <= limit
    value = json.loads(block.text)
    assert value["context_budget"]["used"] == count_response(value, budget) <= limit
    return value


def cited_lines(value, repo):
    """Index only text that actually reached the caller at its verified path and line."""
    assert value["hit"] and value["status"] == "validated" and value["match_kind"] == "evidence"
    hashes = {s["ref"]: s["content_hash"] for s in value["sources"]}
    lines = {}
    for read in value["current_repository_reads"]:
        path = read["path"]
        raw = (repo / path).read_bytes()
        assert read["sha256"] == hashes[path] == hashlib.sha256(raw).hexdigest()
        source = raw.decode().splitlines()
        for n, line in enumerate(read["content"].splitlines(), start=read["start_line"]):
            assert n >= 1 and line == source[n - 1]
            lines[path, n] = line
    return lines


def learning_case(repo, workload):
    fixtures = Path(__file__).parent / "token_evaluations" / "fixtures"
    catalog = json.loads((fixtures / "task-adapter-catalog.json").read_bytes())
    tasks = {t["id"]: t for t in next(w["tasks"] for w in catalog["workloads"] if w["id"] == workload)}
    learn = tasks["cold_learning"]
    snapshot_dir = fixtures / workload / "v1"
    for path in learn["publication_sources"]:
        (repo / path).write_bytes((snapshot_dir / path).read_bytes())
    return snapshot_dir, learn, tasks["useful_hit"]


@pytest.mark.parametrize("paragraphs", [1, 8], ids=["copied_file", "copied_file_and_paragraphs"])
def test_two_near_tied_facts_reach_the_caller_with_a_duplicate_file(onnx_transport, paragraphs):
    client, repo, _ = onnx_transport
    timeout = "The request timeout is 1200 milliseconds."
    retry = "The retry limit is four attempts."
    (repo / "v2").mkdir()
    for path in ("primary.txt", "v2/primary.txt"):
        (repo / path).write_text("\n\n".join([timeout] * paragraphs))
    (repo / "secondary.txt").write_text(retry)
    original = publish(client, repo, "Who owns release scheduling?", "The release manager.",
                       ["primary.txt", "secondary.txt", "v2/primary.txt"])
    question = "What are the request timeout and retry limit?"
    value = research(client, repo, question, 1800)
    assert value.get("id") == original
    lines = cited_lines(value, repo)
    assert lines.get(("secondary.txt", 1)) == retry
    assert any(text == timeout and path in {"primary.txt", "v2/primary.txt"}
               for (path, _), text in lines.items())
    assert len(value["current_repository_reads"]) == 2


@pytest.mark.parametrize("prefix", ["", "1. "])
def test_cited_numeric_answer_stays_compact_even_when_it_mentions_another_file(onnx_transport, prefix):
    client, repo, _ = onnx_transport
    text = prefix + "The request timeout is 1200 milliseconds. See details.txt."
    (repo / "primary.txt").write_text(text)
    (repo / "details.txt").write_text("The release calendar lists the owners.")
    target = publish(client, repo, "Who owns release scheduling?", "The release manager.",
                     ["primary.txt", "details.txt"])
    value = research(client, repo, "Is the request timeout 1200 milliseconds?", 3072)
    assert value["id"] == target
    assert cited_lines(value, repo) == {("primary.txt", 1): text}


@pytest.mark.parametrize("answer", ["Archive retention is permanent.",
                                     "Archive retention is permanent. See notes.txt.",
                                     "Archive retention: permanent. See notes.txt."])
def test_complete_policy_answer_stays_compact(onnx_transport, answer):
    client, repo, _ = onnx_transport
    (repo / "answer.txt").write_text(answer)
    (repo / "notes.txt").write_text(
        "Archive maintenance follows the schedule in the release calendar. "
        "Operations staff rotate the weekly schedule.\n" * 10
    )
    target = publish(client, repo, "Who owns release scheduling?", "The release manager.",
                     ["answer.txt", "notes.txt"])
    value = research(client, repo, "Is archive retention permanent or temporary?", 3072)
    assert value["id"] == target
    assert cited_lines(value, repo) == {("answer.txt", 1): answer}


def test_numbered_question_with_a_link_delivers_the_policy(onnx_transport):
    client, repo, _ = onnx_transport
    question = "Is archive retention permanent or temporary?"
    (repo / "overview.md").write_text(f"1. {question} See notes.txt.")
    answer = "Archive retention is permanent."
    (repo / "notes.txt").write_text(answer)
    target = publish(client, repo, "Who owns release scheduling?", "The release manager.",
                     ["overview.md", "notes.txt"])
    value = research(client, repo, question, 3072)
    assert value["id"] == target
    assert cited_lines(value, repo)["notes.txt", 1] == answer


@pytest.mark.parametrize("heading", [
    "## How the production request timeout is configured\nSee settings.py.",
    "Where is the production request timeout configured: see settings.py.",
    "## How the production request timeout is configured\n[settings.py](./settings.py)",
])
def test_heading_with_source_pointer_delivers_configuration(onnx_transport, heading):
    client, repo, _ = onnx_transport
    question = "Where and how is the production request timeout configured?"
    (repo / "overview.md").write_text(heading)
    (repo / "settings.py").write_text("PRODUCTION_TIMEOUT_MS = 1500")
    target = publish(client, repo, "Who owns release scheduling?", "The release manager.",
                     ["overview.md", "settings.py"])
    result = research(client, repo, question, 3072)
    assert result["id"] == target
    assert cited_lines(result, repo)["settings.py", 1] == "PRODUCTION_TIMEOUT_MS = 1500"


@pytest.mark.parametrize(("heading", "ref"), [
    ("[Production request timeout](./settings.py)", "./settings.py"),
    ("[Production request timeout](./settings.py), [details](#details).", "settings.py"),
    ("[Production request timeout](./settings.py), [jump to the appendix](#appendix).", "settings.py"),
    ("## How the production request timeout is configured\nSee settings.py.", "settings.py"),
    ("## How the production request timeout is configured\n[settings.py](./settings.py), "
     "[details](#details).", "./settings.py"),
])
def test_source_pointer_survives_spelling_anchor_and_paraphrase(onnx_transport, heading, ref):
    client, repo, _ = onnx_transport
    (repo / "overview.md").write_text(heading)
    (repo / ref).write_text("PRODUCTION_TIMEOUT_MS = 1500")
    target = publish(client, repo, "Who owns release scheduling?", "The release manager.", ["overview.md", ref])
    value = research(client, repo, "What is the production request timeout and where is it configured?", 3072)
    assert value["id"] == target
    assert cited_lines(value, repo)[ref, 1] == "PRODUCTION_TIMEOUT_MS = 1500"


def test_link_spelling_keeps_sibling_namesakes_separate():
    refs = {"./docs/index.md", "./config/settings.py", "./docs/settings.py", "./settings.py"}
    for link, expected in (("./settings.py", "./docs/settings.py"), ("../settings.py", "./settings.py"),
                           ("../config/settings.py", "./config/settings.py")):
        _, targets, _ = context._pointer_links(f"[settings.py]({link}), [help](#top).", "./docs/index.md", refs)
        assert targets == {expected}


@pytest.mark.parametrize(("text", "expected"), [
    ("[settings.py](./settings.py)", {"docs/settings.py"}),
    ("[settings.py](../settings.py#timeout)", {"settings.py"}),
    ('[settings.py](<../config%20files/settings.py> "defaults")', {"config files/settings.py"}),
    ("See ./settings.py and ../settings.py.", {"docs/settings.py", "settings.py"}),
    ("[settings.py](./missing/settings.py)", set()),
    ("[settings.py](../../settings.py)", set()),
    ("[settings.py](https://example.org/settings.py)", set()),
    ("[settings.py](#local)", set()),
])
def test_relative_pointer_uses_the_destination_and_containing_directory(text, expected):
    refs = {"docs/index.md", "settings.py", "docs/settings.py", "config files/settings.py"}
    _, targets, unresolved = context._pointer_links(text, "docs/index.md", refs)
    assert targets == expected
    assert unresolved == (not expected)


def test_numeric_fact_in_a_heading_stays_in_the_answer(onnx_transport):
    client, repo, _ = onnx_transport
    text = "## How the request timeout is set to 1200 milliseconds\nSee details.txt."
    (repo / "answer.md").write_text(text)
    (repo / "details.txt").write_text("The release manager owns scheduling.")
    publish(client, repo, "Who owns release scheduling?", "The release manager.", ["answer.md", "details.txt"])
    result = research(client, repo, "How is the request timeout set to 1200 milliseconds?", 3072)
    assert cited_lines(result, repo) == {("answer.md", n): line for n, line in enumerate(text.splitlines(), 1)}


@pytest.mark.parametrize("background", [False, True], ids=["small_bundle", "oversized_note"])
def test_relative_links_select_only_the_linked_settings(onnx_transport, background):
    client, repo, _ = onnx_transport
    _, learn, reuse = learning_case(repo, "gateway")
    (repo / "archive").mkdir()
    (repo / "archive/settings.py").write_text('DEFAULTS = {"timeout_ms": 8000, "attempts": 1}')
    (repo / "overview.md").write_text(
        "[Production inventory request count and timeout](./settings.py), "
        "[request](./handler.py), [count](./client.py)."
    )
    paths = [*learn["publication_sources"], "overview.md", "archive/settings.py"]
    if background:
        (repo / "notes.txt").write_text("Release calendar covers the next maintenance window.\n" * 80)
        paths.append("notes.txt")
    target = publish(client, repo, learn["question"], "See the configuration and execution loop.", paths)
    for budget in (3072, 2450):
        value = research(client, repo, reuse["question"], budget)
        assert value["id"] == target and {s["ref"] for s in value["sources"]} == set(paths)
        lines = cited_lines(value, repo)
        for citation in reuse["oracle"]["citations"]:
            assert all((citation["path"], n) in lines for n in range(citation["start"], citation["end"] + 1))
        assert ("archive/settings.py", 1) not in lines


@pytest.mark.parametrize("duplicate_pointer", [False, True], ids=["new_fact", "copy_of_pointer"])
def test_targeted_pointer_packet_preserves_an_independently_selected_fact(tmp_path, duplicate_pointer):
    files = {"overview.md": "2) Retry limit: see settings.txt.",
             "notes.txt": "The request timeout is 1200 milliseconds.",
             "settings.txt": "The retry limit is four attempts.",
             "unrelated.txt": "Unused background.\n" * 200}
    if duplicate_pointer:
        files["settings.txt"] = files["overview.md"]
    for path, content in files.items():
        (tmp_path / path).write_text(content)
    sources = [snapshot(tmp_path, p) for p in files]

    def bundle(sources, refs=None):
        selected = sources if refs is None else [s for s in sources if s.ref in refs]
        return context.complete_source_bundle(tmp_path, selected, adaptive=True, max_files=8)

    options = context.passage_options(tmp_path, sources, "What are the request timeout and retry limit?",
                                      preferred=[("overview.md", 1), ("notes.txt", 1)],
                                      source_bundle=bundle, adaptive=True)
    paths = ["overview.md", "notes.txt"] if duplicate_pointer else ["notes.txt", "settings.txt"]
    assert [(r["ref"], r["line"], r["text"]) for r in next(options)] == [(p, 1, files[p]) for p in paths]


@pytest.mark.parametrize("workload", ["gateway", "worker"])
@pytest.mark.parametrize(("prefix", "budget", "case"), [
    ("1. ", 3072, "all_names"), ("", 2200, "all_names"), ("1) ", 2200, "all_names"),
    ("", 2200, "unnamed_dependency"), ("2) ", 2400, "oversized_attachment"),
    ("2) ", 2400, "oversized_unnamed_dependency"),
    ("", 2400, "oversized_keyword_index"), ("", 2400, "oversized_single_link"),
    ("", 2400, "aggregate_keyword_index"), ("", 2400, "wire_keyword_index"),
    ("", 2400, "background_keyword_index"),
])
def test_pointer_delivers_the_referenced_facts(onnx_transport, workload, prefix, budget, case):
    client, repo, _ = onnx_transport
    snapshot_dir, learn, reuse = learning_case(repo, workload)
    paths = learn["publication_sources"]
    topic = ("Production inventory request count and timeout" if workload == "gateway" else
             "Priority job backend submissions and wait schedule")
    named = paths[:2] if case.endswith("unnamed_dependency") else paths
    pointer = topic + ": see " + ", ".join(named) + "."
    if case.endswith("keyword_index"):
        pointer = "Index tags: " + topic + "."
    elif case == "oversized_single_link":
        pointer = f"[{topic}](./{paths[0]})"
    (repo / "overview.md").write_text(prefix + pointer)
    sources = [*paths, "overview.md"]
    if case.startswith("oversized_") or case.endswith("keyword_index"):
        repeats = {"aggregate_keyword_index": 49, "wire_keyword_index": 25, "background_keyword_index": 45}.get(case, 80)
        note = "Release calendar covers the next maintenance window."
        if case == "background_keyword_index":
            note = ("Production" if workload == "gateway" else "Priority") + " release calendar."
        (repo / "notes.txt").write_text((note + "\n") * repeats)
        sources.append("notes.txt")
    target = publish(client, repo, learn["question"], "See the configuration and execution loop.", sources)
    value = research(client, repo, reuse["question"], budget)
    assert value["id"] == target
    assert {s["ref"] for s in value["sources"]} == set(sources)
    lines = cited_lines(value, repo)
    for citation in reuse["oracle"]["citations"]:
        path = citation["path"]
        expected = (snapshot_dir / path).read_text().splitlines()
        for n in range(citation["start"], citation["end"] + 1):
            assert lines[path, n] == expected[n - 1]
    if budget < 3072:
        assert {r["path"] for r in value["current_repository_reads"]} == set(paths)
    if "notes.txt" in sources:
        (repo / "notes.txt").write_text("Updated calendar.")
        assert not research(client, repo, reuse["question"], budget).get("current_repository_reads")


@pytest.mark.parametrize(("workload", "repeated", "tags"), [
    ("gateway", "Which team collects inventory setup metrics?", "Production inventory request timeout."),
    ("worker", "What is the evidence for the worker setup report in production?", "Priority job backend."),
], ids=["gateway", "worker"])
@pytest.mark.parametrize("conditions", [
    "repeated_questions", "keyword_index", "pointer", "markdown_link", "topic_list", "combined",
])
def test_compound_facts_and_citations_reach_the_caller(onnx_transport, workload, repeated, tags, conditions):
    client, repo, _ = onnx_transport
    snapshot_dir, learn, reuse = learning_case(repo, workload)
    paths = list(learn["publication_sources"])
    if conditions != "repeated_questions":
        topic = ("Production inventory request count and timeout" if workload == "gateway" else
                 "Priority job backend submissions and wait schedule")
        index = {"pointer": f"{topic}: see {paths[0]} and {paths[1]}.",
                 "markdown_link": f"[{topic}](./{paths[0]}#L1)",
                 "topic_list": topic + "."}.get(conditions, "Index tags: " + tags)
        (repo / "overview.txt").write_text(index)
        paths.append("overview.txt")
    if conditions == "combined":
        (repo / "v2").mkdir()
        copy = "v2/" + paths[0]
        (repo / copy).write_bytes((repo / paths[0]).read_bytes())
        paths.append(copy)
    target = publish(client, repo, learn["question"], "See the configuration and execution loop.", paths)
    if conditions in {"repeated_questions", "combined"}:
        for n in range(12):
            content = f"Team ownership is recorded in the release calendar. Revision {n}."
            publish(client, repo, repeated, content, paths)

    upstream = client.ask(reuse["question"], "team", response_profile=PROFILE, context_budget=ContextBudget())
    assert upstream.get("id") == target, upstream.get("reason")
    # Fit the model-facing tool result into exactly the bytes its upstream evidence used.
    value = research(client, repo, reuse["question"], upstream["context_budget"]["used"])
    assert value.get("id") == target and value["evidence_scope"] == "cited_files"
    lines = cited_lines(value, repo)
    for citation in reuse["oracle"]["citations"]:
        path = citation["path"]
        expected = (snapshot_dir / path).read_text().splitlines()
        for n in range(citation["start"], citation["end"] + 1):
            assert lines[path, n] == expected[n - 1]
    if workload == "gateway":
        assert '"attempts": 3' in lines["settings.py", 1] and '"timeout_ms": 1500' in lines["settings.py", 2]
    else:
        assert '"attempts": 4, "delay_ms": 250' in lines["policy.py", 2]
        assert "return result" in lines["executor.py", 5] and "sleep(delay_ms)" in lines["executor.py", 6]
    assert research(client, repo, reuse["question"], 256)["reason"] == "budget_limited"
    if conditions == "combined":
        (repo / "overview.txt").write_text("Index replaced.")
        assert not research(client, repo, reuse["question"], 3072).get("current_repository_reads")


@pytest.mark.parametrize("encoding", ["utf8_bytes", "o200k_base"])
@pytest.mark.parametrize("shared_copy", [False, True], ids=["single_passage", "distinct_lines_in_one_copy"])
def test_shorter_equivalent_citation_keeps_both_facts_at_the_wire_limit(
    onnx_transport, monkeypatch, encoding, shared_copy,
):
    client, repo, _ = onnx_transport
    if encoding == "o200k_base":
        pytest.importorskip("tiktoken")
        tokenizer = os.environ.get("COLONY_TEST_CONTEXT_TOKENIZER")
        if not tokenizer:
            pytest.skip("Verified local tokenizer fixture unavailable")
        monkeypatch.setenv("COLONY_CONTEXT_TOKENIZER", tokenizer)
    long = "settings/production/gateway/request_timeout.txt"
    timeout = "The request timeout is 1200 milliseconds."
    retry = "The retry limit is four attempts."
    question = "What are the request timeout and retry limit?"
    (repo / long).parent.mkdir(parents=True)
    second = "settings/production/gateway/retry_limit.txt" if shared_copy else "secondary.txt"
    files = {"a.txt": timeout, long: timeout, second: retry}
    for path, text in files.items():
        (repo / path).write_text(timeout + "\n\n" + retry if shared_copy else text)
    target = publish(client, repo, "Who owns release scheduling?", "The release manager.",
                     list(files))
    budget = ContextBudget(encoding=encoding)
    upstream = client.ask(question, "team", context_budget=budget, response_profile=PROFILE)
    assert {r["path"] for r in upstream["current_repository_reads"]} == {long, second}
    # Measure the equivalent packet using the actual encoding and the same hashes.
    # Account for changes to the receipt's own max_tokens and limited fields.
    for read in upstream["current_repository_reads"]:
        if shared_copy or read["path"] == long:
            read["path"] = "a.txt"
    limit = fit_response(upstream, budget)["context_budget"]["used"]
    while limit > 256 and fit_response(upstream, ContextBudget(max_tokens=limit - 1, encoding=encoding))["hit"]:
        limit -= 1
    tight = research(client, repo, question, limit, encoding)
    assert tight.get("id") == target
    second_line = ("a.txt", 3) if shared_copy else (second, 1)
    assert cited_lines(tight, repo) == {("a.txt", 1): timeout, second_line: retry}
    assert research(client, repo, question, limit - 1, encoding)["reason"] == "budget_limited"
    if shared_copy:
        (repo / "a.txt").write_text("Superseded policy.")
        assert not research(client, repo, question, limit, encoding).get("current_repository_reads")


@pytest.mark.parametrize(("encoding", "limit"), [("utf8_bytes", 1536), ("o200k_base", 256)])
def test_repeated_publications_do_not_reread_files_for_citation_permutations(onnx_transport, monkeypatch,
                                                                           encoding, limit):
    client, repo, _ = onnx_transport
    if encoding == "o200k_base":
        if not (tokenizer := os.environ.get("COLONY_TEST_CONTEXT_TOKENIZER")):
            pytest.skip("Verified local tokenizer fixture unavailable")
        monkeypatch.setenv("COLONY_CONTEXT_TOKENIZER", tokenizer)
    paths = ["settings/production/gateway/request_timeout.txt", "settings/production/gateway/retry_limit.txt",
             *(f"copy-{n}.txt" for n in range(6))]
    for path in paths:
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text("The request timeout is 1200 milliseconds.\n\nThe retry limit is four attempts.")
    for _ in range(12):
        publish(client, repo, "Who owns release scheduling?", "The release manager.", paths)

    reads = []
    original = context.source_lines

    def counted(root, source):
        reads.append(source.ref)
        return original(root, source)

    validations, renders = [], []
    original_validate, original_excerpts = store.validate, context.excerpts

    def validate(root, sources):
        validations.extend(s.ref for s in sources)
        return original_validate(root, sources)

    def excerpts(*args, **kwargs):
        renders.append(1)
        return original_excerpts(*args, **kwargs)

    monkeypatch.setattr(store, "validate", validate)
    monkeypatch.setattr(context, "excerpts", excerpts)
    monkeypatch.setattr(context, "source_lines", counted)
    question = "What are the request timeout and retry limit?"
    assert research(client, repo, question, limit, encoding)["reason"] == "budget_limited"
    assert set(reads) == set(paths) and len(reads) <= 3 * len(paths)
    assert len(renders) <= len(paths) ** 2
    assert len(validations) <= 2 * len(paths)
    value = research(client, repo, question, 3072)
    assert set(cited_lines(value, repo).values()) == {
        "The request timeout is 1200 milliseconds.", "The retry limit is four attempts.",
    }


def test_repeated_over_budget_bundles_bound_work_under_writer_lock(onnx_transport, monkeypatch):
    client, repo, database = onnx_transport
    _, learn, reuse = learning_case(repo, "gateway")
    paths = learn["publication_sources"]
    ids = [publish(client, repo, learn["question"], "See the gateway configuration and request loop.", paths)
           for _ in range(24)]

    connect, bundle = database.connect, store.complete_source_bundle
    connections, builds = [], []

    @contextmanager
    def traced_connect():
        with connect() as db:
            connections.append(db)
            try:
                yield db
            finally:
                connections.pop()

    def traced_bundle(*args, **kwargs):
        builds.append(connections[-1].in_transaction)
        return bundle(*args, **kwargs)

    monkeypatch.setattr(database, "connect", traced_connect)
    monkeypatch.setattr(store, "complete_source_bundle", traced_bundle)
    assert research(client, repo, reuse["question"], 256)["reason"] == "budget_limited"
    # The shortlist and final selection each need at most one materialization.
    assert 0 < len(builds) <= 2 and not any(builds)
    upstream = client.ask(reuse["question"], "team", context_budget=ContextBudget(), response_profile=PROFILE)
    assert upstream["id"] == ids[-1]
    client.memory_feedback(ids[-1], "team", verdict="incorrect")
    # The newest packet's feedback warning now costs extra bytes. The previous
    # publication has identical sources, different metadata and still fits.
    assert research(client, repo, reuse["question"], upstream["context_budget"]["used"])["id"] == ids[-2]
    assert not any(builds)


@pytest.mark.parametrize(("encoding", "change"), [
    ("utf8_bytes", "feedback"), ("utf8_bytes", "concurrent_feedback"), ("o200k_base", "id"),
])
def test_repeated_passage_miss_allows_a_smaller_publication(onnx_transport, monkeypatch, encoding, change):
    client, repo, database = onnx_transport
    if encoding == "o200k_base":
        if not (tokenizer := os.environ.get("COLONY_TEST_CONTEXT_TOKENIZER")):
            pytest.skip("Verified local tokenizer fixture unavailable")
        monkeypatch.setenv("COLONY_CONTEXT_TOKENIZER", tokenizer)
    question = "What are the request timeout and retry limit?"
    sources = []
    for ref, text in (("timeout.txt", "The request timeout is 1200 milliseconds."),
                      ("retry.txt", "The retry limit is four attempts.")):
        (repo / ref).write_text(text)
        sources.append(snapshot(repo, ref))
    older = Trail(id="a" * 32, namespace="team", question="Who owns release scheduling?",
                  content="The release manager.", sources=sources)
    assert client.push(older)["stored"]
    budget = ContextBudget(encoding=encoding)
    first = client.ask(question, "team", context_budget=budget, response_profile=PROFILE)
    assert first["id"] == older.id and len(cited_lines(first, repo)) == 2
    limit = first["context_budget"]["used"]
    while limit > 256 and fit_response(first, ContextBudget(max_tokens=limit - 1, encoding=encoding))["hit"]:
        limit -= 1
    newer = older.model_copy(update={"id": "a9b8c7d6" * 4})
    assert client.push(newer)["stored"]
    if change == "feedback":
        client.memory_feedback(newer.id, "team", verdict="incorrect")
    assert client.ask(question, "team", context_budget=budget, response_profile=PROFILE)["id"] == newer.id
    if change == "concurrent_feedback":
        record = database._record_evidence

        def add_feedback(db, sources, requested, value, packet, pack):
            monkeypatch.setattr(database, "_record_evidence", record)
            assert value["id"] == newer.id
            with database.connect() as other:
                other.execute("INSERT INTO memory_feedback_v1 VALUES (?,?,?,?,?)",
                              (newer.id, "reviewer", "incorrect", b"", 1))
            return record(db, sources, requested, value, packet, pack)

        monkeypatch.setattr(database, "_record_evidence", add_feedback)
    tight = research(client, repo, question, limit, encoding)
    assert tight["id"] == older.id and cited_lines(tight, repo) == cited_lines(first, repo)
