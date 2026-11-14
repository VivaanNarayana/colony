"""Policy delivery/size contracts, not claims about agent behavior or savings."""
import asyncio
import copy
import json
import os

import pytest
from test_workflow import identity as identity
from test_workflow_policy_envelopes import FROZEN_6AE5F6F_LOOKUP, FROZEN_6AE5F6F_PUBLICATION

from colony.cli import parser
from colony.context_budget import _counter
from colony.mcp import create_mcp
from colony.models import ContextBudget
from colony.setup import setup_codex
from colony.workflow import (
    _ADAPTIVE_MANAGEMENT_GUIDANCE,
    ADAPTIVE_READ_ONLY_WORKFLOW_GUIDANCE,
    ADAPTIVE_WORKFLOW_GUIDANCE,
)


@pytest.mark.parametrize("read_only", [False, True])
@pytest.mark.parametrize("encoding", ["utf8_bytes", "o200k_base"])
def test_new_guidance_and_catalog_do_not_grow_from_reviewed_head(identity, read_only, encoding, monkeypatch):
    if encoding == "o200k_base":
        if not os.environ.get("COLONY_TEST_CONTEXT_TOKENIZER"):
            pytest.skip("Pinned tokenizer required")
        monkeypatch.setenv("COLONY_CONTEXT_TOKENIZER", os.environ["COLONY_TEST_CONTEXT_TOKENIZER"])
    client, repo, _ = identity
    old = FROZEN_6AE5F6F_LOOKUP + (
        "This connection is read-only: do not call push or publish findings. " if read_only
        else FROZEN_6AE5F6F_PUBLICATION) + _ADAPTIVE_MANAGEMENT_GUIDANCE
    new = ADAPTIVE_READ_ONLY_WORKFLOW_GUIDANCE if read_only else ADAPTIVE_WORKFLOW_GUIDANCE
    count = _counter(ContextBudget(encoding=encoding))
    assert count(new) <= count(old)
    server = create_mcp(client.home, workflow_namespace="team", workflow_repo=repo,
                        workflow_read_only=read_only, context_budget=ContextBudget())
    tools = [t.model_dump(by_alias=True, exclude_none=True) for t in asyncio.run(server.list_tools())]
    old_tools = copy.deepcopy(tools)
    if not read_only:
        next(t for t in old_tools if t["name"] == "push")["description"] = (
            "After substantive new research, including partial hits, push one eligible reusable "
            "finding with applicability and observed file ref/content_hash. Skip isolated values "
            "unless asked to remember; skip unchanged reuse. Follow publication exclusions. "
            "Never invent hashes or research solely to publish. Answer detail/research remain "
            "unrestricted; publication failure must not block the answer. "
            "Namespace bound; taint true; secrets scrubbed."
        )
    assert count(json.dumps({"instructions": new, "tools": tools})) <= count(json.dumps(
        {"instructions": old, "tools": old_tools}))
    setup_codex(client.home, repo, "install", namespace="team", read_only=read_only,
                context_budget=ContextBudget())
    guidance = (repo / "AGENTS.md").read_text(encoding="utf8")
    for invariant in (
        "Address all requested deliverables; include needed examples/evidence",
        "Research gaps, contradictions, misses, stale/unverified evidence.",
        "limited need not mean insufficient.",
        "uncovered scope stays unknown.",
    ):
        assert invariant in guidance and invariant in server.instructions
    if not read_only:
        for invariant in (
            "never turn a simple lookup into a procedure just to publish.",
            "Never repush unchanged reuse or restatements.",
            "Publication limits never restrict answer detail or necessary research.",
            "publication/telemetry failure must not block the answer.",
            "No abstention report, decision call or automatic retry.",
            "Include all supporting observed ref/hash pairs or narrow claims.",
        ):
            assert invariant in guidance and invariant in server.instructions
    assert {t["name"] for t in tools} == ({"ask"} if read_only else {"ask", "push"})


@pytest.mark.parametrize("command", [["ask", "--help"], ["mcp", "--help"],
                                      ["setup", "codex", "install", "--help"]])
def test_help_names_default_byte_units_without_changing_limits(command, capsys):
    with pytest.raises(SystemExit) as stopped:
        parser().parse_args(command)
    assert stopped.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "default 2048 UTF-8 bytes" in help_text
    assert "encoding tokens with --context-encoding o200k_base" in help_text
    assert ContextBudget().max_tokens == 2048 and ContextBudget().encoding == "utf8_bytes"
