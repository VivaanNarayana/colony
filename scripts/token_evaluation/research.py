"""Bounded, fixture-only research MCP. No model or network provider is used."""
from __future__ import annotations

import argparse
import hashlib
import json
from contextvars import ContextVar
from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent
from pydantic import Field, StrictInt

Bound = Annotated[StrictInt, Field(
    ge=1, le=120,
    description="Number of lines, 1-120; omit for a normal small-file read (default 120).",
)]


class Research:
    def __init__(self, root: Path, max_calls: int = 6, files=None):
        self.root = root.resolve(strict=True)
        self.max_calls = max_calls
        self.calls = 0
        self.allowed_files = None if files is None else {Path(value).as_posix() for value in files}
        self.external_charge = ContextVar("research_external_charge", default=False)

    def _charge(self):
        if self.external_charge.get():
            return
        if self.calls >= self.max_calls:
            raise ValueError("Research call limit reached")
        self.calls += 1

    def _allowed(self, path):
        relative = path.relative_to(self.root)
        if self.allowed_files is not None and relative.as_posix() not in self.allowed_files:
            return False
        if any(part.startswith(".") or part in {"control", "AGENTS.md", "AGENTS.override.md"} for part in relative.parts):
            return False
        if path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"} or path.name in {"auth.json", "credentials.json"}:
            return False
        return not any((p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction())) for p in (path, *path.parents) if p.is_relative_to(self.root)) and path.is_file() and path.stat().st_size <= 1048576 and path.resolve().is_relative_to(self.root)

    def _path(self, relative: str) -> Path:
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise ValueError("Only relative fixture paths are allowed")
        candidate = (self.root / relative).resolve(strict=True)
        original = self.root / relative
        if any((p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction())) for p in (original, *original.parents) if p.is_relative_to(self.root)) or not candidate.is_relative_to(self.root) or not self._allowed(candidate):
            raise ValueError("Path is outside fixture files")
        return candidate

    def files(self) -> list[TextContent]:
        self._charge()
        names = sorted(str(p.relative_to(self.root)).replace("\\", "/") for p in self.root.rglob("*") if self._allowed(p))
        return [TextContent(type="text", text="\n".join(names))]

    def read(self, path: str, start: int = 1, lines: int = 120) -> list[TextContent]:
        self._charge()
        if type(start) is not int or type(lines) is not int or start < 1 or lines < 1:
            raise ValueError("Positive integer line bounds required")
        lines = min(lines, 120)
        source = self._path(path)
        raw = source.read_bytes()
        content = raw.decode("utf-8").splitlines()
        end = min(len(content), start - 1 + lines)
        return [TextContent(type="text", text=json.dumps({"path": path, "sha256": hashlib.sha256(raw).hexdigest(), "start": start, "end": end, "content": "\n".join(content[start - 1:end])}))]

    def search(self, query: str) -> list[TextContent]:
        self._charge()
        if not isinstance(query, str) or not query:
            raise ValueError("A nonempty literal query is required")
        blocks = []
        count = 0
        for path in sorted(self.root.rglob("*")):
            if not self._allowed(path):
                continue
            matches = []
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if query in line:
                    matches.append(f"{number}: {line}")
                    count += 1
                    if count == 120:
                        break
            if matches:
                blocks.append(TextContent(type="text", text=json.dumps({"path": path.relative_to(self.root).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "matches": matches})))
            if count == 120:
                break
        return blocks


def create_server(root: Path, max_calls: int = 6, files=None) -> FastMCP:
    research = Research(root, max_calls, files)

    class ChargedMCP(FastMCP):
        async def call_tool(self, name, arguments):
            research._charge()
            token = research.external_charge.set(True)
            try:
                return await super().call_tool(name, arguments)
            finally:
                research.external_charge.reset(token)

    server = ChargedMCP("evaluation-fixture-research")

    @server.tool(structured_output=False)
    def fixture_files() -> CallToolResult:
        """List allowed repository-relative filenames; read a known file directly.

        Every attempt, including failures, consumes one repository research call.
        """
        return CallToolResult(content=research.files())

    @server.tool(structured_output=False)
    def fixture_read(
        path: Annotated[str, Field(description="Allowed repository-relative filename.")],
        start: Annotated[StrictInt, Field(ge=1, description="First line, 1-based.")] = 1,
        lines: Bound = 120,
    ) -> CallToolResult:
        """Read exact file text, line coordinates and the full file's SHA-256.

        Omit lines for a small-file read; the default and maximum are 120 lines.
        End-of-file is clamped. Every attempt, even an invalid or failed read,
        consumes one repository research call.
        """
        return CallToolResult(content=research.read(path, start, lines))

    @server.tool(structured_output=False)
    def fixture_search(query: Annotated[str, Field(
        min_length=1, description="Nonempty, case-sensitive literal substring of file contents.",
    )]) -> CallToolResult:
        """Find a case-sensitive literal substring in allowed file contents.

        This is not regex, semantic or filename search. No matches returns no
        blocks. Discover unknown paths with fixture_files, then read full context
        with fixture_read. Every attempt consumes one repository research call.
        """
        return CallToolResult(content=research.search(query))

    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--max-calls", type=int, default=6, choices=range(1, 13))
    parser.add_argument("--files", type=json.loads, default=None)
    args = parser.parse_args()
    create_server(args.root, args.max_calls, args.files).run(transport="stdio")
