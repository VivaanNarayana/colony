"""Print 24 overlapping tasks or report paired, externally measured agent trials."""

import argparse
import json
import math
from pathlib import Path

FAMILIES = [
    (
        "install",
        "pyproject.toml",
        "python -m pip install -e '.[dev]'",
        [
            "How do I install development dependencies?",
            "Set up the development dependencies.",
            "Which command installs the dev extras?",
        ],
    ),
    (
        "tests",
        "pyproject.toml",
        "python -m pytest -q",
        ["How do I run the tests?", "Run the Python test suite.", "Which command runs pytest?"],
    ),
    (
        "serve",
        "src/colony/cli.py",
        "colony serve --repo . --data .colony/server --issuer .colony/admin/issuer.pub",
        [
            "How do I start the Colony server?",
            "Start the local memory server.",
            "Which serve flags are required?",
        ],
    ),
    (
        "stale",
        "src/colony/validation.py",
        "A changed source file fingerprint makes the trail stale.",
        [
            "Why is a changed file trail stale?",
            "What happens when repository evidence changes?",
            "How are file changes detected during validation?",
        ],
    ),
    (
        "outage",
        "src/colony/client.py",
        "ask returns a miss; push queues encrypted scrubbed data; retry resends.",
        [
            "What happens when the server is down?",
            "Does an offline push get queued?",
            "How do I retry after a server outage?",
        ],
    ),
    (
        "scope",
        "src/colony/server.py",
        "Signed grants control exact namespace push and pull permissions.",
        [
            "Why is namespace access denied?",
            "How are namespace permissions checked?",
            "Can an agent pull from an ungranted namespace?",
        ],
    ),
    (
        "capture",
        "src/colony/cli.py",
        "outputs capture retains a scrubbed tail and always marks it tainted.",
        [
            "What does outputs capture record?",
            "Are captured command outputs tainted?",
            "Does run capture inputs or only output?",
        ],
    ),
    (
        "mcp",
        "src/colony/mcp.py",
        "Run colony --home <absolute agent home> mcp; tools are ask and push.",
        [
            "How do I connect an agent through MCP?",
            "Which tools does the MCP wrapper expose?",
            "Start Colony as an MCP stdio server.",
        ],
    ),
]


def tasks():
    return [
        {
            "task_id": f"{family}-{i + 1}",
            "family": family,
            "question": question,
            "source": source,
            "expected": expected,
        }
        for family, source, expected, questions in FAMILIES
        for i, question in enumerate(questions)
    ]


def report(rows):
    pairs = {}
    for row in rows:
        if row["mode"] not in {"baseline", "colony"}:
            raise ValueError("Unknown measurement mode")
        for field in (
            "tokens",
            "seconds",
            "tool_calls",
            "ask_calls",
            "matches",
            "validated",
            "wrong_reused",
        ):
            value = row[field]
            if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
                raise ValueError(f"Invalid {field}")
        raw = row.get("raw_returns", row["matches"])
        if type(raw) is not int or not row["matches"] <= raw <= row["ask_calls"]:
            raise ValueError("Invalid raw return count")
        if not (
            row["wrong_reused"] <= raw
            and row["matches"] <= row["ask_calls"]
            and row["validated"] <= row["matches"]
        ):
            raise ValueError("Invalid ask/match/validation counts")
        if type(row["correct"]) is not bool:
            raise ValueError("correct must be boolean")
        pair = pairs.setdefault(row["task_id"], {})
        if row["mode"] in pair:
            raise ValueError("Duplicate task/mode measurement")
        pair[row["mode"]] = row
    if not pairs or any(set(pair) != {"baseline", "colony"} for pair in pairs.values()):
        raise ValueError("Each task requires one baseline and one colony measurement")
    baseline = [p["baseline"] for p in pairs.values()]
    colony = [p["colony"] for p in pairs.values()]

    def total(group, field):
        return sum(r[field] for r in group)

    def ratio(n, d):
        return n / d if d else None

    savings = {}
    for field in ["tokens", "seconds", "tool_calls"]:
        b, c = total(baseline, field), total(colony, field)
        savings[field] = {"baseline": b, "colony": c, "reduction": 1 - c / b if b else None}
    return {
        "paired_tasks": len(pairs),
        "reuse_rate": ratio(total(colony, "matches"), total(colony, "ask_calls")),
        "validated_hit_rate": ratio(total(colony, "validated"), total(colony, "matches")),
        "wrong_reuse_rate": ratio(
            total(colony, "wrong_reused"), sum(r.get("raw_returns", r["matches"]) for r in colony)
        ),
        "correct_tasks": {
            "baseline": total(baseline, "correct"),
            "colony": total(colony, "correct"),
        },
        "savings": savings,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--tasks", action="store_true")
    group.add_argument("--results", type=Path)
    args = parser.parse_args()
    if args.tasks:
        for task in tasks():
            print(json.dumps(task))
    else:
        try:
            rows = [
                json.loads(line) for line in args.results.read_text().splitlines() if line.strip()
            ]
            print(json.dumps(report(rows), indent=2))
        except (ValueError, KeyError, TypeError) as exc:
            parser.error(str(exc))
