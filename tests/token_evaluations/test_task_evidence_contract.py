"""Mechanical evidence contracts, not an automatic answer-quality grader."""

import ast
import copy
import hashlib
import json
from pathlib import Path

import pytest
import test_protocol as protocol_controls

from scripts.token_evaluation.evidence import covers, evaluate_mechanisms
from scripts.token_evaluation.protocol import validate_manifest

FIXTURES = Path(__file__).parent / "fixtures"
CATALOG = json.loads((FIXTURES / "catalog.json").read_text())
TASKS = [(w, t) for w in CATALOG["workloads"] for t in w["tasks"]]


@pytest.mark.parametrize("workload,task", TASKS, ids=[w["id"] + "/" + t["id"] for w, t in TASKS])
def test_catalog_evidence_matches_actual_source_constructs(workload, task):
    oracle = task["oracle"]
    if task["id"] == "miss":
        assert oracle["citations"] == []
        assert oracle["evidence_policy"] == {
            "kind": "scoped_absence",
            "scope": sorted(
                next(s for s in workload["snapshots"] if s["id"] == task["snapshot"])["files"]
            ),
        }
        assert oracle["facts"]["answerable"] is False
        return
    assert oracle["evidence_policy"] == {"kind": "support"}
    root = FIXTURES / workload["id"] / task["snapshot"]
    config = "settings.py" if workload["id"] == "gateway" else "policy.py"
    caller = "handler.py" if workload["id"] == "gateway" else "dispatcher.py"
    wrapper = "client.py" if workload["id"] == "gateway" else "executor.py"
    concrete = "transport.py" if workload["id"] == "gateway" else "backend.py"
    wanted = {config: [ast.parse((root / config).read_text()).body[0]]}
    if task["id"] not in {"partial_learning", "direct_read"}:
        wanted[caller] = [ast.parse((root / caller).read_text()).body[-1]]
        wanted[wrapper] = ast.parse((root / wrapper).read_text()).body
        if task["id"] == "insufficient":
            wanted[concrete] = ast.parse((root / concrete).read_text()).body
        else:
            wanted[config] = ast.parse((root / config).read_text()).body
    expected = {}
    for name, nodes in wanted.items():
        lines = (root / name).read_text().splitlines()
        expected[name] = {
            n
            for node in nodes
            for n in range(node.lineno, node.end_lineno + 1)
            if lines[n - 1].strip()
        }
    actual = {}
    for span in oracle["citations"]:
        actual.setdefault(span["path"], set()).update(range(span["start"], span["end"] + 1))
    assert actual == expected
    observed = [
        {"path": path, "start": line, "end": line}
        for path, lines in expected.items()
        for line in lines
    ]
    assert covers(observed, oracle["citations"])
    # No meaningful statement/guard/return/raise may be waived as a blank gap.
    for missing in observed:
        assert not covers([x for x in observed if x != missing], oracle["citations"])


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_policy",
        "unknown_kind",
        "support_false",
        "absence_citations",
        "partial_scope",
        "duplicate_scope",
        "extra_scope",
        "empty_facts",
        "missing_grounded",
        "disabled_strictness",
    ],
)
def test_prospective_contract_rejects_incoherent_review_inputs(tmp_path, mutation):
    value = protocol_controls.manifest(tmp_path)
    workload = value["workloads"][0]
    support = workload["tasks"][0]
    absence = next(t for t in workload["tasks"] if t["id"] == "miss")
    # Explicit inputs also make the baseline rejection test exercise validation,
    # rather than just relying on the old catalog lacking the new field.
    support["oracle"]["evidence_policy"] = {"kind": "support"}
    absence["oracle"].update(
        citations=[],
        evidence_policy={
            "kind": "scoped_absence",
            "scope": sorted(workload["snapshots"][0]["files"]),
        },
    )
    if mutation == "missing_policy":
        support["oracle"].pop("evidence_policy")
    elif mutation == "unknown_kind":
        support["oracle"]["evidence_policy"]["kind"] = "optional"
    elif mutation == "support_false":
        support["oracle"]["facts"]["answerable"] = False
    elif mutation == "absence_citations":
        absence["oracle"]["citations"] = [{"path": "settings.py", "start": 1, "end": 1}]
    elif mutation == "partial_scope":
        absence["oracle"]["evidence_policy"]["scope"].pop()
    elif mutation == "duplicate_scope":
        absence["oracle"]["evidence_policy"]["scope"] *= 2
    elif mutation == "extra_scope":
        absence["oracle"]["evidence_policy"]["scope"].append("private.txt")
    elif mutation == "empty_facts":
        support["oracle"]["facts"] = {}
    elif mutation == "missing_grounded":
        support["rubric"].pop("grounded")
    else:
        support["oracle"]["unsupported_claims_fail"] = False
    with pytest.raises(ValueError):
        validate_manifest(value)


def test_scoped_absence_contract_accepts_empty_support_spans(tmp_path):
    value = protocol_controls.manifest(tmp_path)
    for workload in value["workloads"]:
        for task in workload["tasks"]:
            if task["id"] == "miss":
                task["oracle"].update(
                    citations=[],
                    evidence_policy={
                        "kind": "scoped_absence",
                        "scope": sorted(workload["snapshots"][0]["files"]),
                    },
                )
            else:
                task["oracle"]["evidence_policy"] = {"kind": "support"}
    assert validate_manifest(value) is value


