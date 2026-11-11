"""Source-review counterexamples, not a new grader or tests of agent obedience.

Two repositories can give identical literal search/README observations yet differ
in answerability. Complete content is sufficient, but no particular read method
or number of files is imposed on production or the evaluator by these controls.
"""

import json

import pytest


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
