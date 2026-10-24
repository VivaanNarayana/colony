"""Fixed independent policy-placement comparison; one pair per task, no retries.

Qualify only if all eight cases preserve requested facts and protocol, exact
required research files/pushes, no increased calls, and aggregate native input
tokens fall by at least400. Provider cache behavior is reported without selection.
This is an independent diagnostic, not evidence for any benchmark gate."""

import hashlib
import json
import subprocess
import sys
from importlib import import_module
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "scripts"))
live = import_module("live_benchmark")
root = Path("../benchmark-remediation-description-regressions-34").resolve()
if root.exists():
    raise SystemExit("Fresh output required; no retries")
root.mkdir()
fixtures = [
    {
        "id": "qualified-hit",
        "question": "What is the archive retention rule, including legal holds?",
        "files": {
            "docs/archive.md": "Ordinary archives expire after 65 days. Legal-hold archives remain until the hold is released.\n"
        },
        "read": "docs/archive.md",
        "content": "Ordinary archives expire after 65 days. Legal holds prevent expiry until released.",
        "required_reads": [],
        "pushes": 0,
        "facts": [["65"], ["hold"], ["releas", "indefinit", "never", "not expire"]],
    },
    {
        "id": "source-only",
        "question": "Which region serves staging traffic?",
        "files": {
            "docs/regions.md": "Staging traffic uses ap-east-4. Production traffic uses sa-west-7.\n"
        },
        "read": "docs/regions.md",
        "evidence_only": True,
        "required_reads": [],
        "pushes": 0,
        "facts": [["ap-east-4"]],
    },
    {
        "id": "signature-miss",
        "question": "What algorithm does src/colony/envelope.py use to verify envelope digests?",
        "files": {
            "src/colony/envelope.py": "from .digests import check_digest\n\ndef receive_envelope(message):\n    check_digest(message.body, message.stamp, message.key)\n    return accept(message)\n",
            "src/colony/digests.py": "import hmac\n\ndef check_digest(body, stamp, key):\n    expected = hmac.digest(key, body, 'sha512')\n    if not hmac.compare_digest(expected, stamp):\n        raise ValueError('invalid stamp')\n",
        },
        "required_reads": ["src/colony/envelope.py", "src/colony/digests.py"],
        "pushes": 1,
        "facts": [["hmac"], ["sha512", "sha-512", "sha 512"]],
    },
    {
        "id": "partial-hit",
        "question": "Which ACTIVE_REGION and FALLBACK_REGION values are declared in src/colony/regions.py?",
        "files": {
            "docs/routing.md": "The active zone is cedar-2. The fallback zone is defined in src/colony/regions.py.\n",
            "src/colony/regions.py": "ACTIVE_REGION = 'cedar-2'\nFALLBACK_REGION = 'pine-6'\n",
        },
        "read": "docs/routing.md",
        "content": "The active zone is cedar-2.",
        "required_reads": ["src/colony/regions.py"],
        "pushes": 1,
        "facts": [["cedar-2"], ["pine-6"]],
    },
]
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
        sources = [json.loads(c["result"]["content"][0]["text"])["source"] for c in reads]
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
    and totals["after"]["native_input_tokens"] <= totals["before"]["native_input_tokens"] - 400
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
