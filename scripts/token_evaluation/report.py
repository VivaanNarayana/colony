"""Offline, fail-closed answer masking and quality-bound usage accounting.

Replays test this harness only. No report certifies product savings or billing.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
from pathlib import Path

from colony.product_value import native_usage

from .evidence import evaluate_mechanisms

ARMS = ("no_colony", "colony")
FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "gross_tokens",
          "uncached_input_plus_output")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def extract_answer(events_path, policy):
    _require(policy in ("last_agent_message", "all_agent_messages"), "Unknown answer policy")
    messages, phases = [], []
    for line in Path(events_path).read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("type") == "item.completed":
            item = event.get("item", {})
            if item.get("type") == "agent_message":
                _require(isinstance(item.get("text"), str), "Malformed answer")
                messages.append(item["text"])
                phases.append(item.get("phase"))
    text = (messages[-1] if messages else "") if policy == "last_agent_message" else "\n".join(messages)
    return {"text": text, "answer_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "all_messages_sha256": hashlib.sha256("\n".join(messages).encode()).hexdigest(),
            "phase_guaranteed": bool(phases) and all(p is not None for p in phases)}


def activity(events_path):
    """Observe exact completed MCP calls and every JSON content block; never infer reuse."""
    counts = dict.fromkeys(("colony_calls", "research_calls", "hits", "misses", "stored",
                            "claimed_current_reads", "stale", "miss_or_research_advice", "claimed_validated_hits", "safe_refusals", "claimed_evidence_hits", "cache_misses", "unapproved_calls"), 0)
    seen, completed = set(), set()
    for number, line in enumerate(Path(events_path).read_text(encoding="utf-8").splitlines()):
        event = json.loads(line)
        item = event.get("item", {})
        if event.get("type") not in {"item.started", "item.updated", "item.completed"}:
            continue
        kind = item.get("type")
        identity = item.get("id", ("line", number))
        first = identity not in seen
        seen.add(identity)
        if kind != "mcp_tool_call":
            if first and kind not in {"agent_message", "reasoning", "todo_list"}:
                counts["unapproved_calls"] += 1
            continue
        server, tool = str(item.get("server", "")).lower(), item.get("tool")
        colony = server == "colony"
        allowed = ((colony and tool in {"ask", "push"}) or
                   (server == "repository" and tool in {"fixture_files", "fixture_read", "fixture_search"}))
        if first:
            counts["unapproved_calls"] += not allowed
            counts["colony_calls"] += colony
            counts["research_calls"] += server == "repository"
        if not colony or event.get("type") != "item.completed":
            continue
        if identity in completed:
            raise ValueError("Duplicate completed tool call")
        completed.add(identity)
        result = item.get("result") or {}
        if item.get("status") != "completed" or result.get("isError"):
            continue
        for block in result.get("content", []):
            if block.get("type") != "text":
                continue
            try:
                value = json.loads(block["text"])
            except (ValueError, KeyError, TypeError):
                continue
            if not isinstance(value, dict):
                continue
            if tool == "ask":
                counts["hits"] += value.get("hit") is True
                counts["claimed_validated_hits"] += (value.get("hit") is True
                    and value.get("status") == "validated"
                    and bool(value.get("current_repository_reads")))
                counts["misses"] += value.get("hit") is False
                counts["cache_misses"] += (value.get("hit") is False
                    and value.get("reason") == "no_relevant_trail")
                counts["claimed_current_reads"] += bool(value.get("current_repository_reads"))
                counts["stale"] += value.get("status") == "stale"
                counts["safe_refusals"] += (value.get("hit") is False and value.get("reason") in {
                    "no_relevant_trail", "git_context_mismatch", "git_local_evidence_not_verified",
                    "git_no_verified_hit", "git_invalid_receipt"})
                counts["claimed_evidence_hits"] += (value.get("hit") is True
                    and value.get("status") == "validated"
                    and value.get("match_kind") == "evidence"
                    and bool(value.get("current_repository_reads")))
                counts["miss_or_research_advice"] += (value.get("hit") is False or value.get("recommendation") == "research")
            if tool == "push":
                counts["stored"] += value.get("stored") is True
    return counts


def _load(run_dir):
    root = Path(run_dir).resolve(strict=True)
    run = _read(root / "run.json")
    manifest = run["protocol"]
    _require(run.get("contract") == "colony.token-evaluation-run/1", "Wrong run contract")
    _require(digest(manifest) == run.get("protocol_sha256"), "Frozen protocol changed")
    for relative, expected_hash in run["files"].items():
        frozen = (root / relative).resolve(strict=True)
        _require(frozen.is_relative_to(root) and _sha(frozen) == expected_hash, "Frozen source changed")
    _require(isinstance(manifest, dict), "Missing frozen protocol")
    tasks = {(w["id"], t["id"]): t for w in manifest["workloads"] for t in w["tasks"]}
    expected = {(arm, *key) for arm in ARMS for key in tasks}
    records = [json.loads(line) for line in (root / "control" / "attempts.jsonl").read_text(encoding="utf-8").splitlines()]
    invocations, results = {}, {}
    for record in records:
        identity = record["id"]
        _require(isinstance(identity, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", identity),
                 "Invalid attempt identity")
        _require(identity not in results, "Duplicate attempt")
        invocation_path = root / "control" / f"{identity}-invocation.json"
        invocation = _read(invocation_path)
        _require(all(invocation[k] == record[k] for k in ("id", "arm", "workload", "task")),
                 "Invocation identity changed")
        invocations[identity] = invocation
        results[identity] = record
    _require({p.name for p in (root / "control").glob("*-invocation.json")} ==
             {f"{identity}-invocation.json" for identity in results}, "Unlisted invocation")
    rows, seen, logs = [], set(), set()
    for identity, invocation in invocations.items():
        record = results[identity]
        key = tuple(invocation[k] for k in ("arm", "workload", "task"))
        _require(key in expected and key not in seen, "Unexpected/duplicate scheduled task")
        _require(all(record[k] == invocation[k] for k in ("arm", "workload", "task")),
                 "Result identity changed")
        seen.add(key)
        result = record.get("result", record)
        relative = result.get("events")
        _require(isinstance(relative, str) and Path(relative).name == relative,
                 "Every attempt requires a retained native event basename")
        path = (root / "logs" / relative).resolve(strict=True)
        _require(path.is_relative_to(root / "logs") and not Path(relative).is_absolute(),
                 "Log outside inventory")
        _require(path not in logs and _sha(path) == result.get("events_sha256"),
                 "Duplicate/altered events")
        logs.add(path)
        state = result.get("state")
        _require(state in ("completed", "failed", "timeout", "interrupted"), "Unknown state")
        seconds = result.get("seconds")
        _require(seconds is None or (type(seconds) in (int, float) and math.isfinite(seconds)
                                    and seconds >= 0), "Invalid duration")
        task = tasks[key[1:]]
        answer = extract_answer(path, manifest["output"]["answer_policy"])
        workload = next(w for w in manifest["workloads"] if w["id"] == key[1])
        current = next((s for s in workload["snapshots"] if s["id"] == task.get("snapshot")), None)
        rows.append({"id": str(identity), "arm": key[0], "workload": key[1], "task": key[2],
                     "state": state, "seconds": seconds, "phase": task["phase"],
                     "source_sha256": digest({"snapshot": task.get("snapshot"), "files": current["files"] if current else None}),
                     "citation_sha256": digest(task["oracle"]),
                     "label_sha256": digest(task["rubric"]), **answer, "activity": activity(path), **native_usage(path)})
        # native_usage hashes all agent messages; preserve declared answer selection.
        rows[-1].update(answer)
        _require(_sha(path) == result["events_sha256"], "Events changed while reading")
    _require(seen == expected, "Missing scheduled tasks")
    for workload in manifest["workloads"]:
        for arm in ARMS:
            observed_order = [r["task"] for r in rows
                              if r["arm"] == arm and r["workload"] == workload["id"]]
            _require(observed_order == [t["id"] for t in workload["tasks"]],
                     "Scheduled task order changed")
    actual = {p.resolve() for p in (root / "logs").rglob("*.events.jsonl") if p.is_file()}
    _require(actual == logs, "Unlisted/missing log files")
    return run, manifest, rows


def mask(run_dir, output_dir):
    run, manifest, rows = _load(run_dir)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    public, private = [], []
    for row in rows:
        case = secrets.token_hex(16)
        binding = {k: row[k] for k in ("answer_sha256", "source_sha256", "citation_sha256",
                                       "label_sha256")}
        task = next(t for w in manifest["workloads"] if w["id"] == row["workload"]
                    for t in w["tasks"] if t["id"] == row["task"])
        workload = next(w for w in manifest["workloads"] if w["id"] == row["workload"])
        current = next((s for s in workload["snapshots"] if s["id"] == task.get("snapshot")), None)
        sources = []
        for relative, expected_hash in (current["files"].items() if current else []):
            source_root = Path(run_dir).resolve() / "workspaces" / "no_colony" / row["workload"] / current["id"]
            source = (source_root / relative).resolve(strict=True)
            _require(source.is_relative_to(source_root) and _sha(source) == expected_hash,
                     "Current grading source changed")
            sources.append({"path": relative, "sha256": expected_hash,
                            "content": source.read_bytes().decode("utf-8")})
        public.append({"case": case, "answer": row["text"], "current_sources": sources,
                       "question": task.get("question"), "oracle": task["oracle"],
                       "rubric": task["rubric"], "snapshot": task.get("snapshot"),
                       "protocol_sha256": run["protocol_sha256"], **binding})
        private.append({"case": case, "id": row["id"], **binding})
    secrets.SystemRandom().shuffle(public)
    (output / "answers.json").write_text(json.dumps(public, indent=2), encoding="utf-8")
    (output / "private-map.json").write_text(json.dumps(private, indent=2), encoding="utf-8")
    return {"answers": str(output / "answers.json"), "private_map": str(output / "private-map.json")}


def report(run_dir, quality_path, output_path):
    run, manifest, rows = _load(run_dir)
    root = Path(run_dir)
    collection = _read(root / "collection.json") if (root / "collection.json").exists() else {}
    native = collection.get("native_evidence") is True
    if native:
        _require(collection.get("protocol_sha256") == run["protocol_sha256"], "Collection binding changed")
        _require(collection.get("preflight_sha256") == _sha(root / "preflight.json"), "Preflight binding changed")
        preflight = _read(root / "preflight.json")
        _require(preflight.get("ok") is True and preflight.get("native_ready") is True
                 and preflight.get("protocol_sha256") == run["protocol_sha256"],
                 "Preflight was not ready for this protocol")
        _require(collection.get("initial_empty_stores") == {"no_colony": True, "colony": True},
                 "Initial empty store proof missing")
        transitions = collection.get("transitions", [])
        expected_transitions = {}
        for workload in manifest["workloads"]:
            snapshots = {snapshot["id"]: snapshot["files"] for snapshot in workload["snapshots"]}
            for task in workload["tasks"]:
                for arm in ARMS:
                    expected_transitions[(arm, workload["id"], task["id"])] = (
                        task["snapshot"], digest(snapshots[task["snapshot"]]))
        observed_transitions = {}
        for transition in transitions:
            key = tuple(transition[k] for k in ("arm", "workload", "task"))
            _require(key not in observed_transitions, "Duplicate snapshot transition")
            observed_transitions[key] = (transition["snapshot"], transition["files_sha256"])
        _require(observed_transitions == expected_transitions, "Snapshot transition proof mismatch")
        for row in rows:
            invocation = _read(root / "control" / f'{row["id"]}-invocation.json')
            _require(invocation.get("native_evidence") is True, "Non-native invocation in native collection")
            _require(invocation.get("protocol_sha256") == run["protocol_sha256"],
                     "Invocation protocol changed")
            _require(invocation.get("frozenclient_executable_sha256") == manifest["client"]["sha256"],
                     "Invocation client changed")
    quality = _read(quality_path)
    _require(quality.get("reviewer") and quality.get("method"),
             "Independent reviewer identity and method required")
    _require(quality.get("protocol_sha256") == run.get("protocol_sha256"), "Protocol binding changed")
    reviews = quality["labels"]
    _require(len({r["id"] for r in reviews}) == len(reviews), "Duplicate quality")
    lookup = {r["id"]: r for r in reviews}
    _require(set(lookup) == {r["id"] for r in rows}, "Missing/unlisted quality")
    for row in rows:
        review = lookup[row["id"]]
        for field in ("answer_sha256", "source_sha256", "citation_sha256", "label_sha256"):
            _require(review.get(field) == row[field], "Quality binding changed: " + field)
        _require(all(type(review.get(k)) is bool for k in ("correct", "grounded", "format_compliant")),
                 "Explicit quality labels required")
        row["successful"] = (review["correct"] and review["grounded"] and review["format_compliant"]
                             and row["state"] == "completed" and row["native_finished"])
    proofs = evaluate_mechanisms(root, manifest, rows)
    for row in rows:
        task = next(t for w in manifest["workloads"] if w["id"] == row["workload"]
                    for t in w["tasks"] if t["id"] == row["task"])
        proof = proofs[row["id"]]
        row["mechanism_proof"] = proof
        field = {"required_publication": "publication", "required_hit": "hit",
                 "required_miss": "miss", "required_stale": "stale",
                 "required_insufficient": "insufficient"}.get(task.get("expected_use"))
        row["expected_use_verified"] = (row["arm"] == "no_colony" or
            manifest["track"] == "natural" or field is None or proof[field])
        if row["arm"] == "colony" and manifest["track"] == "controlled":
            if task.get("expected_use") == "required_hit":
                row["expected_use_verified"] &= proof["useful_reuse"]
            if task.get("expected_use") == "required_publication" and task.get("scenario") == "cold_learning":
                row["expected_use_verified"] &= proof["miss"]
        row["successful"] &= row["expected_use_verified"]
    arms = {}
    for arm in ARMS:
        selected = [r for r in rows if r["arm"] == arm]
        successes = sum(all(r["successful"] for r in selected if r["workload"] == w["id"])
                        for w in manifest["workloads"])
        known = {f: sum(r["known_usage"][f] for r in selected) for f in FIELDS}
        complete = all(r["usage_complete"] and r["native_finished"] and r["state"] == "completed" for r in selected)
        arms[arm] = {"known_usage": known, "total_usage": known if complete else None,
                     "successful_workflows": successes,
                     "gross_tokens_per_successful_workflow": known["gross_tokens"] / successes
                     if complete and successes else None,
                     "unknown_usage_attempts": sum(not r["usage_complete"] for r in selected),
                     "failed_attempts": sum(r["state"] != "completed" or not r["native_finished"]
                                            for r in selected),
                     "phase_known_usage": {phase: {f: sum(r["known_usage"][f] for r in selected
                                                               if r["phase"] == phase)
                                                    for f in FIELDS} for phase in ("learn", "reuse")},
                     "session_seconds_sum": sum(r["seconds"] for r in selected)
                     if all(r["seconds"] is not None for r in selected) else None}
    adoption = any(r["activity"]["colony_calls"] for r in rows if r["arm"] == "colony")
    policy_compliant = all((r["arm"] != "no_colony" or r["activity"]["colony_calls"] == 0) and
                          r["activity"]["unapproved_calls"] == 0 and
                          r["activity"]["research_calls"] <= manifest.get("research", {}).get("max_calls", 6)
                          for r in rows)
    for row in rows:
        row["useful_reuse"] = (native and row["arm"] == "colony" and row["phase"] == "reuse"
            and row["successful"] and proofs[row["id"]]["useful_reuse"]
            and any(prior["id"] == proofs[row["id"]].get("published_attempt")
                    and prior["successful"] for prior in rows)
            and row["activity"]["unapproved_calls"] == 0)
    useful_reuse = any(r["useful_reuse"] for r in rows)
    quality_complete = all(r["successful"] for r in rows)
    usage_complete = all(arms[a]["total_usage"] is not None for a in ARMS)
    controlled_pass = (native and collection.get("status") == "complete"
        and manifest["track"] == "controlled" and quality_complete and usage_complete
        and adoption and policy_compliant and all(r["expected_use_verified"] for r in rows))
    reduction = None
    if (native and collection.get("status") == "complete"
            and quality_complete and usage_complete and adoption and policy_compliant):
        reduction = {}
        for field in ("gross_tokens", "uncached_input_plus_output"):
            baseline = arms["no_colony"]["total_usage"][field]
            reduction[field] = 1 - arms["colony"]["total_usage"][field] / baseline if baseline else None
    result = {"contract": "colony.token-evaluation/1", "arms": arms, "attempts": rows,
              "certifying": False, "supported_product_win": "not_established", "billing": None,
              "native_evidence": native, "harness_complete": True, "quality_complete": quality_complete,
              "controlled_mechanism_pass": controlled_pass,
              "adoption": adoption, "adoption_context": manifest["track"],
              "natural_adoption_observed": adoption if manifest["track"] == "natural" else None,
              "colony_attempts_using_mcp": sum(r["activity"]["colony_calls"] > 0 for r in rows if r["arm"] == "colony"),
              "useful_reuse": useful_reuse,
              "tool_policy_compliant": policy_compliant, "observed_token_reduction": reduction,
              "observed_reduction_not_confidence": True,
              "limitations": ["All learning, failures and agent messages are charged.",
                              "Prompt caching is not Colony memory reuse.",
                              "MCP adoption and provenance require independent trace verification.",
                              "Unknown usage is not zero; no hypothetical future amortization.",
                              "Native receipts are declared counterproof, not cryptographic attestation.",
                              "Setup guidance is delivered manually; native loader fidelity is unverified.",
                              "Independent quality labels bind bytes; hashes do not establish truth.",
                              "No-Colony initial empty-store proof means no memory configured."]}
    path = Path(output_path)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    return result
