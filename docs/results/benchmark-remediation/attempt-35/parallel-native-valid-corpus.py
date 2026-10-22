"""Fixed independent independent-read scheduling comparison; one pair per task, no retries.

Qualify only if all eight cases preserve requested facts and protocol, exact
required research files/pushes, no increased calls, and aggregate native input
tokens fall by at least10000. Provider cache behavior is reported without selection.
This is an independent diagnostic, not evidence for any benchmark gate."""

import hashlib
import json
import subprocess
import sys
from importlib import import_module
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "scripts"))
live = import_module("live_benchmark")
root = Path("../benchmark-remediation-parallel-regressions-35b").resolve()
if root.exists():
    raise SystemExit("Fresh output required; no retries")
root.mkdir()
fixtures = [
    {
        "id": "independent-policies",
        "question": "What attempt cap is specified in src/colony/queue_rules.py and what retention rule is specified in src/colony/archive_rules.py?",
        "files": {
            "src/colony/queue_rules.py": "# Queue delivery policy\nMAX_ATTEMPTS = 9\n",
            "src/colony/archive_rules.py": "# Ordinary archives expire after 57 days. Legal holds prevent expiry until released.\nRETENTION_DAYS = 57\n",
        },
        "required_reads": ["src/colony/queue_rules.py", "src/colony/archive_rules.py"],
        "pushes": 1,
        "facts": [["nine", "9"], ["57"], ["hold"], ["release"]],
    },
    {
        "id": "independent-instructions",
        "question": "How do I prepare and then send a bundle, following src/colony/build_steps.py and src/colony/send_steps.py?",
        "files": {
            "src/colony/build_steps.py": "# Prepare a bundle using `bundle compile --signed`. Signing is required before sending.\n",
            "src/colony/send_steps.py": "# Send the prepared bundle using `bundle send --target staging`. Sending to production is forbidden.\n",
        },
        "required_reads": ["src/colony/build_steps.py", "src/colony/send_steps.py"],
        "pushes": 1,
        "facts": [["bundle compile --signed"], ["bundle send --target staging"], ["production"]],
    },
    {
        "id": "independent-locations",
        "question": "Which preferred region is declared in src/colony/preferred.py and which standby region is declared in src/colony/standby.py?",
        "files": {
            "src/colony/preferred.py": "REGION = 'ap-west-8'\n",
            "src/colony/standby.py": "REGION = 'eu-central-5'\n",
        },
        "required_reads": ["src/colony/preferred.py", "src/colony/standby.py"],
        "pushes": 1,
        "facts": [["ap-west-8"], ["eu-central-5"]],
    },
    {
        "id": "discovered-dependency",
        "question": "What algorithm does src/colony/verifier.py use to validate record signatures?",
        "files": {
            "src/colony/verifier.py": "from .algorithms import verify_record\n\ndef check(record):\n    verify_record(record)\n",
            "src/colony/algorithms.py": "import hmac\n\ndef verify_record(record):\n    expected = hmac.digest(record.key, record.body, 'sha224')\n    if not hmac.compare_digest(expected, record.signature):\n        raise ValueError('bad signature')\n",
        },
        "required_reads": ["src/colony/verifier.py", "src/colony/algorithms.py"],
        "pushes": 1,
        "facts": [["hmac"], ["sha224", "sha-224", "sha 224"]],
    },
]
for fixture in fixtures:
    assert all(
        name in {"README.md", "pyproject.toml"}
        or (Path(name).parent.as_posix() == "src/colony" and Path(name).suffix == ".py")
        for name in fixture["files"]
    )
source_trees = {}
for arm in ["before", "after"]:
    tree = root / arm / "source"
    for path in sorted(Path("src/colony").glob("*.py")):
        data = (
            subprocess.check_output(
                ["git", "show", "ebcdebd44466330eeb7aaaf09aec7390fb0b012d:" + path.as_posix()]
            )
            if arm == "before"
            else path.read_bytes().replace(b"\r\n", b"\n")
        )
        target = tree / "colony" / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    source_trees[arm] = tree
