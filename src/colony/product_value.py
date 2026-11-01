"""Offline token/quality scorecard for matched Colony/no-Colony workflows.

Reads existing native logs only. Never launches agents, changes gates or certifies readiness.
"""

import argparse
import hashlib
import math
import re
from pathlib import Path

from .report_io import (
    bounded_bytes,
    checked_path,
    decode,
    files_under,
    put,
    read,
    relative_file,
    require,
    sha,
)

CONTRACT = "colony.product-value/1"
MODES = ("no_colony", "colony")
FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens")
MAX_BYTES = 64 << 20


def native_usage(path):
    """One turn.started per turn.completed/failed; completion usage is per turn."""
    try:
        return _native_usage_bytes(bounded_bytes(path, MAX_BYTES))
    except (KeyError, TypeError, IndexError, AttributeError, OverflowError, RecursionError) as exc:
        raise ValueError("Malformed native log") from exc


def _native_usage_bytes(source):
    known = dict.fromkeys([*FIELDS, "gross_tokens", "uncached_input_plus_output"], 0)
    active, finished, accounted, turns = False, True, True, 0
    answers, auxiliary = [], []
    for line in source.decode("utf-8").splitlines():
        event = decode(line)
        require(isinstance(event, dict), "Native event must be an object")
        kind = event.get("type")
        require(
            kind
            in {
                "thread.started",
                "turn.started",
                "turn.completed",
                "turn.failed",
                "item.started",
                "item.updated",
                "item.completed",
                "error",
            },
            "Unsupported native event format",
        )
        if kind == "turn.started":
            require(not active, "Overlapping/duplicate native turns")
            active = True
        elif kind == "error":
            # A native stream-level failure cannot certify complete usage merely
            # because an earlier turn happened to complete.
            finished = accounted = False
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
            extra = {k: v for k, v in usage.items() if k not in FIELDS}
            require(
                all(
                    re.fullmatch(r"[a-zA-Z0-9_]{1,64}", k)
                    and type(v) in {int, float}
                    and math.isfinite(v)
                    and v >= 0
                    for k, v in extra.items()
                ),
                "Unsupported auxiliary usage",
            )
            auxiliary.append(extra)
        elif kind == "item.completed":
            item = event.get("item")
            require(isinstance(item, dict), "Malformed native item")
            if item.get("type") == "agent_message":
                require(isinstance(item.get("text"), str), "Malformed native answer")
                answers.append(item["text"])
    return {
        "known_usage": known,
        "native_finished": finished and not active and turns > 0,
        "usage_complete": accounted and finished and not active and turns > 0,
        "answer_sha256": hashlib.sha256("\n".join(answers).encode()).hexdigest(),
        "auxiliary_usage_not_added": auxiliary,
    }


def scorecard(manifest_file, logs):
    """Score exactly one explicit inventory; unsupported/malformed input is rejected."""
    try:
        return _scorecard(manifest_file, logs)
    except (KeyError, TypeError, IndexError, AttributeError, OverflowError, RecursionError) as exc:
        raise ValueError("Malformed product-value manifest or native log") from exc


def _scorecard(manifest_file, logs):
    require(Path(manifest_file).stat().st_size <= 1 << 20, "Manifest size limit")
    manifest_hash = sha(manifest_file)
    manifest, logs = read(manifest_file), checked_path(logs)
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
    rows, latest, imported_bytes = [], {}, 0
    for attempt in attempts:
        mode, key = attempt["mode"], (attempt["workflow"], attempt["task"])
        require(mode in MODES and key in tasks, "Unexpected arm/task")
        require(attempt["state"] in {"completed", "failed", "timeout"}, "Unknown attempt state")
        path = relative_file(logs, attempt["events"])
        source = bounded_bytes(path, MAX_BYTES - imported_bytes)
        imported_bytes += len(source)
        require(
            hashlib.sha256(source).hexdigest() == attempt["events_sha256"],
            "Native log hash changed",
        )
        parsed = _native_usage_bytes(source)
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


