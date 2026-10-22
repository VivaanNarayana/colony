"""Fixed full-retrieval scope check; new isolated source/store exports, no retries.

Require all three feature excerpts to show the actual requested operation, no
negative hit and a recovered supported excerpt before native evaluation.
"""

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

noise = "".join(f"    notes.append({i})\n\n" for i in range(30))
fixtures = [
    {
        "id": "refund",
        "question": "How is the refund charge computed?",
        "file": "src/colony/refunds.py",
        "text": "def compute_refund_charge(amount, processing_fee):\n    notes = []\n\n"
        + noise
        + "    return max(0, amount - processing_fee)\n\ndef audit_codes(codes):\n    return sorted(set(codes))\n",
        "required": ["return max(0, amount - processing_fee)"],
    },
    {
        "id": "checksum",
        "question": "How are upload checksums verified?",
        "file": "src/colony/uploads.py",
        "text": "import hashlib\n\ndef verify_upload_checksum(data, expected):\n    notes = []\n\n"
        + noise
        + "    actual = hashlib.sha256(data).hexdigest()\n    if actual != expected:\n        raise ValueError('changed')\n",
        "required": ["hashlib.sha256", "actual != expected"],
    },
    {
        "id": "expiry",
        "question": "What does the cursor expiry check do?",
        "file": "src/colony/cursors.py",
        "text": "def check_cursor_expiry(now, expires_at, token):\n    notes = []\n\n"
        + noise
        + "    if now > expires_at:\n        raise ValueError('expired')\n    return token\n",
        "required": ["now > expires_at", "ValueError('expired')"],
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
                    "evidence": r["packet"].get("evidence"),
                }
                for r in rows
            ]
        ),
        flush=True,
    )
    raise SystemExit()
root = Path("../benchmark-remediation-scopes-regressions-37").resolve()
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
qualified = all(r["supported"] and not r["negative"].get("hit") for r in after) and any(
    not b["supported"] and a["supported"] for b, a in zip(before, after, strict=True)
)
(root / "outcome.json").write_text(
    json.dumps(
        {
            "qualified_for_native": qualified,
            "before_supported": sum(r["supported"] for r in before),
            "after_supported": sum(r["supported"] for r in after),
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
    newline="\n",
)
raise SystemExit(0 if qualified else 1)
