"""Frozen, bounded evaluation contracts. This module never starts a model."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .output import validate_output
from .rubrics import DIMENSIONS

CONTRACT = "colony.token-evaluation/9"
ARMS = ("no_colony", "colony")
THREE_ARMS = ("no_colony", "main", "colony")


def evaluation_arms(manifest):
    return THREE_ARMS if "main_target" in manifest else ARMS


def arm_protocol(manifest, arm):
    """Use one frozen client/workload policy and the selected runtime only."""
    return {**manifest, "target": manifest["main_target"]} if arm == "main" else manifest


def arm_order(manifest, workload_index, task_index):
    arms = evaluation_arms(manifest)
    offset = workload_index + task_index
    if len(arms) == 2:
        return arms if offset % 2 == 0 else tuple(reversed(arms))
    offset %= len(arms)
    return arms[offset:] + arms[:offset]


def cohort_root(root, arm):
    return root / "cohorts" / "main" if arm == "main" else root


ATTEMPT_ID_PATTERN = r"[a-zA-Z0-9_-]{1,128}"
SCENARIOS = {"cold_learning", "useful_hit", "miss", "stale", "insufficient", "direct_read"}
USES = {"optional", "required_publication", "required_hit", "required_miss", "required_stale", "required_insufficient"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def require(value, message):
    if not value:
        raise ValueError(message)


def validate_contract(manifest):
    require(isinstance(manifest, dict) and manifest.get("contract") == CONTRACT,
            "Unsupported evaluation contract; use the original pinned collector for historical protocols")
    validate_output(manifest.get("output"))
    from .quality import review_policy
    require(manifest.get("quality_review") == review_policy(), "Frozen agent review policy required")


def collector_hashes():
    collector = Path(__file__).resolve().parent
    files = [*sorted(collector.glob("*.py")), collector.parent / "token_evaluation.py"]
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}


def verify_active_collector(manifest):
    """Require the complete active implementation, not a partial or other export's pins."""
    validate_contract(manifest)
    collector = manifest.get("collector")
    require(isinstance(collector, dict) and collector.get("files") == collector_hashes(),
            "Pin the complete active collector with matching source hashes")


def attempt_id(workload, task, arm):
    """Bounded identifier from an unambiguous tuple, independent of display-name length."""
    require(all(isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", value)
                for value in (workload, task)) and arm in THREE_ARMS, "Invalid attempt components")
    return "attempt-" + digest([workload, task, arm])


def planned_attempt_ids(manifest):
    """Check the entire schedule before collection can create or launch a session."""
    plan, identifiers = {}, set()
    for workload in manifest["workloads"]:
        for task in workload["tasks"]:
            for arm in evaluation_arms(manifest):
                key = (workload["id"], task["id"], arm)
                identity = attempt_id(*key)
                require(isinstance(identity, str) and re.fullmatch(ATTEMPT_ID_PATTERN, identity),
                        "Invalid generated attempt identity")
                require(key not in plan and identity not in identifiers, "Duplicate generated attempt identity")
                plan[key] = identity
                identifiers.add(identity)
    return plan


def relative(value):
    require(isinstance(value, str) and value and "\\" not in value and ":" not in value, "Invalid fixture path")
    p = Path(value)
    require(not p.is_absolute() and all(x not in {".", ".."} and not x.startswith(".") for x in value.split("/")), "Unsafe fixture path")
    require(p.name not in {"AGENTS.md", "AGENTS.override.md", "auth.json", "identity.json", "issuer.pem"}, "Control file cannot be research input")
    return value


def _text(value, name):
    require(isinstance(value, str) and 0 < len(value) <= 4000 and not any(ord(c) < 32 and c not in "\n\t" for c in value), "Invalid " + name)


def _flags(args, allowed):
    require(isinstance(args, list) and all(isinstance(x, str) for x in args), "Arguments must be a string list")
    i = 0
    seen = set()
    while i < len(args):
        flag = args[i]
        require(flag in allowed, "Unsupported target option: " + flag)
        require(flag not in seen, "Duplicate target option: " + flag)
        seen.add(flag)
        if allowed[flag]:
            require(i + 1 < len(args) and not args[i + 1].startswith("--"), "Missing target option value")
            i += 1
        i += 1


