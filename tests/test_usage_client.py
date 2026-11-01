import asyncio
import json
import os
import subprocess
import time
import uuid

import httpx
import pytest
from cryptography.fernet import Fernet

from colony.cli import execute, parser
from colony.client import Client, MemoryUnavailable
from colony.mcp import create_mcp
from colony.models import Grant, Scope, Trail
from colony.security import generate_keys, issue, private_write
from colony.usage import UsageJournal
from colony.usage_report import make_report


@pytest.fixture
def observed_client(tmp_path):
    admin, home = tmp_path / "issuer", tmp_path / "home"
    generate_keys(admin)
    grant = Grant(
        member_id="local-client",
        name="Local client",
        server="http://127.0.0.1:7331",
        expires_at=int(time.time()) + 3600,
        scopes=[Scope(namespace="team", push=True, pull=True)],
    )
    private_write(
        home / "identity.json", issue(admin / "issuer.pem", grant).model_dump_json().encode()
    )
    private_write(home / "issuer.pub", (admin / "issuer.pub").read_bytes())
    private_write(home / "queue.key", Fernet.generate_key())
    return Client(home)


def test_default_off_and_enabled_do_not_change_results_or_requests(observed_client, monkeypatch):
    client = observed_client
    result = {"hit": True, "status": "validated", "content": "answer-sentinel-private"}
    requests = []

    def request(route, payload):
        requests.append((route, payload))
        return result

    monkeypatch.setattr(client, "request", request)
    assert client.ask("question-sentinel-private", "team") is result
    assert not (client.home / "usage").exists()
    client.usage.enable()
    assert client.ask("question-sentinel-private", "team") is result
    assert requests[0] == requests[1]
    events = client.usage.events()
    assert len(events) == 1 and events[0]["outcome"] == "answer"
    assert "sentinel-private" not in json.dumps(events)
    assert "sentinel-private" not in json.dumps(make_report(client.home))
    assert set(result) == {"hit", "status", "content"}


@pytest.mark.parametrize(
    ("response", "outcome"),
    [
        ({"hit": True, "status": "validated"}, "answer"),
        ({"hit": True, "status": "validated", "match_kind": "evidence"}, "evidence"),
        ({"hit": False}, "miss"),
        ({"hit": False, "status": "stale"}, "stale"),
        ({"hit": False, "status": "unverified"}, "unverified"),
        ({"hit": False, "reason": "server_unavailable"}, "unavailable"),
    ],
)
def test_lookup_outcomes_are_not_conflated(observed_client, monkeypatch, response, outcome):
    client = observed_client
    client.usage.enable()
    monkeypatch.setattr(client, "request", lambda *args: response)
    assert client.ask("q") is response
    assert client.usage.events()[0]["outcome"] == outcome


def test_queue_and_retry_are_attempts_of_one_logical_publication(observed_client, monkeypatch):
    client = observed_client
    client.usage.enable()

    def offline(*args):
        raise httpx.ConnectError("private-error-sentinel")

    monkeypatch.setattr(client, "request", offline)
    trail = Trail(
        id=uuid.uuid4().hex, namespace="team", question="private-question", content="secret"
    )
    assert client.push(trail) == {"id": trail.id, "stored": False, "queued": True}
    assert client.retry() == {"sent": 0, "pending": True}
    monkeypatch.setattr(client, "request", lambda *args: {"stored": True, "id": trail.id})
    assert client.retry() == {"sent": 1, "pending": False}
    events = client.usage.events()
    assert [e["outcome"] for e in events] == ["queued", "unavailable", "accepted"]
    assert [e["operation"] for e in events] == ["publish", "retry", "retry"]
    assert len({e["logical_id"] for e in events}) == 1
    assert trail.id not in json.dumps(events)
    report = make_report(client.home)
    assert report["activity"]["observed_request_attempts"] == 3
    assert report["activity"]["identified_logical_operations"] == 1
    assert not list((client.home / "queue").glob("*.enc"))


def test_logger_failures_preserve_success_and_original_error(observed_client, monkeypatch, capsys):
    client = observed_client
    client.usage.enable()
    expected = {"hit": False}
    monkeypatch.setattr(client, "request", lambda *args: expected)

    def broken(**kwargs):
        raise OSError("private-logger-sentinel")

    monkeypatch.setattr(client.usage, "record", broken)
    assert client.ask("private-question") is expected
    assert client.usage.status()["gap"] is True
    error = httpx.HTTPStatusError(
        "private-server-sentinel",
        request=httpx.Request("POST", "https://example.invalid"),
        response=httpx.Response(403),
    )

    def denied(*args):
        raise error

    monkeypatch.setattr(client, "request", denied)
    with pytest.raises(httpx.HTTPStatusError) as exc:
        client.ask("private-question")
    assert exc.value is error
    warning = capsys.readouterr().err
    assert "usage may be incomplete" in warning
    assert "sentinel" not in warning


def test_uncertain_mutation_replay_has_one_logical_id(observed_client, monkeypatch):
    client = observed_client
    client.usage.enable()
    op_id, trail_id = "a" * 32, "b" * 32
    monkeypatch.setattr(client, "request", lambda *args: {"malformed": "secret"})
    with pytest.raises(MemoryUnavailable) as error:
        client.memory_feedback(trail_id, "team", verdict="helpful", operation_id=op_id)
    assert error.value.operation_id == op_id

    def acknowledged(route, payload):
        return {
            "id": payload["trail_id"],
            "operation_id": payload["operation_id"],
            "verdict": payload["verdict"],
            "replayed": True,
            "memory": {
                "state": "active",
                "revision": 0,
                "replacement_id": None,
                "feedback": {"helpful": 1, "incorrect": 0, "outdated": 0},
            },
        }

    monkeypatch.setattr(client, "request", acknowledged)
    assert client.memory_feedback(trail_id, "team", verdict="helpful", operation_id=op_id)[
        "replayed"
    ]
    events = client.usage.events()
    assert [e["outcome"] for e in events] == ["unknown", "accepted"]
    assert len({e["logical_id"] for e in events}) == 1
    assert op_id not in json.dumps(events)
    assert make_report(client.home)["quality"]["assessed_hits"] == 0