def comparison_report(manifest_file, logs):
    """Metadata-only CLI summary; the historical scorecard retains its frozen shape.

    Manifest prose, task IDs and native event refs never enter this summary.
    Repeated calls rebuild one selected inventory without persisting imports.
    """
    report = scorecard(manifest_file, logs)
    fields = (
        "attempts",
        "failed_attempts",
        "unknown_usage_attempts",
        "unknown_quality_attempts",
        "grounded_successful_workflows",
        "known_usage",
        "total_usage",
        "session_seconds_sum",
        "gross_tokens_per_successful_workflow",
        "phase_known_gross_tokens",
        "scheduled_tasks",
        "correct_tasks",
        "grounded_tasks",
    )
    summary = {
        "contract": "colony.usage-comparison/1",
        "scorecard_contract": CONTRACT,
        "manifest_sha256": report["manifest_sha256"],
        "arms": {mode: {field: report["arms"][mode][field] for field in fields} for mode in MODES},
        "observed_token_reduction": report["observed_token_reduction"],
        "supported_product_win": "not_established",
        "production_readiness": "not_evaluated",
        "legacy_eight_gates": "not_evaluated_or_changed",
        "dollars": None,
        "limitations": report["limitations"]
        + [
            "Only the explicitly selected compatible native-log inventory is imported; no raw logs are retained",
            "Repeated comparisons rebuild the inventory; there is no cumulative token import store",
        ],
    }

    # Session time sums can overflow despite finite individual observations.
    def finite(value):
        if isinstance(value, dict):
            return all(finite(v) for v in value.values())
        if isinstance(value, list):
            return all(finite(v) for v in value)
        return not isinstance(value, float) or math.isfinite(value)

    require(finite(summary), "Nonfinite aggregate comparison")
    return summary


def render_comparison(report):
    """Render fixed labels and numeric aggregates without manifest/log prose."""
    lines = ["Colony matched comparison (colony.usage-comparison/1)"]
    for mode in MODES:
        arm = report["arms"][mode]
        totals = arm["total_usage"]
        gross = "unknown" if totals is None else str(totals["gross_tokens"])
        uncached = "unknown" if totals is None else str(totals["uncached_input_plus_output"])
        lines.append(
            f"{mode}: {arm['attempts']} attempts; {arm['failed_attempts']} failed; "
            f"gross tokens {gross}; uncached input plus output {uncached}; "
            f"known gross subtotal {arm['known_usage']['gross_tokens']}."
        )
        lines.append(
            f"{mode}: {arm['unknown_usage_attempts']} attempts with unknown usage; "
            f"{arm['unknown_quality_attempts']} attempts with unknown quality; "
            f"{arm['correct_tasks']}/{arm['scheduled_tasks']} final tasks labeled correct; "
            f"{arm['grounded_tasks']}/{arm['scheduled_tasks']} final tasks labeled grounded and correct; "
            f"{arm['grounded_successful_workflows']} grounded successful workflows."
        )
    for field in ("gross_tokens", "uncached_input_plus_output"):
        reduction = report["observed_token_reduction"][field]
        value = (
            "unknown (incomplete usage or zero baseline)"
            if reduction is None
            else f"{reduction:.6%}"
        )
        lines.append(f"Observed {field} reduction against no_colony: {value}.")
    lines.extend(
        [
            "All learning and retry costs are included; missing/failed usage is unknown, not zero.",
            "Quality labels and protocol identity are supplied externally; hashes bind bytes, not truth.",
            "Descriptive point observations only; no confidence interval or supported product win.",
            "Dollars: unknown. Production readiness and historical gates: not evaluated or changed.",
            "This comparison is rebuilt from one explicit inventory; it is not a persistent token import.",
        ]
    )
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--logs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="NEW report; never overwrite")
    args = parser.parse_args()
    put(args.output, scorecard(args.manifest, args.logs))


if __name__ == "__main__":
    main()
