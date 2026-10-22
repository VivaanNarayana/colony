"""Four independent one-shot behavioral regressions, not benchmark gate evidence."""

import hashlib
import json
import sys
from importlib import import_module
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "scripts"))
live = import_module("live_benchmark")
root = Path("../benchmark-remediation-stopping-regressions-25").resolve()
if root.exists():
    raise SystemExit("Fresh output required; no retries")
root.mkdir()
fixtures = [
    {
        "id": "retention",
        "question": "In src/colony/archives.py, how long are archives retained?",
        "files": {
            "archives.py": "from .credentials import validate_ticket\n\nRETENTION_DAYS = 37\n\ndef retention(ticket):\n    validate_ticket(ticket)\n    return RETENTION_DAYS\n",
            "credentials.py": 'def validate_ticket(ticket):\n    if ticket.audience != "records":\n        raise ValueError("wrong audience")\n',
        },
        "required": ["archives.py"],
        "allowed": ["archives.py"],
        "fact": ["37", "thirty-seven"],
    },
    {
        "id": "period",
        "question": "In src/colony/scheduler.py, how often does reconciliation run?",
        "files": {
            "scheduler.py": "from .job_access import verify_job\n\nRECONCILIATION_PERIOD_SECONDS = 19\n\ndef run(job):\n    verify_job(job)\n    schedule(job, every_seconds=RECONCILIATION_PERIOD_SECONDS)\n",
            "job_access.py": 'def verify_job(job):\n    if job.queue != "internal":\n        raise ValueError("wrong queue")\n',
        },
        "required": ["scheduler.py"],
        "allowed": ["scheduler.py"],
        "fact": ["19", "nineteen"],
    },
    {
        "id": "signature",
        "question": "How does src/colony/receiver.py verify a message signature?",
        "files": {
            "receiver.py": "from .signatures import verify_signature\n\ndef receive(message, signature, public_key):\n    verify_signature(message, signature, public_key)\n    return accept(message)\n",
            "signatures.py": "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey\n\ndef verify_signature(message, signature, public_key):\n    Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)\n",
        },
        "required": ["receiver.py", "signatures.py"],
        "allowed": ["receiver.py", "signatures.py"],
        "fact": ["Ed25519"],
    },
    {
        "id": "region",
        "question": "Which region does src/colony/routing.py select for the sandbox tenant?",
        "files": {
            "routing.py": "from .regions import REGIONS\n\ndef region(tenant):\n    return REGIONS[tenant]\n",
            "regions.py": 'REGIONS = {"sandbox": "eu-west-2", "primary": "us-east-2"}\n',
        },
        "required": ["routing.py", "regions.py"],
        "allowed": ["routing.py", "regions.py"],
        "fact": ["eu-west-2"],
    },
]
source = Path("src/colony/mcp.py").resolve()
sha = hashlib.sha256(source.read_bytes()).hexdigest()
plan = {
    "role": "Independent behavioral regressions; four previously unrun fixture tasks; no gate evidence, no control-arm or identical-task retries",
    "model": "gpt-6-luna",
    "effort": "low",
    "source_sha256": sha,
    "fixtures": fixtures,
    "pass_rule": "Every requested fact correct; exactly one ask then repository research and one successful push with an actual read fingerprint; required paths read and no unasked helper reads in the first two cases; manual answer/saved-content review.",
}
(root / "plan.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
bootstrap = root / "colony-fixture.py"
bootstrap.write_text(
    """import json,sys
from pathlib import Path
from importlib import import_module
sys.path.insert(0, """
    + repr(str(source.parents[1]))
    + """)
import colony.mcp as m
class Client:
    def __init__(self, home): pass
    def ask(self, *a, **kw): return {"hit": False, "reason": "no_relevant_trail"}
    def push(self, trail): return {"id": trail.id, "stored": True, "duplicate": False}
m.Client=Client
m.create_mcp(Path.cwd()).run(transport="stdio")
""",
    encoding="utf-8",
)
rows = []
for f in fixtures:
    work = root / f["id"]
    repo = work / "repo"
    (repo / "src/colony").mkdir(parents=True)
    (repo / "README.md").write_text("# Independent fixture\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text('[project]\nname = "fixture"\n', encoding="utf-8")
    for name, content in f["files"].items():
        (repo / "src/colony" / name).write_text(content, encoding="utf-8")
    live.RUN = work
    live.OUT = work / "results"
    live.OUT.mkdir()
    row = live.run_subject(
        {"task_id": f["id"], "family": f["id"], "question": f["question"]},
        "colony",
        implementation=bootstrap,
        max_research_calls=6,
    )
    items = [
        e["item"]
        for line in (live.OUT / (f["id"] + ".colony.events.jsonl"))
        .read_text(encoding="utf-8")
        .splitlines()
        if (e := json.loads(line)).get("type") == "item.completed"
    ]
    calls = [i for i in items if i["type"] == "mcp_tool_call"]
    asks = [i for i in calls if i["server"] == "colony" and i["tool"] == "ask"]
    pushes = [i for i in calls if i["server"] == "colony" and i["tool"] == "push"]
    reads = [i for i in calls if i["server"] == "repository" and i["tool"] == "read_file"]
    paths = [i["arguments"]["path"] for i in reads]
    errors = []
    if len(asks) != 1 or asks[0]["arguments"].get("question") != f["question"]:
        errors.append("ask protocol")
    if len(pushes) != 1 or pushes[0]["arguments"].get("question") != f["question"]:
        errors.append("push protocol")
    if any("src/colony/" + p not in paths for p in f["required"]):
        errors.append("missing required source")
    if any(p not in ["src/colony/" + x for x in f["allowed"]] for p in paths):
        errors.append("unrequested dependency read")
    if not any(x.casefold() in row["answer"].casefold() for x in f["fact"]):
        errors.append("missing requested fact")
    read_sources = [json.loads(i["result"]["content"][0]["text"])["source"] for i in reads]
    if pushes:
        for s in pushes[0]["arguments"].get("sources", []):
            if s not in read_sources:
                errors.append("push fingerprint absent from actual read")
    if not pushes or not pushes[0]["arguments"].get("sources"):
        errors.append("no push sources")
    rows.append(
        {
            "task": f["id"],
            "row": row,
            "reads": paths,
            "saved": pushes[0]["arguments"].get("content") if pushes else None,
            "errors": errors,
        }
    )
    print(json.dumps({"task": f["id"], "reads": paths, "errors": errors}), flush=True)
assert hashlib.sha256(source.read_bytes()).hexdigest() == sha
(root / "observations.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
raise SystemExit(int(any(r["errors"] for r in rows)))
