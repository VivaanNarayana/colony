import json

import pytest
from pydantic import TypeAdapter, ValidationError

from scripts.token_evaluation.research import Bound, Research


def test_read_eof_and_clamp(tmp_path):
    (tmp_path / "a").write_text("\n".join(str(i) for i in range(200)))
    research = Research(tmp_path)
    assert json.loads(research.read("a", 201)[0].text)["content"] == ""
    assert len(json.loads(research.read("a", lines=1000)[0].text)["content"].splitlines()) == 120
    with pytest.raises(ValueError):
        research.read("a", start=True)


def test_strict_mcp_line_bound():
    for value in (True, "2", 121, 0):
        with pytest.raises(ValidationError):
            TypeAdapter(Bound).validate_python(value)


def test_search_zero_one_multi_blocks_and_budget(tmp_path):
    (tmp_path / "a").write_text("one\ncommon")
    (tmp_path / "b").write_text("common")
    research = Research(tmp_path, 3)
    assert research.search("missing") == []
    assert len(research.search("one")) == 1
    assert len(research.search("common")) == 2
    with pytest.raises(ValueError, match="limit"):
        research.files()


def test_outside_paths(tmp_path):
    research = Research(tmp_path)
    for path in ("../outside", str(tmp_path.parent)):
        with pytest.raises((ValueError, FileNotFoundError)):
            research.read(path)


def test_private_and_large_files_are_not_exposed(tmp_path):
    (tmp_path / "AGENTS.md").write_text("private guidance")
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text("private")
    (tmp_path / "large").write_bytes(b"x" * 1048577)
    (tmp_path / "source.py").write_text("source")
    research = Research(tmp_path)
    assert research.files()[0].text == "source.py"
    for name in ("AGENTS.md", ".codex/config.toml", "large"):
        with pytest.raises(ValueError):
            research.read(name)


def test_allowlist_excludes_unlisted_source(tmp_path):
    (tmp_path / "allowed.py").write_text("common")
    (tmp_path / "unlisted.py").write_text("common")
    research = Research(tmp_path, files=["allowed.py"])
    assert research.files()[0].text == "allowed.py"
    assert len(research.search("common")) == 1
    with pytest.raises(ValueError):
        research.read("unlisted.py")


def test_malformed_mcp_calls_consume_allowance(tmp_path):
    import asyncio

    from mcp.server.fastmcp.exceptions import ToolError

    from scripts.token_evaluation.research import create_server

    server = create_server(tmp_path, max_calls=1, files=[])
    async def control():
        with pytest.raises(ToolError):
            await server.call_tool("fixture_read", {"path": "a", "lines": True})
        with pytest.raises(ValueError, match="limit"):
            await server.call_tool("fixture_files", {})
    asyncio.run(control())
