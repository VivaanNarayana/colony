"""Offline token/quality scorecard for matched Colony/no-Colony workflows.

Reads existing native logs only. Never launches agents, changes gates or certifies readiness.
"""

import argparse
import hashlib
import math
from pathlib import Path

from production_delivery_core import decode, files_under, put, read, relative_file, require, sha

CONTRACT = "colony.product-value/1"
MODES = ("no_colony", "colony")
FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens")
MAX_BYTES = 64 << 20


def native_usage(path):
    """One turn.started per turn.completed/failed; completion usage is per turn."""
    known = dict.fromkeys([*FIELDS, "gross_tokens", "uncached_input_plus_output"], 0)
    active, finished, accounted, turns = False, True, True, 0
    answers, auxiliary = [], []
    for line in path.read_text(encoding="utf-8").splitlines():
        event = decode(line)
        kind = event.get("type")
        if kind == "turn.started":
            require(not active, "Overlapping/duplicate native turns")
            active = True
        elif kind in {"turn.completed", "turn.failed"}:
            require(active, "Native completion without a unique started turn")
            active = False
            usage = event.get("usage")
            if kind == "turn.failed":
                finished = accounted = False
            else:
                turns += 1
            if usage is None:
                accounted = False
                continue
            require(isinstance(usage, dict), "Malformed usage")
            for field in FIELDS:
                if field not in usage:
                    accounted = False
                    continue
                value = usage.get(field)
                require(type(value) is int and value >= 0, "Invalid native token count")
                known[field] += value
            if {"input_tokens", "cached_input_tokens"} <= usage.keys():
                require(
                    usage["cached_input_tokens"] <= usage["input_tokens"], "Cache exceeds input"
                )
            if {"input_tokens", "output_tokens"} <= usage.keys():
                known["gross_tokens"] += usage["input_tokens"] + usage["output_tokens"]
            if set(FIELDS) <= usage.keys():
                known["uncached_input_plus_output"] += (
                    usage["input_tokens"] - usage["cached_input_tokens"] + usage["output_tokens"]
                )
            auxiliary.append({k: v for k, v in usage.items() if k not in FIELDS})
        elif kind == "item.completed" and event.get("item", {}).get("type") == "agent_message":
            answers.append(event["item"]["text"])
    return {
        "known_usage": known,
        "native_finished": finished and not active and turns > 0,
        "usage_complete": accounted and finished and not active and turns > 0,
        "answer_sha256": hashlib.sha256("\n".join(answers).encode()).hexdigest(),
        "auxiliary_usage_not_added": auxiliary,
    }