def validate_evidence_policy(oracle, files):
    """Freeze support obligations separately from a bounded absence claim."""
    require(isinstance(oracle, dict), "Freeze an evidence oracle")
    policy = oracle.get("evidence_policy")
    require(isinstance(policy, dict), "Freeze explicit task evidence policy")
    kind = policy.get("kind")
    facts = oracle.get("facts", {})
    require(isinstance(facts, dict), "Freeze typed evidence facts")
    if "answerable" in facts:
        require(type(facts["answerable"]) is bool, "Answerability must be explicit Boolean")
    citations = oracle.get("citations")
    require(isinstance(citations, list), "Freeze source spans")
    if kind == "support":
        require(set(policy) == {"kind"} and citations, "Support requires source spans")
        require(oracle.get("facts", {}).get("answerable") is not False,
                "Unanswerable facts require scoped absence evidence")
    elif kind == "scoped_absence":
        scope = policy.get("scope")
        require(set(policy) == {"kind", "scope"}
                and isinstance(scope, list) and scope
                and all(isinstance(name, str) for name in scope)
                and len(scope) == len(set(scope)) and set(scope) == set(files),
                "Absence scope must name every distinct frozen source file")
        require(not citations and oracle.get("facts", {}).get("answerable") is False,
                "Scoped absence requires unanswerable facts and no support spans")
    else:
        raise ValueError("Unknown task evidence policy")
    for citation in citations:
        require(isinstance(citation, dict) and citation.get("path") in files
                and type(citation.get("start")) is int and type(citation.get("end")) is int
                and 1 <= citation["start"] <= citation["end"], "Invalid required source span")
    return policy


