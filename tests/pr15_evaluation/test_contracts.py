"""Provider-free controls for the exported evaluator, never native savings evidence."""

import asyncio
import copy
import hashlib
import json
import shutil

import pytest

from scripts import pr15_evaluation


def test_pinned_export_is_complete_and_contains_no_application(evaluator):
    receipt = evaluator.receipt
    assert receipt["base"] == pr15_evaluation.BASE
    assert receipt["native_sessions_started"] == 0 and receipt["native_evidence"] is False
    for name, digest in receipt["files"].items():
        assert not name.startswith("src/")
        assert hashlib.sha256((evaluator.root / name).read_bytes()).hexdigest() == digest
    assert not (evaluator.root / ".git").exists()


def test_export_refuses_existing_and_repository_outputs(tmp_path):
    existing = tmp_path / "existing"
    existing.mkdir()
    marker = existing / "preserve.txt"
    marker.write_text("retained", encoding="utf-8")
    with pytest.raises(ValueError, match="new and outside"):
        pr15_evaluation.prepare(existing)
    with pytest.raises(ValueError, match="new and outside"):
        pr15_evaluation.prepare(pr15_evaluation.ROOT / "forbidden-export")
    assert marker.read_text(encoding="utf-8") == "retained"


def test_changed_patch_rejected_before_export(tmp_path, monkeypatch):
    overlay = tmp_path / "overlay"
    shutil.copytree(pr15_evaluation.OVERLAY, overlay)
    with (overlay / "correction.patch").open("ab") as stream:
        stream.write(b"corruption")
    monkeypatch.setattr(pr15_evaluation, "OVERLAY", overlay)
    output = tmp_path / "export"
    with pytest.raises(ValueError, match="manifest/patch"):
        pr15_evaluation.prepare(output)
    assert not output.exists()


def test_actual_tool_metadata_and_literal_search(evaluator, tmp_path):
    (tmp_path / "a.py").write_text("VALUE = 3\n", encoding="utf-8")
    server = evaluator.research.create_server(tmp_path)

    async def check():
        tools = {tool.name: tool for tool in await server.list_tools()}
        assert all(tool.description for tool in tools.values())
        assert "literal" in tools["fixture_search"].description
        assert "not regex" in tools["fixture_search"].description
        assert tools["fixture_search"].inputSchema["properties"]["query"]["minLength"] == 1
        assert tools["fixture_read"].inputSchema["properties"]["lines"]["maximum"] == 120
        result = await server.call_tool("fixture_search", {"query": "VALUE.*3"})
        assert (result[0] if isinstance(result, tuple) else result) == []
        result = await server.call_tool("fixture_search", {"query": "VALUE"})
        if isinstance(result, tuple):
            result = result[0]
        assert json.loads(result[0].text)["path"] == "a.py"
    asyncio.run(check())


@pytest.mark.parametrize("tool,args", [
    ("fixture_read", {"path": "a.py", "lines": 160}),
    ("fixture_read", {"path": "a.py", "lines": True}),
    ("fixture_read", {"path": "a.py", "start": 0}),
    ("fixture_search", {"query": ""}),
])
def test_invalid_attempts_are_charged(evaluator, tmp_path, tool, args):
    from mcp.server.fastmcp.exceptions import ToolError

    (tmp_path / "a.py").write_text("VALUE = 3\n", encoding="utf-8")
    server = evaluator.research.create_server(tmp_path, max_calls=2)

    async def check():
        with pytest.raises(ToolError):
            await server.call_tool(tool, args)
        assert await server.call_tool("fixture_files", {})
        with pytest.raises((ToolError, ValueError), match="call limit"):
            await server.call_tool("fixture_files", {})
    asyncio.run(check())


def context(tmp_path, text, name="a.py"):
    raw = text.encode("utf-8")
    (tmp_path / name).write_bytes(raw)
    return {name: hashlib.sha256(raw).hexdigest()}, [{"path": name, "start": 1,
                                                    "end": len(text.splitlines())}]


