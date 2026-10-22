"""Fixed independent source-proof lookup matrix; no model or frozen tasks."""

import hashlib
import json
import sqlite3
import statistics
import subprocess
import time
import types
from pathlib import Path

from colony.store import Store

before = types.ModuleType("before_store")
exec(
    subprocess.check_output(
        ["git", "show", "687d2a79bef92255dea8d6d37f06567cd9708bf9:src/colony/store.py"]
    ).decode("utf-8"),
    before.__dict__,
)
rows = []
summaries = []
for count in [512, 8192, 32768]:
    dbs = {}
    for arm in ["before", "after"]:
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute(
            "CREATE TABLE trail_sources (trail_id TEXT NOT NULL,kind TEXT NOT NULL,ref TEXT NOT NULL,content_hash TEXT NOT NULL,symbol TEXT,contains TEXT)"
        )
        db.executemany(
            "INSERT INTO trail_sources VALUES (?,?,?,?,?,?)",
            [
                (
                    f"trail-{i}",
                    "file",
                    f"docs/policy-{j}.md",
                    hashlib.sha256(f"{i}-{j}".encode()).hexdigest(),
                    "check" if j == 1 else None,
                    "DAYS = 37" if j == 2 else None,
                )
                for i in range(count)
                for j in range(3)
            ],
        )
        if arm == "after":
            db.execute("CREATE INDEX trail_sources_trail ON trail_sources(trail_id)")
        dbs[arm] = db
    queries = ["trail-0", f"trail-{count // 2}", f"trail-{count - 1}", "absent"] * 16
    functions = {"before": before.Store.sources, "after": Store.sources}
    for block, order in enumerate([["before", "after"], ["after", "before"]]):
        for arm in order:
            for q in queries[:4]:
                functions[arm](dbs[arm], q)
            for index, q in enumerate(queries):
                start = time.perf_counter_ns()
                result = functions[arm](dbs[arm], q)
                elapsed = time.perf_counter_ns() - start
                actual = [x.model_dump() for x in result]
                other = [
                    x.model_dump()
                    for x in functions["after" if arm == "before" else "before"](
                        dbs["after" if arm == "before" else "before"], q
                    )
                ]
                assert actual == other, (count, arm, q)
                rows.append(
                    {
                        "trails": count,
                        "source_rows": count * 3,
                        "block": block,
                        "arm": arm,
                        "query_index": index,
                        "query": q,
                        "nanoseconds": elapsed,
                        "returned_sources": len(result),
                        "result_sha256": hashlib.sha256(
                            json.dumps(actual, sort_keys=True).encode()
                        ).hexdigest(),
                    }
                )
    summary = {
        "trails": count,
        "source_rows": count * 3,
        "all_results_identical": True,
        "arms": {
            a: {
                "observations": len(
                    t := [r for r in rows if r["trails"] == count and r["arm"] == a]
                ),
                "median_ns": statistics.median(r["nanoseconds"] for r in t),
                "total_ns": sum(r["nanoseconds"] for r in t),
            }
            for a in ["before", "after"]
        },
        "plans": {
            a: [
                list(r)
                for r in dbs[a].execute(
                    "EXPLAIN QUERY PLAN SELECT kind,ref,content_hash,symbol,contains FROM trail_sources WHERE trail_id=?"
                    + (" ORDER BY rowid" if a == "after" else ""),
                    ("trail-0",),
                )
            ]
            for a in ["before", "after"]
        },
    }
    summaries.append(summary)
    for db in dbs.values():
        db.close()
p = Path("docs/results/benchmark-remediation/attempt-32/source-proof-diagnostic.json")
p.write_text(
    json.dumps(
        {
            "role": "Independent fixed SQLite query matrix; every observation retained; not whole lookup/native gate evidence",
            "prior_source": "687d2a79bef92255dea8d6d37f06567cd9708bf9",
            "after_store_sha256": hashlib.sha256(
                Path("src/colony/store.py").read_bytes().replace(b"\r\n", b"\n")
            ).hexdigest(),
            "sizes": [512, 8192, 32768],
            "orders": [["before", "after"], ["after", "before"]],
            "queries": "first,middle,last,missing, sixteen repetitions each per block",
            "summaries": summaries,
            "rows": rows,
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
    newline="\n",
)
print(json.dumps(summaries, indent=2))
