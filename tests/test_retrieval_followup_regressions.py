"""Retained failure reproducers, not new agent runs or hidden evaluation cases.

The four public-fixture publications reproduce misses observed before this fix.
They must be accompanied by independent-domain precision controls; these known
cases alone cannot establish retrieval quality or native token savings.
"""

import json
from pathlib import Path

import pytest
from conftest import auth

from colony.models import Source, Trail

CASES = json.loads((Path(__file__).parent / "fixtures/retrieval_followups.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=[case["case"] for case in CASES])
def test_published_multifile_finding_supports_natural_followup(team, case):
    app, api, repo, _, key = team
    fixture = Path(__file__).parent / "token_evaluations/fixtures" / case["lineage"] / "v1"
    for file in fixture.iterdir():
        if file.is_file():
            (repo / file.name).write_text(file.read_text(encoding="utf-8"), encoding="utf-8",
                                         newline="\n")
    trail = Trail(
        id="e" * 32, namespace="team", question=case["question"], content=case["content"],
        sources=[Source(**source) for source in case["sources"]],
    )
    api.post("/trails", json=trail.model_dump(), headers=auth(key)).raise_for_status()
    result = app.state.store.ask(case["followup"], ["team"], 604800, 0.60)
    assert result["hit"], "A successful publication must provide usable current source evidence"
    assert result["match_kind"] == "evidence" and result["status"] == "validated"
    required = (
        {"settings.py", "handler.py", "client.py"} if case["lineage"] == "gateway"
        else {"policy.py", "dispatcher.py", "executor.py"}
    )
    assert required <= {item["ref"] for item in result["evidence"]}
    for item in result["evidence"]:
        assert item["line"] == 1 and item["complete_file"] is True
        assert item["text"] == (repo / item["ref"]).read_text(encoding="utf-8").rstrip("\n")
    assert case["content"] not in result["content"]
