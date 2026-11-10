"""Compact bundles must survive caller Git verification with every cited core."""

import hashlib

import pytest
import test_compact_delivery as controls

from colony.compact import PROFILE
from colony.models import ContextBudget


@pytest.fixture
def transport(tmp_path, monkeypatch):
    return controls.transport.__wrapped__(tmp_path, monkeypatch)


@pytest.mark.parametrize("count", [4, 5, 8])
@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_complete_bundle_survives_git_client_and_mcp(transport, count, ending):
    client, repo, _, responses = transport
    trail = controls.publish(client, repo, count, ending=ending, content="Finding.", git=True)
    budget = ContextBudget(max_tokens=8192)
    value = controls.ask_mcp(client, repo, budget, git=True)
    remote = responses[-1]
    assert remote["hit"] and len(remote["current_repository_reads"]) == count
    assert value["response_profile"] == PROFILE and value["hit"] is True
    assert value["git"]["compatibility"] == "matched"
    assert value["evidence_scope"] == "cited_files"
    reads = value["current_repository_reads"]
    assert len(reads) == count
    assert {read["path"] for read in reads} == {source.ref for source in trail.sources}
    identities = {source["ref"]: source["content_hash"] for source in value["sources"]}
    for read in reads:
        raw = (repo / read["path"]).read_bytes()
        assert read["sha256"] == identities[read["path"]] == hashlib.sha256(raw).hexdigest()
        assert read["content"] == "\n".join(raw.decode("utf-8").splitlines())
        assert read["start_line"] == 1 and read["complete_file"] is True