def _validate_manifest(manifest):
    verify_active_collector(manifest)
    require(manifest.get("track") in {"natural", "controlled"}, "Unknown evaluation track")
    for key in ("target", "client", "limits", "research", "output"):
        require(isinstance(manifest.get(key), dict), "Missing " + key)
    client = manifest["client"]
    for target in ([manifest["target"], manifest["main_target"]] if "main_target" in manifest else [manifest["target"]]):
        require(isinstance(target, dict), "Invalid target runtime")
        require(re.fullmatch(r"[a-f0-9]{40}", target.get("revision", "")), "Pin a full target revision")
        require(Path(target.get("python", "")).is_absolute(), "Target Python must be absolute")
        hashes = target.get("runtime_hashes")
        require(isinstance(hashes, dict) and hashes, "Pin target runtime hashes")
        for name, value in hashes.items():
            relative(name)
            require(name.endswith(".py") and re.fullmatch(r"[a-f0-9]{64}", value), "Invalid runtime fingerprint")
        budget_flags = {"--context-budget": True, "--context-encoding": True, "--evidence-files": False, "--compact": False}
        _flags(target.get("mcp_args", []), budget_flags)
        _flags(target.get("setup_args", []), {"--context-budget": True, "--context-encoding": True})
        def context_options(args):
            return {args[i]: args[i + 1] for i in range(len(args)) if args[i] in {"--context-budget", "--context-encoding"}}
        require(context_options(target.get("mcp_args", [])) == context_options(target.get("setup_args", [])),
                "Setup and MCP context options must match")
        _flags(target.get("server_args", []), {"--models": True, "--max-age": True})
    require(Path(client.get("executable", "")).is_absolute() and re.fullmatch(r"[a-f0-9]{64}", client.get("sha256", "")), "Pin native client executable")
    for key in ("model", "version", "effort"):
        _text(client.get(key), "client " + key)
    require(client["effort"] in {"minimal", "low", "medium", "high", "xhigh"}, "Unsupported effort")
    limits = manifest["limits"]
    for key in ("max_attempts", "session_seconds", "wall_seconds", "gross_token_stop", "disk_bytes", "min_free_bytes"):
        require(type(limits.get(key)) is int and limits[key] > 0, "Positive integer limit required: " + key)
    require(limits.get("retries") == 0 and type(limits["retries"]) is int, "Retries must be disabled")
    require(limits["max_attempts"] <= 128 and limits["session_seconds"] <= 300 and limits["wall_seconds"] <= 7200, "Evaluation bounds too large")
    require(type(manifest["research"].get("max_calls")) is int and 1 <= manifest["research"]["max_calls"] <= 12, "Bound research calls")
    require(manifest["output"].get("answer_policy") in {"last_agent_message", "all_agent_messages"}, "Freeze answer extraction")
    require(manifest["output"].get("format") in {"json", "free_text"}, "Freeze output contract")
    workloads = manifest.get("workloads")
    require(isinstance(workloads, list) and 0 < len(workloads) <= 16, "Use bounded workloads")
    ids = set()
    tasks_total = 0
    for workload in workloads:
        ident = workload.get("id", "")
        require(re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", ident) and ident not in ids, "Duplicate/invalid workload")
        ids.add(ident)
        require(Path(workload.get("source_root", "")).is_absolute(), "Source roots must be absolute")
        snapshots = workload.get("snapshots")
        require(isinstance(snapshots, list) and snapshots, "Freeze source snapshots")
        snapshot_ids = set()
        for snapshot in snapshots:
            sid = snapshot.get("id", "")
            require(re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", sid) and sid not in snapshot_ids, "Duplicate/invalid snapshot")
            snapshot_ids.add(sid)
            require(isinstance(snapshot.get("files"), dict) and snapshot["files"], "Missing frozen source files")
            for name, value in snapshot["files"].items():
                relative(name)
                require(re.fullmatch(r"[a-f0-9]{64}", value), "Invalid source fingerprint")
        tasks = workload.get("tasks")
        require(isinstance(tasks, list) and tasks and tasks[0].get("phase") == "learn", "First task must count learning")
        require(tasks[0].get("snapshot") == snapshots[0]["id"], "First task must use initial source snapshot")
        task_ids = set()
        learning = {}
        phases = []
        for task in tasks:
            tid = task.get("id", "")
            require(re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", tid) and tid not in task_ids, "Duplicate/invalid task")
            task_ids.add(tid)
            require(task.get("phase") in {"learn", "reuse"} and task.get("scenario") in SCENARIOS, "Unknown task phase/scenario")
            phases.append(task["phase"])
            require(task.get("snapshot") in snapshot_ids and task.get("expected_use") in USES, "Missing task source/use expectation")
            if manifest["track"] == "natural":
                require(task["expected_use"] == "optional", "Natural adoption cannot force memory calls")
            _text(task.get("question"), "task question")
            require(isinstance(task.get("oracle"), dict) and isinstance(task.get("rubric"), dict), "Freeze independent quality oracle/rubric")
            oracle = task["oracle"]
            require(isinstance(oracle.get("facts"), dict) and oracle["facts"],
                    "Freeze nonempty independent facts")
            require(oracle.get("unsupported_claims_fail") is True
                    and oracle.get("stale_snapshot_fails") is True,
                    "Unsupported claims and stale evidence must fail")
            require(set(task["rubric"]) == set(DIMENSIONS), "Freeze separate completion, correctness and grounding rubrics")
            for field in DIMENSIONS:
                _text(task["rubric"].get(field), "task " + field + " rubric")
            citations = oracle.get("citations")
            snapshot = next(s for s in snapshots if s["id"] == task["snapshot"])
            policy = validate_evidence_policy(oracle, snapshot["files"])
            if "research_hints" in task:
                hints = task["research_hints"]
                require(task["expected_use"] == "optional"
                        and isinstance(hints, list) and hints
                        and all(isinstance(name, str) and name in snapshot["files"] for name in hints)
                        and len(hints) == len(set(hints)),
                        "Optional research hints must name distinct frozen source files")
            if "publication_sources" in task:
                sources = task["publication_sources"]
                require(task["phase"] == "learn" and isinstance(sources, list) and sources
                        and len(sources) == len(set(sources))
                        and set(sources) == (set(policy["scope"]) if policy["kind"] == "scoped_absence"
                                             else {c["path"] for c in citations}),
                        "Learning publication sources must match its oracle")
            if "memory_from" in task or "memory_query" in task:
                require(task["phase"] == "reuse" and task.get("memory_from") in learning,
                        "Memory must bind an earlier declared learning step")
                require(task.get("memory_query") == learning[task["memory_from"]]["question"],
                        "Memory lookup must match the declared learning question")
            if manifest["track"] == "controlled":
                if task["expected_use"] in {"required_hit", "required_stale", "required_insufficient"}:
                    require("memory_from" in task and "memory_query" in task, "Controlled reuse needs declared memory binding")
                if task["expected_use"] == "required_publication":
                    require("publication_sources" in task, "Controlled publication needs declared source scope")
            if task["phase"] == "learn":
                learning[tid] = task
        require(phases == sorted(phases) and "reuse" in phases, "Freeze finite learn-then-reuse horizon")
        tasks_total += len(tasks)
    if "main_target" in manifest:
        require(tasks_total * 3 == limits["max_attempts"], "Attempt budget must exactly match the three-arm schedule")
    else:
        require(tasks_total * 2 <= limits["max_attempts"], "Attempt budget cannot complete the paired schedule")
    planned_attempt_ids(manifest)
    # JSON round-trip rejects hidden non-serializable inputs/nonfinite numbers.
    json.dumps(manifest, allow_nan=False)
    return manifest


def validate_manifest(manifest):
    try:
        return _validate_manifest(manifest)
    except (KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise ValueError("Malformed evaluation manifest") from exc


def validate_authorization(value, run):
    protocol = run["protocol"]
    expected = {"protocol_sha256": run["protocol_sha256"],
                "revision": protocol["target"]["revision"],
                "model": protocol["client"]["model"], "limits": protocol["limits"]}
    if "main_target" in protocol:
        expected["main_revision"] = protocol["main_target"]["revision"]
    require(isinstance(value, dict) and all(value.get(k) == v for k, v in expected.items()),
            "Authorization does not bind exact protocol, target, model and limits")
    intent = value.get("owner_intent")
    require(isinstance(intent, str) and bool(intent.strip()) and len(intent) <= 4000,
            "Explicit written owner intent is required")
    return value