def test_structural_blank_omission_but_not_missing_logic(evaluator, tmp_path):
    files, required = context(tmp_path, "alpha = 1\n\nbeta = 2\n")
    spans = [{"path": "a.py", "start": 1, "end": 1},
             {"path": "a.py", "start": 3, "end": 3}]
    covers = evaluator.evidence.covers
    assert covers(spans, required, root=tmp_path, files=files)
    assert not covers(spans[:1], required, root=tmp_path, files=files)
    assert not covers(spans, required)  # No source context preserves strict legacy coverage.
    assert not covers(spans, required, root=tmp_path, files={"a.py": "0" * 64})
    (tmp_path / "a.py").write_text("alpha = 9\n\nbeta = 2\n", encoding="utf-8")
    assert not covers(spans, required, root=tmp_path, files=files)


@pytest.mark.parametrize("name,text", [
    ("a.py", 'value = """first\n\nlast"""\n'),
    ("a.py", 'value = """unterminated\n\nlast\n'),
    ("a.txt", "first\n\nlast\n"),
])
def test_meaningful_or_unclassified_whitespace_stays_required(evaluator, tmp_path, name, text):
    files, required = context(tmp_path, text, name)
    spans = [{"path": name, "start": 1, "end": 1},
             {"path": name, "start": 3, "end": len(text.splitlines())}]
    assert not evaluator.evidence.covers(spans, required, root=tmp_path, files=files)


def test_blank_only_oracle_cannot_prove_reuse(evaluator, tmp_path):
    files, required = context(tmp_path, "\n\n")
    assert not evaluator.evidence.covers([], required, root=tmp_path, files=files)


def test_source_content_is_never_normalized_for_verification(evaluator, tmp_path):
    files, _ = context(tmp_path, "if True:\n    value = 1\n")
    read = {"path": "a.py", "sha256": files["a.py"], "start": 1,
            "content": "if True:\n    value = 1"}
    assert evaluator.evidence.verify_current_reads([read], tmp_path, files)
    forged = copy.deepcopy(read)
    forged["content"] = "if True:\nvalue = 1"
    assert not evaluator.evidence.verify_current_reads([forged], tmp_path, files)


def test_default_oracle_uses_defining_assignment_without_changing_facts(evaluator):
    for workload in evaluator.catalog["workloads"]:
        for task in workload["tasks"]:
            if task["id"] in {"partial_learning", "direct_read", "insufficient"}:
                first = task["oracle"]["citations"][0]
                assert first["start"] == first["end"] == 1
                source = evaluator.product.FIXTURES / workload["id"] / task["snapshot"]
                if task["id"] == "insufficient":
                    assert evaluator.product.source_gap_facts(source, workload["id"]) == task["oracle"]["facts"]
                else:
                    import ast
                    definition = ast.parse((source / first["path"]).read_text(
                        encoding="utf-8")).body[0]
                    assert (definition.lineno, definition.end_lineno) == (
                        first["start"], first["end"]
                    )
                    values = ast.literal_eval(definition.value)
                    key = "timeout_ms" if workload["id"] == "gateway" else "delay_ms"
                    assert task["oracle"]["facts"] == {"default_" + key: values[key]}


def test_fresh_protocol_binds_patched_collector_and_rejects_historical_pins(evaluator):
    import importlib

    protocol = importlib.import_module("scripts.token_evaluation.protocol")
    fresh = {"evaluator_overlay": dict(protocol.EVALUATOR_OVERLAY),
             "collector": {"files": protocol.active_collector()}}
    protocol.verify_active_collector(fresh)
    historical = copy.deepcopy(fresh)
    historical.pop("evaluator_overlay")
    with pytest.raises(ValueError, match="historical trials"):
        protocol.verify_active_collector(historical)
    changed = copy.deepcopy(fresh)
    name = next(iter(changed["collector"]["files"]))
    changed["collector"]["files"][name] = "0" * 64
    with pytest.raises(ValueError, match="exact patched collector"):
        protocol.verify_active_collector(changed)


def test_native_report_refuses_unversioned_evidence_before_scoring(
    evaluator, tmp_path, monkeypatch
):
    import importlib

    report = importlib.import_module("scripts.token_evaluation.report")
    (tmp_path / "collection.json").write_text(
        json.dumps({"native_evidence": True}), encoding="utf-8"
    )
    monkeypatch.setattr(report, "_load", lambda _: ({}, {}, []))
    with pytest.raises(ValueError, match="historical trials"):
        report.report(tmp_path, tmp_path / "no-quality.json", tmp_path / "no-report.json")
    assert not (tmp_path / "no-report.json").exists()
