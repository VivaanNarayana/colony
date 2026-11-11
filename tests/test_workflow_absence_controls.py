"""Source-review counterexamples, not a new grader or tests of agent obedience.

Two repositories can give identical literal search/README observations yet differ
in answerability. Complete content is sufficient, but no particular read method
or number of files is imposed on production or the evaluator by these controls.
"""

import hashlib
import json

import pytest

from colony.compact import PROFILE
from colony.context_budget import fit_response, with_citation_bounds
from colony.models import ContextBudget


@pytest.mark.parametrize("query,record", [
    ("storage retention", {"storage": {"ttl_days": 19}}),
    ("response deadline", {"network": {"timeout_ms": 730}}),
    ("maximum parallel jobs", {"scheduler": {"workers": 7}}),
])
def test_empty_literal_search_and_readme_do_not_establish_semantic_absence(query, record):
    common = {"README.md": "Service configuration and operational notes.\n",
              "loader.py": "import json\n"}
    present = {**common, "config.json": json.dumps(record)}
    absent = {**common, "config.json": "{}"}

    def observed(files):
        return (sorted(files), files["README.md"],
                [(name, text) for name, text in files.items() if query in text])

    assert observed(present) == observed(absent)
    assert observed(present)[2] == []
    assert json.loads(present["config.json"]) == record
    assert json.loads(absent["config.json"]) == {}
    # An on-topic current read changes the evidence, without requiring every file.
    assert present["config.json"] != absent["config.json"]


@pytest.mark.parametrize("change", ["unread", "changed"])
def test_partial_or_stale_evidence_does_not_certify_current_scope(change):
    old = {"README.md": "Runtime options.\n", "options.json": "{}"}
    current = {**old, "options.json": '{"expiry_seconds": 47}'}
    observed = {"README.md": old["README.md"]} if change == "unread" else old
    assert observed["README.md"] == current["README.md"]
    assert observed.get("options.json") != current["options.json"]
    assert json.loads(current["options.json"])["expiry_seconds"] == 47


@pytest.mark.parametrize("queries", [["retention"], ["retention", "expiry"]])
def test_more_empty_searches_and_unrelated_reads_still_leave_a_scope_gap(queries):
    common = {"README.md": "Service runtime.\n", "loader.py": "from config import OPTIONS\n",
              "worker.py": "def run(options):\n    return options\n"}
    present = {**common, "config.json": '{"ttl_days": 19}'}
    absent = {**common, "config.json": "{}"}

    def observed(files):
        searches = [[name for name, text in files.items() if q in text] for q in queries]
        return sorted(files), {name: files[name] for name in common}, searches

    assert observed(present) == observed(absent)
    assert present["config.json"] != absent["config.json"]
    # Calling this the "exposed repository" does not remove config.json from it.
    assert "config.json" in observed(present)[0]


@pytest.mark.parametrize("complete_scope", [False, True])
@pytest.mark.parametrize("config", ['{"ttl_days": 19}', '{}'])
def test_complete_cited_files_and_citation_hints_do_not_certify_repository_absence(
    complete_scope, config,
):
    files = {"readme.txt": "Runtime notes", "options.json": config}
    selected = files if complete_scope else {"readme.txt": files["readme.txt"]}
    reads = [
        {"path": path, "sha256": hashlib.sha256(text.encode()).hexdigest(),
         "content": text, "start_line": 1, "complete_file": True}
        for path, text in selected.items()
    ]
    response = {"response_profile": PROFILE, "status": "validated", "hit": True,
                "match_kind": "evidence", "current_repository_reads": reads,
                "sources": [{"ref": r["path"], "content_hash": r["sha256"]} for r in reads]}
    budget = ContextBudget(max_tokens=8192)
    result = with_citation_bounds(fit_response(response, budget), budget)
    assert result["evidence_scope"] == "cited_files"  # Only the cited set is complete.
    delivered = {r["path"]: r["content"] for r in result["current_repository_reads"]}
    assert (set(delivered) == set(files)) == complete_scope
    if complete_scope:
        # A current cache bundle provides the same text as current file reads.
        assert delivered == files
        assert json.loads(delivered["options.json"]).get("ttl_days") == json.loads(config).get("ttl_days")
    else:
        assert "options.json" not in delivered  # Bounds do not fill evidence gaps.
