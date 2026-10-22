"""Four new independent one-shot behavioral regressions, not benchmark gate evidence."""

import hashlib
import json
import sys
from importlib import import_module
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "scripts"))
live = import_module("live_benchmark")
root = Path("../benchmark-remediation-output-regressions-30").resolve()
if root.exists():
    raise SystemExit("Fresh output required; no retries")
root.mkdir()
fixtures = [{'id': 'qualified-retention', 'question': 'In src/colony/retention.py, how long are archives retained?', 'files': {'retention.py': '# Default archives expire after 14 days. Production archives require 90 days.\n# Legal-hold archives never expire until the hold is released.\nDEFAULT_DAYS = 14\nPRODUCTION_DAYS = 90\n'}, 'required': ['retention.py'], 'allowed': ['retention.py'], 'fact': ['14']}, {'id': 'retry-steps', 'question': 'In src/colony/retry_policy.py, how should an operator retry a failed export?', 'files': {'retry_policy.py': '# First repair the connection. Then run `exportctl retry --pending`.\n# If the server still returns 503, wait at least 23 seconds before another attempt.\nRETRY_DELAY_SECONDS = 23\n'}, 'required': ['retry_policy.py'], 'allowed': ['retry_policy.py'], 'fact': ['exportctl retry --pending']}, {'id': 'dependent-region', 'question': 'Which region does src/colony/dispatch.py select for the archive tenant?', 'files': {'dispatch.py': 'from .zones import TENANT_REGIONS\n\ndef region(tenant):\n    return TENANT_REGIONS[tenant]\n', 'zones.py': 'TENANT_REGIONS = {"archive": "ap-south-1", "sandbox": "eu-north-1"}\n'}, 'required': ['dispatch.py', 'zones.py'], 'allowed': ['dispatch.py', 'zones.py'], 'fact': ['ap-south-1']}, {'id': 'signing-exception', 'question': 'In src/colony/signing.py, are signatures always required?', 'files': {'signing.py': '# Production messages require Ed25519 signatures.\n# Local test fixtures may omit a signature only when sandbox=True.\ndef required(sandbox=False):\n    return not sandbox\n'}, 'required': ['signing.py'], 'allowed': ['signing.py'], 'fact': ['sandbox']}]
source = Path("src/colony/mcp.py").resolve()
sha = hashlib.sha256(source.read_bytes()).hexdigest()
plan = {
    "role": "Independent behavioral regressions; four new previously unrun qualification/required-step/dependency fixtures; no gate evidence, no control-arm or identical-task retries",
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
