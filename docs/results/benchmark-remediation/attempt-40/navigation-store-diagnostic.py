"""Fixed full-retrieval navigation check; new isolated source/store exports, no retries.

Require at least two prior-location misses, no negative hint/hit and unchanged
hit eligibility before native evaluation. No source/task retries or model changes.
"""

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

fixtures = [
    {
        "id": "spool",
        "question": "Which spool options are mandatory?",
        "file": "src/colony/launch.py",
        "text": "from pathlib import Path\nimport typer\n\napp = typer.Typer()\n\n@app.command()\ndef spool(\n    journal: Path = typer.Option(...),\n    checkpoint: Path = typer.Option(...),\n    limit: int = typer.Option(17),\n):\n    replicate(journal, checkpoint, limit)\n",
        "required": ["journal", "checkpoint"],
    },
    {
        "id": "mirror",
        "question": "Which mirror flags must be supplied?",
        "file": "src/colony/mirroring.py",
        "text": "from pathlib import Path\nimport typer\n\napp = typer.Typer()\n\n@app.command()\ndef mirror(\n    origin: Path = typer.Option(...),\n    cache: Path = typer.Option(...),\n    host: str = typer.Option('127.0.0.1'),\n):\n    start(origin, cache, host)\n",
        "required": ["origin", "cache"],
    },
    {
        "id": "inspect",
        "question": "What inspect options are required?",
        "file": "src/colony/inspection.py",
        "text": "from pathlib import Path\nimport typer\n\napp = typer.Typer()\n\n@app.command()\ndef inspect(\n    root: Path = typer.Option(...),\n    log: Path = typer.Option(...),\n    mode: str = typer.Option('quick'),\n):\n    check(root, log, mode)\n",
        "required": ["root", "log"],
    },
]
if len(sys.argv) > 1:
    source = Path(sys.argv[1])
    work = Path(sys.argv[2])
    sys.path.insert(0, str(source))
    from colony.models import Grant, Scope, Trail
    from colony.store import Store
    from colony.validation import snapshot

    grant = Grant(
        member_id="fixture",
        name="Fixture",
        server="http://localhost",
        expires_at=int(time.time()) + 3600,
        scopes=[Scope(namespace="bench", push=True, pull=True)],
    )
    rows = []
    for f in fixtures:
        repo = work / f["id"] / "repo"
        target = repo / f["file"]
        target.parent.mkdir(parents=True)
        target.write_bytes(f["text"].encode())
        store = Store(work / f["id"] / "data", repo)
        store.push(
            Trail(
                id="a" * 32,
                namespace="bench",
                question="What is the unrelated audit retention period?",
                content="Audit records remain for five days.",
                sources=[snapshot(repo, f["file"])],
            ),
            grant,
        )
        packet = store.ask(f["question"], ["bench"], 604800, 0.7)
        negative = store.ask("How are customer signing keys rotated?", ["bench"], 604800, 0.7)
        supported = packet.get("status") == "validated" and all(
            any(fact in e["text"] for e in packet.get("evidence", [])) for fact in f["required"]
        )
        rows.append({"fixture": f, "packet": packet, "negative": negative, "supported": supported})
    (work / "observations.json").write_text(
        json.dumps(rows, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(
        json.dumps(
            [
                {
                    "task": r["fixture"]["id"],
                    "supported": r["supported"],
                    "negative_hit": r["negative"].get("hit", False),
                    "research_paths": r["packet"].get("research_paths"),
                    "evidence": r["packet"].get("evidence"),
                }
                for r in rows
            ]
        ),
        flush=True,
    )
    raise SystemExit()
root = Path("../benchmark-remediation-navigation-regressions-40").resolve()
if root.exists():
    raise SystemExit("Fresh directory required; no retries")
root.mkdir()
proof = {}
for arm in ["before", "after"]:
    source = root / arm / "source"
    proof[arm] = {}
    for p in sorted(Path("src/colony").glob("*.py")):
        data = (
            subprocess.check_output(
                ["git", "show", "c659ed45aaafbe2e0df01b3d9e10e0ab807b5b58:" + p.as_posix()]
            )
            if arm == "before"
            else p.read_bytes().replace(b"\r\n", b"\n")
        )
        target = source / "colony" / p.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        proof[arm][p.name] = hashlib.sha256(data).hexdigest()
    subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), str(source), str(root / arm)], check=True
    )
(root / "source-provenance.json").write_text(
    json.dumps(proof, indent=2) + "\n", encoding="utf-8", newline="\n"
)
before = json.loads((root / "before/observations.json").read_text(encoding="utf-8"))
after = json.loads((root / "after/observations.json").read_text(encoding="utf-8"))
new_navigation = sum(
    not b["packet"].get("hit") and a["packet"].get("research_paths") == [a["fixture"]["file"]]
    for b, a in zip(before, after, strict=True)
)
qualified = (
    new_navigation >= 2
    and all(not r["negative"].get("hit") and not r["negative"].get("research_paths") for r in after)
    and all(
        b["packet"].get("hit") == a["packet"].get("hit")
        and (not a["packet"].get("hit") or a["supported"])
        for b, a in zip(before, after, strict=True)
    )
)
(root / "outcome.json").write_text(
    json.dumps(
        {
            "qualified_for_native": qualified,
            "new_navigation": new_navigation,
            "positive_hits_unchanged": all(
                b["packet"].get("hit") == a["packet"].get("hit")
                for b, a in zip(before, after, strict=True)
            ),
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
    newline="\n",
)
raise SystemExit(0 if qualified else 1)