def scorecard(manifest_file, logs):
    require(Path(manifest_file).stat().st_size <= 1 << 20, "Manifest size limit")
    manifest_hash = sha(manifest_file)
    manifest, logs = read(manifest_file), Path(logs)
    require(manifest["contract"] == CONTRACT, "Wrong product-value contract")
    protocol = manifest["protocol"]
    require(protocol["comparison"] == "colony_vs_no_colony", "Not a product-value comparison")
    for field in [
        "implementation",
        "corpus",
        "model",
        "client",
        "effort",
        "instructions_sha256",
        "budget",
        "memory_difference",
        "rubric_sha256",
        "reviewer",
        "review_method",
    ]:
        require(bool(protocol[field]), "Missing protocol identity: " + field)
    workflows = protocol["workflows"]
    require(
        workflows and len({w["id"] for w in workflows}) == len(workflows), "Duplicate workflows"
    )
    tasks = {}
    for workflow in workflows:
        phases = [t["phase"] for t in workflow["tasks"]]
        require(phases == sorted(phases), "Learning must precede reuse")
        for task in workflow["tasks"]:
            key = (workflow["id"], task["id"])
            require(key not in tasks and task["question"], "Duplicate/empty task")
            require(task["phase"] in {"learn", "reuse"}, "Unknown task phase")
            tasks[key] = task
        require(
            {t["phase"] for t in workflow["tasks"]} == {"learn", "reuse"},
            "Each workflow needs explicit learning and reuse tasks",
        )
    attempts = manifest["attempts"]
    require(0 < len(attempts) <= 128, "Use 1..128 explicitly inventoried attempts")
    native_files = {
        p.relative_to(logs).as_posix(): p
        for p in files_under(logs)
        if p.name.endswith(".events.jsonl")
    }
    require(sum(p.stat().st_size for p in native_files.values()) <= MAX_BYTES, "Log size limit")
    require(len({a["id"] for a in attempts}) == len(attempts), "Duplicate attempt ID")
    require(len({a["events"] for a in attempts}) == len(attempts), "Native log reused")
    require(set(native_files) == {a["events"] for a in attempts}, "Unlisted/missing native logs")
    rows, latest = [], {}
    for attempt in attempts:
        mode, key = attempt["mode"], (attempt["workflow"], attempt["task"])
        require(mode in MODES and key in tasks, "Unexpected arm/task")
        require(attempt["state"] in {"completed", "failed", "timeout"}, "Unknown attempt state")
        path = relative_file(logs, attempt["events"])
        require(sha(path) == attempt["events_sha256"], "Native log hash changed")
        parsed = native_usage(path)
        require(sha(path) == attempt["events_sha256"], "Native log changed during scoring")
        require(parsed["answer_sha256"] == attempt["answer_sha256"], "Review answer hash mismatch")
        for label in ["correct", "grounded"]:
            require(attempt[label] is None or type(attempt[label]) is bool, "Invalid quality label")
        seconds = attempt["seconds"]
        require(
            seconds is None
            or (type(seconds) in {int, float} and math.isfinite(seconds) and seconds >= 0),
            "Invalid session time",
        )
        row = {**attempt, **parsed, "phase": tasks[key]["phase"]}
        row["usage_complete"] &= attempt["state"] == "completed"
        row["successful"] = (
            attempt["state"] == "completed"
            and parsed["native_finished"]
            and attempt["correct"] is True
            and attempt["grounded"] is True
        )
        rows.append(row)
        latest[(mode, *key)] = row
    require(
        set(latest) == {(mode, *key) for mode in MODES for key in tasks}, "Missing scheduled task"
    )
    for workflow in workflows:
        for mode in MODES:
            order = []
            for row in rows:
                if (
                    row["workflow"] == workflow["id"]
                    and row["mode"] == mode
                    and (not order or order[-1] != row["task"])
                ):
                    order.append(row["task"])
            require(order == [t["id"] for t in workflow["tasks"]], "Task/retry order differs")

    def aggregate(selected, successes):
        known = {
            field: sum(r["known_usage"][field] for r in selected)
            for field in [*FIELDS, "gross_tokens", "uncached_input_plus_output"]
        }
        complete = all(r["usage_complete"] for r in selected)
        return {
            "attempts": len(selected),
            "failed_attempts": sum(
                r["state"] != "completed" or not r["native_finished"] for r in selected
            ),
            "unknown_usage_attempts": sum(not r["usage_complete"] for r in selected),
            "unknown_quality_attempts": sum(
                r["correct"] is None or r["grounded"] is None for r in selected
            ),
            "grounded_successful_workflows": successes,
            "known_usage": known,
            "total_usage": known if complete else None,
            "session_seconds_sum": sum(r["seconds"] for r in selected)
            if all(r["seconds"] is not None for r in selected)
            else None,
            "gross_tokens_per_successful_workflow": known["gross_tokens"] / successes
            if complete and successes
            else None,
        }

    paired, arms = [], {}
    for workflow in workflows:
        pair = {"workflow": workflow["id"]}
        for mode in MODES:
            selected = [r for r in rows if r["workflow"] == workflow["id"] and r["mode"] == mode]
            success = all(
                latest[(mode, workflow["id"], t["id"])]["successful"] for t in workflow["tasks"]
            )
            pair[mode] = aggregate(selected, int(success))
        paired.append(pair)
    for mode in MODES:
        arms[mode] = aggregate(
            [r for r in rows if r["mode"] == mode],
            sum(p[mode]["grounded_successful_workflows"] for p in paired),
        )
        arms[mode]["phase_known_gross_tokens"] = {
            phase: sum(
                r["known_usage"]["gross_tokens"]
                for r in rows
                if r["mode"] == mode and r["phase"] == phase
            )
            for phase in ["learn", "reuse"]
        }
        final = [latest[(mode, *key)] for key in tasks]
        arms[mode]["scheduled_tasks"] = len(final)
        arms[mode]["correct_tasks"] = sum(
            r["correct"] is True and r["state"] == "completed" and r["native_finished"]
            for r in final
        )
        arms[mode]["grounded_tasks"] = sum(r["successful"] for r in final)
    reductions = {}
    for field in ["gross_tokens", "uncached_input_plus_output"]:
        a, b = [arms[m]["total_usage"] for m in MODES]
        reductions[field] = 1 - b[field] / a[field] if a and b and a[field] else None
    require(sha(manifest_file) == manifest_hash, "Manifest changed during scoring")
    return {
        "contract": CONTRACT,
        "manifest_sha256": manifest_hash,
        "protocol": protocol,
        "arms": arms,
        "paired_workflows": paired,
        "attempts": rows,
        "observed_token_reduction": reductions,
        "supported_product_win": "not_established",
        "production_readiness": "not_evaluated",
        "legacy_eight_gates": "not_evaluated_or_changed",
        "dollars": None,
        "limitations": [
            "Descriptive observations, no confidence interval or automatic acceptance",
            "Protocol/pins and quality supplied externally; hashes bind bytes, not truth",
            "Session time sums are not end-to-end workflow wall time",
            "Unknown/failed usage is not zero; unobserved billing remains unknown",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--logs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="NEW report; never overwrite")
    args = parser.parse_args()
    put(args.output, scorecard(args.manifest, args.logs))


if __name__ == "__main__":
    main()
