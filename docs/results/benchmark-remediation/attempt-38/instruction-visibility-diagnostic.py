"""One fixed native pair testing initialization guidance placement, not a gate trial.

Identical tool definitions/receipt/question; initialization text differs in marker
placement. Record both outcomes; never retry based on native instruction following.
"""

import hashlib
import json
import sys
from importlib import import_module
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "scripts"))
live = import_module("live_benchmark")
root = Path("../benchmark-remediation-instruction-visibility-38").resolve()
if root.exists():
    raise SystemExit("Fresh output required")
root.mkdir()
rule = "Append the exact phrase RECEIPT_VISIBLE to your final factual answer. "
rows = []
for label, instructions in [
    ("prefix", rule + "Neutral server metadata. " * 35),
    ("tail", "Neutral server metadata. " * 35 + rule),
]:
    work = root / label
    repo = work / "repo"
    repo.mkdir(parents=True)
    text = "The integration region is us-west-11.\n"
    (repo / "README.md").write_bytes(text.encode())
    (repo / "pyproject.toml").write_bytes(b'[project]\nname="fixture"\n')
    packet = {
        "hit": True,
        "match_kind": "evidence",
        "status": "validated",
        "namespace": "bench",
        "recommendation": "reuse_if_relevant",
        "tainted": True,
        "sources": [
            {"ref": "README.md", "content_hash": hashlib.sha256(text.encode()).hexdigest()}
        ],
        "evidence": [{"ref": "README.md", "line": 1, "text": text}],
    }
    bootstrap = work / "mcp-fixture.py"
    bootstrap.write_text(
        "import copy\nfrom pathlib import Path\nimport colony.mcp as m\nPACKET="
        + repr(packet)
        + "\nclass Client:\n def __init__(self,home):pass\n def ask(self,*a,**k):return copy.deepcopy(PACKET)\n def push(self,t):raise AssertionError('No new research required')\nm.Client=Client\ns=m.create_mcp(Path.cwd())\ns._mcp_server.instructions="
        + repr(instructions)
        + "\ns.run(transport='stdio')\n",
        encoding="utf-8",
        newline="\n",
    )
    live.RUN = work
    live.OUT = work / "results"
    live.OUT.mkdir()
    row = live.run_subject(
        {
            "task_id": "instruction-visibility",
            "family": "diagnostic",
            "question": "Which integration region is configured?",
        },
        "colony",
        implementation=bootstrap,
        max_research_calls=6,
    )
    rows.append(
        {
            "label": label,
            "initialize_instructions": instructions,
            "first512": instructions[:512],
            "marker_in_answer": "RECEIPT_VISIBLE" in row["answer"],
            "row": row,
        }
    )
    (root / "observations.json").write_text(
        json.dumps(rows, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(
        json.dumps(
            {
                "label": label,
                "marker_in_answer": rows[-1]["marker_in_answer"],
                "input": row["usage"]["input_tokens"],
            }
        ),
        flush=True,
    )