orders = [["before", "after"], ["after", "before"], ["before", "after"], ["after", "before"]]
# Factual source provenance only. Cases, order and acceptance are fixed above.
(root / "source-provenance.json").write_text(
    json.dumps(
        {
            "before_commit": "ebcdebd44466330eeb7aaaf09aec7390fb0b012d",
            "source_sha256": {
                arm: {
                    p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in (tree / "colony").glob("*.py")
                }
                for arm, tree in source_trees.items()
            },
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
rows = []
for fixture, order in zip(fixtures, orders, strict=True):
    for arm in order:
        work = root / arm / fixture["id"]
        repo = work / "repo"
        repo.mkdir(parents=True)
        (repo / "README.md").write_text("# Independent fixture\n", encoding="utf-8")
        (repo / "pyproject.toml").write_text('[project]\nname="fixture"\n', encoding="utf-8")
        for name, content in fixture["files"].items():
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        if fixture.get("read"):
            ref = fixture["read"]
            digest = hashlib.sha256((repo / ref).read_bytes()).hexdigest()
            packet = {
                "hit": True,
                "match_kind": "evidence" if fixture.get("evidence_only") else "answer",
                "status": "validated",
                "namespace": "bench",
                "recommendation": "reuse_if_relevant",
                "tainted": True,
                "sources": [{"ref": ref, "content_hash": digest}],
                "evidence": [{"ref": ref, "line": 1, "text": fixture["files"][ref]}],
            }
            if not fixture.get("evidence_only"):
                packet["content"] = fixture["content"]
        else:
            packet = {"hit": False, "reason": "no_relevant_trail"}
        bootstrap = work / "mcp-fixture.py"
        bootstrap.write_text(
            "import copy,sys\nfrom pathlib import Path\nsys.path.insert(0,"
            + repr(str(source_trees[arm]))
            + ")\nimport colony.mcp as m\nPACKET="
            + repr(packet)
            + "\nclass Client:\n def __init__(self,home):pass\n def ask(self,*a,**kw):return copy.deepcopy(PACKET)\n def push(self,trail):return {'id':trail.id,'stored':True,'duplicate':False}\nm.Client=Client\nm.create_mcp(Path.cwd()).run(transport='stdio')\n",
            encoding="utf-8",
        )
        live.RUN = work
        live.OUT = work / "results"
        live.OUT.mkdir()
        row = live.run_subject(
            {"task_id": fixture["id"], "family": fixture["id"], "question": fixture["question"]},
            "colony",
            implementation=bootstrap,
            max_research_calls=6,
        )
        events = [
            json.loads(line)
            for line in (live.OUT / (fixture["id"] + ".colony.events.jsonl"))
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        calls = [
            e["item"]
            for e in events
            if e.get("type") == "item.completed"
            and e.get("item", {}).get("type") == "mcp_tool_call"
        ]
        asks = [c for c in calls if c["server"] == "colony" and c["tool"] == "ask"]
        pushes = [c for c in calls if c["server"] == "colony" and c["tool"] == "push"]
        reads = [c for c in calls if c["server"] == "repository" and c["tool"] == "read_file"]
        research = [c for c in calls if c["server"] == "repository"]
        errors = []
        if len(asks) != 1 or asks[0]["arguments"].get("question") != fixture["question"]:
            errors.append("ask protocol")
        if len(pushes) != fixture["pushes"]:
            errors.append("push count")
        if sorted(c["arguments"]["path"] for c in reads) != sorted(fixture["required_reads"]):
            errors.append("research files")
        for alternatives in fixture["facts"]:
            if not any(fact.casefold() in row["answer"].casefold() for fact in alternatives):
                errors.append("missing requested fact " + repr(alternatives))
        sources = []
        for call in reads:
            try:
                sources.append(json.loads(call["result"]["content"][0]["text"])["source"])
            except (KeyError, IndexError, TypeError, json.JSONDecodeError):
                errors.append("failed research read: " + call["arguments"]["path"])
        proofs = {(s.get("kind", "file"), s["ref"], s["content_hash"]) for s in sources}
        for push in pushes:
            if (
                push["arguments"].get("question") != fixture["question"]
                or push["arguments"].get("namespace") != "bench"
            ):
                errors.append("push question/scope")
            if not push["arguments"].get("sources"):
                errors.append("missing push proof")
            for source in push["arguments"].get("sources", []):
                if (
                    source.get("kind", "file"),
                    source["ref"],
                    source["content_hash"],
                ) not in proofs:
                    errors.append("push proof absent from actual new read")
        usage = [e["usage"] for e in events if e.get("type") == "turn.completed"]
        rows.append(
            {
                "arm": arm,
                "task": fixture["id"],
                "row": row,
                "reads": [c["arguments"]["path"] for c in reads],
                "research_calls": len(research),
                "saved": [c["arguments"].get("content") for c in pushes],
                "errors": errors,
                "native_input_tokens": sum(u["input_tokens"] for u in usage),
                "native_output_tokens": sum(u["output_tokens"] for u in usage),
                "native_cached_input_tokens": sum(u.get("cached_input_tokens", 0) for u in usage),
            }
        )
        (root / "observations.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "arm": arm,
                    "task": fixture["id"],
                    "input_tokens": rows[-1]["native_input_tokens"],
                    "errors": errors,
                }
            ),
            flush=True,
        )
totals = {
    arm: {
        key: sum(r[key] for r in rows if r["arm"] == arm)
        for key in [
            "native_input_tokens",
            "native_output_tokens",
            "native_cached_input_tokens",
            "research_calls",
        ]
    }
    for arm in ["before", "after"]
}
qualified = (
    not any(r["errors"] for r in rows)
    and totals["after"]["native_input_tokens"] <= totals["before"]["native_input_tokens"] - 10000
    and totals["after"]["research_calls"] <= totals["before"]["research_calls"]
    and sum(r["row"]["tool_calls"] for r in rows if r["arm"] == "after")
    <= sum(r["row"]["tool_calls"] for r in rows if r["arm"] == "before")
)
(root / "outcome.json").write_text(
    json.dumps(
        {
            "mechanically_qualified": qualified,
            "totals": totals,
            "manual_review_still_required": True,
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
raise SystemExit(0 if qualified else 1)
