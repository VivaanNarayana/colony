"""Prepare shuffled answers and source-target checks, without assigning quality labels."""

import json
import random
import re
import sys
from pathlib import Path
from urllib.parse import unquote

results, runtime = map(Path, sys.argv[1:3])
protocol = json.loads((results / "protocol.json").read_text())
tasks = {t["task_id"]: t for t in protocol["task_spec"]}
records = []
for arm in ["main", "feature"]:
    corpus = runtime / ("main" if arm == "main" else "feat") / "repo"
    manifest = json.loads((results / arm / "corpus-sha256.json").read_text())
    for s in (results / arm / "raw-measurements.jsonl").read_text().splitlines():
        row = json.loads(s)
        links = re.findall(r"\[[^\]]+\]\(([^)\n]+)\)", row["answer"])
        errors = []
        for link in links:
            target, _, fragment = unquote(link.strip("<>")).partition("#")
            line = re.search(r":(\d+)$", target)
            if line:
                target = target[: line.start()]
            if fragment and not re.fullmatch(r"L\d+(?:-L?\d+)?", fragment):
                errors.append("invalid source fragment")
            try:
                path = (corpus / target).resolve()
                ref = path.relative_to(corpus.resolve()).as_posix()
                if ref not in manifest or not path.is_file():
                    raise ValueError("not in corpus")
                number = (
                    int(fragment[1:].split("-")[0]) if fragment else int(line[1]) if line else None
                )
                if number is not None and not 1 <= number <= len(
                    path.read_text(encoding="utf-8").splitlines()
                ):
                    errors.append("source line outside file")
            except (ValueError, OSError):
                errors.append("source target missing or outside frozen repository: " + target)
        bare = re.findall(
            r"(?<![/\w])(?:src/colony/[\w.]+\.py|README\.md|pyproject\.toml)(?!\w)", row["answer"]
        )
        valid = not errors and bool(links or any(ref in manifest for ref in bare))
        packets = [json.loads(a["result"]["content"][0]["text"]) for a in row["asks"]]
        records.append(
            {
                "arm": arm,
                "task_id": row["task_id"],
                "question": tasks[row["task_id"]]["question"],
                "family": row["family"],
                "answer": row["answer"],
                "citation_valid": valid,
                "citation_errors": errors,
                "packets": packets,
            }
        )
random.Random(20261022).shuffle(records)
for i, record in enumerate(records):
    record["review_id"] = f"R{i + 1:02d}"
(results / "review-mapping.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
for r in records:
    print(
        json.dumps(
            {
                k: r[k]
                for k in [
                    "review_id",
                    "question",
                    "family",
                    "answer",
                    "citation_valid",
                    "citation_errors",
                ]
            },
            ensure_ascii=True,
        )
    )
    for p in r["packets"]:
        print(
            json.dumps(
                {
                    "hit": p.get("hit"),
                    "kind": p.get("match_kind"),
                    "saved_content": p.get("content"),
                    "reads": [
                        {k: e[k] for k in ["path", "start_line", "content"]}
                        for e in p.get("current_repository_reads", [])
                    ],
                },
                ensure_ascii=True,
            )
        )