@pytest.mark.parametrize(
    "delivery",
    ["complete_cache", "partial_cache", "complete_research", "partial_research", "forged_cache"],
)
def test_scoped_absence_coverage_is_current_full_scope_not_a_research_mandate(tmp_path, delivery):
    from test_evidence import call

    root = tmp_path / "workspaces/colony/w/v1"
    root.mkdir(parents=True)
    sources, reads, files = [], [], {}
    for name, text in [("a.py", "RATE = 17\n"), ("README.txt", "Retail invoice component.\n")]:
        raw = text.encode()
        (root / name).write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        files[name] = digest
        sources.append({"ref": name, "content_hash": digest})
        reads.append(
            {"path": name, "sha256": digest, "start": 1, "end": 1, "content": text.rstrip("\n")}
        )
    support = {
        "id": "learn",
        "phase": "learn",
        "question": "Rate?",
        "snapshot": "v1",
        "oracle": {
            "citations": [{"path": "a.py", "start": 1, "end": 1}],
            "evidence_policy": {"kind": "support"},
        },
    }
    absence = {
        "id": "reuse",
        "phase": "reuse",
        "question": "Does this repository define a refund policy?",
        "snapshot": "v1",
        "oracle": {
            "facts": {"answerable": False},
            "citations": [],
            "evidence_policy": {"kind": "scoped_absence", "scope": sorted(files)},
        },
    }
    manifest = {
        "track": "natural",
        "workloads": [
            {"id": "w", "snapshots": [{"id": "v1", "files": files}], "tasks": [support, absence]}
        ],
    }
    publication = {"question": "Rate?", "content": "Rate is 17", "sources": sources}
    learn = [call("repository", "fixture_read", {"path": r["path"]}, r) for r in reads]
    learn.append(call("colony", "push", publication, {"stored": True, "id": "memory"}))
    answer = {
        "hit": True,
        "status": "validated",
        "id": "memory",
        "sources": sources,
        "content": publication["content"],
        "current_repository_reads": copy.deepcopy(reads),
    }
    if delivery.startswith("partial"):
        answer["current_repository_reads"] = answer["current_repository_reads"][:1]
    if delivery == "forged_cache":
        answer["current_repository_reads"][-1]["content"] = "Refunds are available."
    reuse = [call("colony", "ask", {"question": absence["question"]}, answer)]
    if delivery.endswith("research"):
        answer["current_repository_reads"] = []
        selected = reads if delivery.startswith("complete") else reads[:1]
        reuse = [
            call(
                "colony",
                "ask",
                {"question": absence["question"]},
                {"hit": False, "reason": "no_relevant_trail"},
            )
        ]
        reuse += [call("repository", "fixture_read", {"path": r["path"]}, r) for r in selected]
    rows = []
    for task, events in [(support, learn), (absence, reuse)]:
        path = tmp_path / (task["id"] + ".jsonl")
        path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
        rows.append(
            {
                "id": task["id"],
                "task": task["id"],
                "workload": "w",
                "arm": "colony",
                "events_path": str(path),
            }
        )
    proof = evaluate_mechanisms(tmp_path, manifest, rows)["reuse"]
    if delivery == "complete_cache":
        assert proof["hit"] and proof["useful_reuse"]
    else:
        assert not proof["useful_reuse"]
    # Actual source scope coverage is separate from automatic truth/quality.
    assert proof["source_scope_covered"] == delivery.startswith("complete")


@pytest.mark.parametrize(
    "mutation", ["changed_file", "missing_file", "nonboolean_answerable", "scope_extra_field"]
)
def test_absence_scope_rejects_unbound_or_ambiguous_input(tmp_path, mutation):
    from scripts.token_evaluation.evidence import required_source_spans

    raw = b"VALUE = 7\n"
    path = tmp_path / "a.py"
    path.write_bytes(raw)
    snapshot = {"files": {"a.py": hashlib.sha256(raw).hexdigest()}}
    task = {
        "oracle": {
            "facts": {"answerable": False},
            "citations": [],
            "evidence_policy": {"kind": "scoped_absence", "scope": ["a.py"]},
        }
    }
    assert required_source_spans(task, snapshot, tmp_path) == [
        {"path": "a.py", "start": 1, "end": 1}
    ]
    if mutation == "changed_file":
        path.write_bytes(b"VALUE = 9\n")
    elif mutation == "missing_file":
        path.unlink()
    elif mutation == "nonboolean_answerable":
        task["oracle"]["facts"]["answerable"] = 0
    else:
        task["oracle"]["evidence_policy"]["ignore_errors"] = True
    with pytest.raises((ValueError, OSError)):
        required_source_spans(task, snapshot, tmp_path)


def test_no_lines_cannot_vacuously_earn_coverage_or_reuse(tmp_path):
    from scripts.token_evaluation.evidence import required_source_spans

    (tmp_path / "a.py").write_bytes(b"")
    snapshot = {"files": {"a.py": hashlib.sha256(b"").hexdigest()}}
    task = {
        "oracle": {
            "facts": {"answerable": False},
            "citations": [],
            "evidence_policy": {"kind": "scoped_absence", "scope": ["a.py"]},
        }
    }
    required = required_source_spans(task, snapshot, tmp_path)
    assert required == [] and not covers([], required)