def test_memory_action_is_recorded_and_permission_errors_remain_errors(
    observed_client, monkeypatch
):
    client = observed_client
    client.usage.enable()
    with pytest.raises(ValueError):
        client.push(Trail(id=uuid.uuid4().hex, namespace="private", question="q", content="c"))
    assert client.usage.events()[-1]["outcome"] == "denied"
    with pytest.raises(ValueError):
        client.memory_change("a" * 32, "team", expected_revision=0, action="invalid")
    assert client.usage.events()[-1]["operation"] == "change"
    assert client.usage.events()[-1]["outcome"] == "invalid"


def test_mcp_uses_same_local_collection_without_changing_tool_schema(observed_client, monkeypatch):
    client = observed_client
    monkeypatch.setattr(Client, "request", lambda *args: {"hit": False})
    server = create_mcp(client.home)
    before = asyncio.run(server.list_tools())
    client.usage.enable()
    asyncio.run(server.call_tool("ask", {"question": "private-question"}))
    after = asyncio.run(server.list_tools())
    assert [t.model_dump() for t in before] == [t.model_dump() for t in after]
    assert UsageJournal(client.home).events()[0]["outcome"] == "miss"


def test_cli_report_and_review_need_no_identity(tmp_path):
    home = tmp_path / "unjoined"

    def command(*args):
        return execute(parser().parse_args(["--home", str(home), *args]))

    assert command("usage", "status")["enabled"] is False
    assert not home.exists()
    assert command("report", "--format", "json")["activity"]["observed_request_attempts"] == 0
    assert not home.exists()
    command("usage", "enable", "--retention-days", "7", "--max-events", "20")
    journal = UsageJournal(home)
    event_id = journal.record(operation="lookup", outcome="evidence", duration_ms=2)
    command("usage", "review", event_id, "--verdict", "useful")
    report = command("report", "--format", "json")
    assert report["quality"]["assessed_hits"] == 1
    assert report["quality"]["self_reported_usefulness"]["fraction"] == 1
    assert report["model_usage"]["token_savings"] is None
    output = tmp_path / "report.json"
    assert command("report", "--format", "json", "--output", str(output)) is None
    assert json.loads(output.read_text())["contract"] == "colony.usage-report/1"
    with pytest.raises(FileExistsError):
        command("report", "--format", "json", "--output", str(output))
    assert command("usage", "disable")["enabled"] is False
    assert command("usage", "purge")["purged"] == 1


def test_cli_report_refuses_linked_output_ancestor(tmp_path):
    outside, link = tmp_path / "outside", tmp_path / "linked"
    outside.mkdir()
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            check=True,
            capture_output=True,
        )
    else:
        link.symlink_to(outside, target_is_directory=True)
    args = parser().parse_args(
        [
            "--home",
            str(tmp_path / "unjoined"),
            "report",
            "--format",
            "json",
            "--output",
            str(link / "nested" / "report.json"),
        ]
    )
    with pytest.raises(ValueError):
        execute(args)
    assert not list(outside.iterdir())


def test_report_compare_preserves_options_before_or_after_subcommand(tmp_path, monkeypatch):
    from colony import product_value

    monkeypatch.setattr(product_value, "comparison_report", lambda *args: {"contract": "test"})
    monkeypatch.setattr(product_value, "render_comparison", lambda report: "fixed text")
    for index, prefix in enumerate((True, False)):
        output = tmp_path / str(index) / "report.json"
        options = ["--format", "json", "--output", str(output)]
        compare = ["compare", "--manifest", "manifest.json", "--logs", "logs"]
        words = ["report", *(options + compare if prefix else compare + options)]
        assert execute(parser().parse_args(words)) is None
        assert json.loads(output.read_text()) == {"contract": "test"}


@pytest.mark.parametrize("status", [[], {}, 123])
def test_malformed_lookup_status_is_visible_unknown_not_silent_gap(
    observed_client, monkeypatch, status
):
    client = observed_client
    client.usage.enable()
    response = {"hit": False, "status": status}
    monkeypatch.setattr(client, "request", lambda *args: response)
    assert client.ask("q") is response
    assert client.usage.events()[0]["outcome"] == "unknown"
    assert client.usage.status()["health"] == "healthy"


def test_unhashable_memory_action_retains_invalid_observation(observed_client):
    client = observed_client
    client.usage.enable()
    with pytest.raises(ValueError):
        client.memory_change("a" * 32, "team", expected_revision=0, action=[])
    event = client.usage.events()[0]
    assert (event["operation"], event["outcome"]) == ("change", "invalid")
    assert client.usage.status()["health"] == "healthy"


@pytest.mark.parametrize("bound", ["--since", "--until"])
@pytest.mark.parametrize("compare", [False, True])
def test_cli_explicit_empty_bound_is_rejected_not_unbounded(tmp_path, bound, compare):
    words = ["--home", str(tmp_path / "unjoined"), "report", bound, ""]
    if compare:
        words += ["compare", "--manifest", "unused", "--logs", "unused"]
    with pytest.raises(ValueError):
        execute(parser().parse_args(words))
