"""One fixed before/after pair per independent caller/helper fixture; no retries."""

import hashlib
import json
import subprocess
import sys
from importlib import import_module
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "scripts"))
live = import_module("live_benchmark")
root = Path("../benchmark-remediation-helper-regressions-31").resolve()
if root.exists():
    raise SystemExit("Fresh output required; no retries")
root.mkdir()
fixtures = [
    {
        "id": "guard",
        "question": "What checks does src/colony/commit.py apply before committing a record?",
        "files": {
            "commit.py": 'from .contexts import validate_context\n\ndef commit(request):\n    validate_context(request.context)\n    if request.version != 7:\n        raise ValueError("unsupported version")\n    return commit_record(request)\n',
            "contexts.py": 'from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey\n\ndef validate_context(context):\n    Ed25519PublicKey.from_public_bytes(context.public_key).verify(context.signature, context.body)\n    if context.expired:\n        raise ValueError("expired context")\n',
        },
        "required": ["commit.py"],
        "max_after_reads": 1,
        "facts": ["7", "context"],
    },
    {
        "id": "topic",
        "question": "How does src/colony/publisher.py restrict topic access?",
        "files": {
            "publisher.py": 'from .tickets import verify_ticket\n\ndef publish(topic, ticket, message):\n    verify_ticket(ticket)\n    if topic not in ticket.topics:\n        raise PermissionError("topic denied")\n    return append(topic, message)\n',
            "tickets.py": 'import hmac\n\ndef verify_ticket(ticket):\n    expected = hmac.digest(ticket.issuer_key, ticket.body, "sha256")\n    if not hmac.compare_digest(expected, ticket.signature):\n        raise ValueError("bad ticket")\n',
        },
        "required": ["publisher.py"],
        "max_after_reads": 1,
        "facts": ["topic", "ticket"],
    },
    {
        "id": "stamp",
        "question": "Which algorithm verifies the stamp in src/colony/receiver.py?",
        "files": {
            "receiver.py": "from .stamps import verify_stamp\n\ndef receive(message):\n    verify_stamp(message.body, message.stamp, message.key)\n    return accept(message)\n",
            "stamps.py": 'import hmac\n\ndef verify_stamp(body, stamp, key):\n    expected = hmac.digest(key, body, "sha256")\n    if not hmac.compare_digest(expected, stamp):\n        raise ValueError("invalid stamp")\n',
        },
        "required": ["receiver.py", "stamps.py"],
        "max_after_reads": 2,
        "facts": ["sha", "hmac"],
    },
    {
        "id": "shard",
        "question": "Which shard does src/colony/routing.py select for the archive tenant?",
        "files": {
            "routing.py": "from .shards import TENANT_SHARDS\n\ndef route(tenant):\n    return TENANT_SHARDS[tenant]\n",
            "shards.py": 'TENANT_SHARDS = {"archive": "amber-4", "sandbox": "violet-2"}\n',
        },
        "required": ["routing.py", "shards.py"],
        "max_after_reads": 2,
        "facts": ["amber-4"],
    },
]
source_trees = {}
for arm in ["before", "after"]:
    tree = root / arm / "source"
    for p in sorted(Path("src/colony").glob("*.py")):
        data = (
            subprocess.check_output(
                ["git", "show", "687d2a79bef92255dea8d6d37f06567cd9708bf9:" + p.as_posix()]
            )
            if arm == "before"
            else p.read_bytes().replace(b"\r\n", b"\n")
        )
        target = tree / "colony" / p.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    source_trees[arm] = tree
