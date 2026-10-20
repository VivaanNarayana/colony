"""Read-only research tools shared by both benchmark arms."""

import argparse
import hashlib
import threading
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

parser = argparse.ArgumentParser()
parser.add_argument("root", type=Path)
parser.add_argument("--max-calls", type=int)
args = parser.parse_args()
ROOT = args.root.resolve()
server = FastMCP("Repository")
used_calls = 0
budget_lock = threading.Lock()


def take_call():
    global used_calls
    with budget_lock:
        if args.max_calls is not None and used_calls >= args.max_calls:
            raise ValueError("Research tool budget exhausted; answer from existing evidence")
        used_calls += 1


@server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def read_file(path: str) -> dict:
    """Read README.md, pyproject.toml, or src/colony/*.py; return content and actual SHA256."""
    take_call()
    target = (ROOT / path).resolve()
    target.relative_to(ROOT)
    allowed = {
        ROOT / "README.md",
        ROOT / "pyproject.toml",
        *list((ROOT / "src/colony").glob("*.py")),
    }
    if target not in {p.resolve() for p in allowed}:
        raise ValueError("Outside research corpus")
    data = target.read_bytes()
    return {
        "path": path,
        "sha256": hashlib.sha256(data).hexdigest(),
        "content": data.decode("utf-8"),
    }


@server.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def search(text: str) -> list[dict]:
    """Literal case-insensitive search of README.md, pyproject.toml, and src/colony/*.py; max 50 lines."""
    take_call()
    results = []
    for path in [
        ROOT / "README.md",
        ROOT / "pyproject.toml",
        *sorted((ROOT / "src/colony").glob("*.py")),
    ]:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if text.lower() in line.lower():
                results.append({"path": path.relative_to(ROOT).as_posix(), "line": n, "text": line})
                if len(results) >= 50:
                    return results
    return results


server.run(transport="stdio")
