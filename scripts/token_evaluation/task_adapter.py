"""Evaluator-only fixture boundary around the installed task-scoped adapter.

One process per task. No model invocation; no alternative product implementation.
"""
import argparse
import json
from pathlib import Path

from mcp.types import CallToolResult

from colony.client import Client
from colony.evidence_refs import EvidenceError
from colony.task_mcp import TaskMCP
from colony.workflow import Workflow

try:
    from .research import Research
except ImportError:  # Launched as a pinned absolute script with target Python -I.
    import importlib.util
    spec = importlib.util.spec_from_file_location('evaluation_research', Path(__file__).with_name('research.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    Research = module.Research


def create_server(home, repo, question, files, max_calls, *, read_only=False,
                  direct_path=None, remember=False, context_budget=None):
    research = Research(repo, max_calls, files)

    class BoundedTaskMCP(TaskMCP):
        async def call_tool(self, name, arguments):
            # Charge before argument validation; failures and continuation pages count.
            if name in {'read', 'fixture_files', 'fixture_search'}:
                research._charge()
            token = research.external_charge.set(True)
            try:
                return await super().call_tool(name, arguments)
            finally:
                research.external_charge.reset(token)

    options = {} if context_budget is None else {'context_budget': context_budget}
    server = BoundedTaskMCP(Workflow(Client(home), 'bench', repo, read_only=read_only), question,
                            direct_path=direct_path, remember=remember, **options)
    original_read = server.evidence.read

    def read(path, start=1, end=None, *, offset=0):
        try:
            research._path(path)
        except (OSError, ValueError, TypeError, RuntimeError):
            raise EvidenceError('Path is outside fixture files') from None
        return original_read(path, start, end, offset=offset)

    server.evidence.read = read

    @server.tool(structured_output=False)
    def fixture_files() -> CallToolResult:
        """List allowed source paths; each attempt consumes one research call."""
        return CallToolResult(content=research.files())

    @server.tool(structured_output=False)
    def fixture_search(query: str) -> CallToolResult:
        """Search literal source text; each attempt consumes one research call."""
        return CallToolResult(content=research.search(query))

    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('home', 'repo'):
        parser.add_argument('--' + flag, type=Path, required=True)
    parser.add_argument('--question', required=True)
    parser.add_argument('--files', type=json.loads, required=True)
    parser.add_argument('--max-calls', type=int, choices=range(1, 13), required=True)
    parser.add_argument('--direct-path')
    parser.add_argument('--remember', action='store_true')
    parser.add_argument('--read-only', action='store_true')
    parser.add_argument('--context-budget', type=int)
    parser.add_argument('--context-encoding', choices=('utf8_bytes', 'o200k_base'))
    args = parser.parse_args()
    budget = None
    if args.context_budget is not None or args.context_encoding is not None:
        # Runtime capability check, not an implementation of adaptive behavior.
        from colony.cli import _context_budget
        budget = _context_budget(args)
    server = create_server(args.home, args.repo, args.question, args.files, args.max_calls,
                           direct_path=args.direct_path, remember=args.remember,
                           read_only=args.read_only, context_budget=budget)
    try:
        server.run(transport='stdio')
    finally:
        server.evidence.close()


if __name__ == '__main__':
    main()
