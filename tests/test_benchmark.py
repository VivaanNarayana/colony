import asyncio
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

spec = importlib.util.spec_from_file_location(
    "benchmark", Path(__file__).parents[1] / "scripts/benchmark.py"
)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def test_research_adapter_enforces_one_shared_call_budget(tmp_path):
    (tmp_path / "README.md").write_text("A private fixture fact.\n")
    script = Path(__file__).parents[1] / "scripts/benchmark_repo_mcp.py"

    async def check():
        params = StdioServerParameters(
            command=sys.executable, args=[str(script), str(tmp_path), "--max-calls", "1"]
        )
        async with stdio_client(params) as streams, ClientSession(*streams) as session:
            await session.initialize()
            first = await session.call_tool("read_file", {"path": "README.md"})
            assert not first.isError and "private fixture fact" in first.content[0].text
            for tool, arguments in [
                ("search", {"text": "fact"}),
                ("read_file", {"path": "README.md"}),
            ]:
                denied = await session.call_tool(tool, arguments)
                assert denied.isError
                assert "Research tool budget exhausted" in denied.content[0].text
                assert "private fixture fact" not in denied.content[0].text

    asyncio.run(asyncio.wait_for(check(), timeout=20))


@pytest.mark.parametrize("selected", ["main", "feature"])
def test_live_launcher_pins_imports_despite_conflicting_pythonpath(tmp_path, monkeypatch, selected):
    scripts = Path(__file__).parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    import benchmark_lookup_live as paired

    sources = {}
    for name in ["main", "feature"]:
        source = tmp_path / name / "src"
        package = source / "colony"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text(f"MARKER = {name!r}\n")
        (package / "mcp.py").write_text("# MCP implementation marker\n")
        (package / "cli.py").write_text(
            "from colony import MARKER\ndef main():\n    print(MARKER)\n"
        )
        sources[name] = source
    launcher = tmp_path / "runner/implementation.py"
    paired.write_bootstrap(sources[selected], launcher, selected)
    result = subprocess.run(
        [sys.executable, str(launcher), "mcp"],
        env={**os.environ, "PYTHONPATH": str(sources["feature"])},
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    assert result.stdout.strip() == selected
    proof = json.loads(launcher.with_name("mcp-implementation.json").read_text())
    assert proof["revision"] == selected
    assert Path(proof["module_paths"]["colony.mcp"]).is_relative_to(sources[selected])


def test_task_set_and_paired_metrics():
    assert len(benchmark.tasks()) == 24
    rows = [
        {
            "task_id": "one",
            "mode": mode,
            "tokens": tokens,
            "seconds": 10,
            "tool_calls": 4,
            "ask_calls": 2,
            "matches": 1,
            "validated": 1,
            "wrong_reused": 0,
            "correct": True,
        }
        for mode, tokens in [("baseline", 100), ("colony", 70)]
    ]
    result = benchmark.report(rows)
    assert result["reuse_rate"] == 0.5
    assert result["validated_hit_rate"] == 1
    assert result["savings"]["tokens"]["reduction"] == pytest.approx(0.3)
    with pytest.raises(ValueError):
        benchmark.report(rows[:1])
    with pytest.raises(ValueError):
        benchmark.report(rows + rows)
    with pytest.raises(ValueError):
        benchmark.report([{**r, "tokens": -1} for r in rows])


def test_wrong_topic_reuse_counts_against_raw_returns_without_becoming_useful():
    rows = [
        dict(
            task_id="one",
            mode=mode,
            tokens=100,
            seconds=1,
            tool_calls=2,
            ask_calls=2,
            matches=0,
            validated=0,
            raw_returns=2,
            wrong_reused=int(mode == "colony"),
            correct=mode == "baseline",
        )
        for mode in ["baseline", "colony"]
    ]
    result = benchmark.report(rows)
    assert result["reuse_rate"] == 0
    assert result["wrong_reuse_rate"] == 0.5
    assert result["correct_tasks"]["colony"] == 0
