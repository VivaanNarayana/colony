from concurrent.futures import ThreadPoolExecutor

from conftest import auth

from colony.models import Trail
from colony.retrieval_diagnostics import record_retrieval, trace_retrieval
from colony.validation import snapshot


def test_capture_is_opt_in_nested_and_restored_after_exception():
    record_retrieval("disabled", count=1)
    with trace_retrieval() as outer:
        record_retrieval("outer", count=1)
        try:
            with trace_retrieval() as inner:
                record_retrieval("inner", accepted=False)
                raise ValueError("diagnostic caller failed")
        except ValueError:
            pass
        record_retrieval("outer_again", count=2)
    record_retrieval("disabled_again", count=3)
    assert [event["stage"] for event in outer.events] == ["outer", "outer_again"]
    assert inner.events == [{"stage": "inner", "accepted": False}]


def test_metadata_capture_is_bounded_and_excludes_payloads():
    with trace_retrieval() as trace:
        for _ in range(300):
            record_retrieval(
                "gate", accepted=False, reason="relevance", candidate_id="finding",
                question="private query", content="private answer", path="private/file",
                credentials={"secret": "private"}, count=["not scalar"],
            )
    assert len(trace.events) == 256
    assert trace.dropped_events == 44
    assert trace.events[0] == {
        "stage": "gate", "accepted": False, "reason": "relevance", "candidate_id": "finding",
    }


def test_captures_do_not_cross_threads():
    def capture(name):
        with trace_retrieval() as trace:
            record_retrieval("thread", candidate_id=name)
        return trace.events

    with trace_retrieval() as parent, ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(capture, ["one", "two"]))
        record_retrieval("parent", count=1)
    assert results == [
        [{"stage": "thread", "candidate_id": "one"}],
        [{"stage": "thread", "candidate_id": "two"}],
    ]
    assert parent.events == [{"stage": "parent", "count": 1}]


def test_actual_store_gates_are_observable_without_changing_the_response(team):
    app, api, repo, _, key = team
    trail = Trail(
        id="d" * 32, namespace="team", question="Configure the build timeout",
        content="Set TIMEOUT = 30 for the build.", sources=[snapshot(repo, "build.py")],
    )
    api.post("/trails", json=trail.model_dump(), headers=auth(key)).raise_for_status()
    question = "How do I rotate the database password?"
    ordinary = app.state.store.ask(question, ["team"], 604800, 0.60)
    with trace_retrieval() as trace:
        diagnosed = app.state.store.ask(question, ["team"], 604800, 0.60)
    assert diagnosed == ordinary
    assert diagnosed["hit"] is False
    assert trace.events, "Capture actual gate decisions, not a separate approximate calculation"
    assert any(event.get("accepted") is False for event in trace.events)
    assert all("question" not in event and "content" not in event for event in trace.events)
    assert "diagnostics" not in diagnosed and "trace" not in diagnosed


def test_trace_explains_exact_question_bypass_without_query_text(team):
    app, api, repo, _, key = team
    question = "Configure the build timeout"
    trail = Trail(
        id="f" * 32, namespace="team", question=question,
        content="The configured TIMEOUT is 30.", sources=[snapshot(repo, "build.py")],
    )
    api.post("/trails", json=trail.model_dump(), headers=auth(key)).raise_for_status()
    with trace_retrieval() as trace:
        result = app.state.store.ask(question, ["team"], 604800, 0.99)
    assert result["hit"]
    gate = next(event for event in trace.events if event["stage"] == "answer_gate")
    assert gate["exact_question"] is True
    assert gate["threshold"] == 0.99 and gate["question_threshold"] == 0.70
    assert gate["procedure_required"] is True
    assert gate["relevance_pass"] and gate["question_pass"] and gate["procedure_pass"]
    assert gate["accepted"] is True
    assert "question" not in gate