plan = {
    "role": "Independent fixed paired caller/helper behavior diagnostic, not gate evidence or held-out confirmation",
    "fixtures": fixtures,
    "order": [("before", "after"), ("after", "before"), ("before", "after"), ("after", "before")],
    "retries": 0,
    "model": "gpt-6-luna",
    "effort": "low",
    "before_commit": "687d2a79bef92255dea8d6d37f06567cd9708bf9",
    "source_sha256": {
        a: {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (t / "colony").glob("*.py")}
        for a, t in source_trees.items()
    },
    "candidate_qualification": "Both arms core facts/protocol/fingerprints correct; after caller cases <=1 read and necessary implementation/value cases both defining files; after total research reads strictly fewer than before. Manual facts/qualification/source review required before a full benchmark trial.",
}
(root / "plan.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
rows = []
for f, order in zip(fixtures, plan["order"], strict=True):
    for arm in order:
        work = root / arm / f["id"]
        repo = work / "repo"
        (repo / "src/colony").mkdir(parents=True)
        (repo / "README.md").write_text("# Independent fixture\n", encoding="utf-8")
        (repo / "pyproject.toml").write_text('[project]\nname="fixture"\n', encoding="utf-8")
        for name, content in f["files"].items():
            (repo / "src/colony" / name).write_text(content, encoding="utf-8")
        boot = work / "mcp-fixture.py"
        boot.write_text(
            "import sys\nfrom pathlib import Path\nsys.path.insert(0,"
            + repr(str(source_trees[arm]))
            + ')\nimport colony.mcp as m\nclass Client:\n def __init__(self,home):pass\n def ask(self,*a,**kw):return {"hit":False,"reason":"no_relevant_trail"}\n def push(self,trail):return {"id":trail.id,"stored":True,"duplicate":False}\nm.Client=Client\nm.create_mcp(Path.cwd()).run(transport="stdio")\n',
            encoding="utf-8",
        )
        live.RUN = work
        live.OUT = work / "results"
        live.OUT.mkdir()
        row = live.run_subject(
            {"task_id": f["id"], "family": f["id"], "question": f["question"]},
            "colony",
            implementation=boot,
            max_research_calls=6,
        )
        calls = [
            e["item"]
            for event_line in (live.OUT / (f["id"] + ".colony.events.jsonl"))
            .read_text(encoding="utf-8")
            .splitlines()
            if (e := json.loads(event_line)).get("type") == "item.completed"
            and e.get("item", {}).get("type") == "mcp_tool_call"
        ]
        asks = [c for c in calls if c["server"] == "colony" and c["tool"] == "ask"]
        pushes = [c for c in calls if c["server"] == "colony" and c["tool"] == "push"]
        reads = [c for c in calls if c["server"] == "repository" and c["tool"] == "read_file"]
        paths = [c["arguments"]["path"] for c in reads]
        errors = []
        if len(asks) != 1 or asks[0]["arguments"].get("question") != f["question"]:
            errors.append("ask protocol")
        if len(pushes) != 1 or pushes[0]["arguments"].get("question") != f["question"]:
            errors.append("push protocol")
        if any("src/colony/" + p not in paths for p in f["required"]):
            errors.append("missing required source")
        if arm == "after" and len(reads) > f["max_after_reads"]:
            errors.append("unnecessary dependency reads")
        for fact in f["facts"]:
            if fact.casefold() not in row["answer"].casefold():
                errors.append("missing " + fact)
        read_sources = [json.loads(c["result"]["content"][0]["text"])["source"] for c in reads]
        if not pushes or not pushes[0]["arguments"].get("sources"):
            errors.append("no push sources")
        if pushes:
            for s in pushes[0]["arguments"].get("sources", []):
                if s not in read_sources:
                    errors.append("fingerprint not in actual read")
        rows.append(
            {
                "arm": arm,
                "task": f["id"],
                "row": row,
                "reads": paths,
                "saved": pushes[0]["arguments"].get("content") if pushes else None,
                "errors": errors,
            }
        )
        (root / "observations.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
        print(
            json.dumps({"arm": arm, "task": f["id"], "reads": paths, "errors": errors}), flush=True
        )
totals = {a: sum(len(r["reads"]) for r in rows if r["arm"] == a) for a in ["before", "after"]}
qualifies = not any(r["errors"] for r in rows) and totals["after"] < totals["before"]
(root / "outcome.json").write_text(
    json.dumps(
        {
            "mechanical_candidate_qualifies": qualifies,
            "read_totals": totals,
            "manual_review_still_required": True,
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
raise SystemExit(0 if qualifies else 1)
