"""Versioned, provider-free production delivery measurement primitives.

This is separate from the historical eight gates and never publishes statuses.
Structured fixture checks cannot establish natural-language/agent correctness.
"""

import hashlib
import io
import json
import math
import os
import platform
import re
import shutil
import stat
import statistics
import subprocess
import sys
import zipfile
from importlib.metadata import version
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

CONTRACT = "colony.production-delivery/1"
MVP = "202745a92f6c6c59e4c990321be9c5112697594e"
LIMITS = {"seconds": 600, "artifact_bytes": 1 << 30, "free_bytes": 5 << 30, "clients": 4}
RANK_QUESTION = "Request timeout retries"


def execution_environment():
    return {
        "python": sys.version,
        "executable": str(Path(sys.executable).resolve()),
        "platform": platform.platform(),
        "processor": platform.processor(),
        "logical_cpus": os.cpu_count(),
        "packages": {
            p: version(p)
            for p in ["fastembed", "onnxruntime", "numpy", "sqlite-vec", "mcp", "pydantic"]
        },
    }


def verify_environment(expected):
    actual = execution_environment()
    require(actual == expected, "Execution environment differs from frozen plan")
    return actual


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def decode(text):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "Duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("Nonfinite JSON constant: " + value)

    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)


def read(path):
    return decode(Path(path).read_text(encoding="utf-8"))


def put(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def relative_file(root, ref):
    require(isinstance(ref, str) and ref and ref.isprintable(), "Invalid source ref")
    require("\\" not in ref and ":" not in ref, "Non-relative source ref")
    require(all(p not in {"", ".", ".."} for p in ref.split("/")), "Unsafe source ref")
    require(not PurePosixPath(ref).is_absolute(), "Absolute source ref")
    root = Path(root).resolve()
    path = root
    for part in ref.split("/"):
        path /= part
        require(not linked(path), "Linked source path")
    require(path.resolve().is_relative_to(root) and path.is_file(), "Missing/outside source")
    return path


def linked(path):
    return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)


def files_under(root):
    root = Path(root)
    require(not linked(root), "Linked root")
    pending = [root]
    while pending:
        directory = pending.pop()
        for path in sorted(directory.iterdir()):
            require(not linked(path), "Linked artifact")
            if path.is_dir():
                pending.append(path)
            elif path.is_file():
                yield path


def file_manifest(root):
    return {p.relative_to(root).as_posix(): sha(p) for p in files_under(root)}


def fixture_tasks(root):
    root = Path(root)
    suite = read(root / "workload.json")
    require(suite["version"] == 1, "Wrong fixture version")
    tasks = suite["tasks"]
    require(len(tasks) > 0 and len({t["id"] for t in tasks}) == len(tasks), "Duplicate/empty tasks")
    result = []
    for task in tasks:
        require(re.fullmatch(r"[a-z0-9-]+", task["id"]) is not None, "Invalid task id")
        require(re.fullmatch(r"[a-z0-9-]+", task["repository"]) is not None, "Invalid repo id")
        source = relative_file(root / "repos" / task["repository"], task["source"])
        value = read(source)[task["field"]]
        require(type(value) in {str, int, bool}, "Only explicit scalar fixture facts supported")
        result.append(
            {**task, "namespace": "delivery", "expected": value, "source_sha256": sha(source)}
        )
    return result


def citation_valid(root, target, expected_ref, expected_hash):
    """Check actual same-repository sources, numeric lines and real Markdown headings."""
    try:
        require(isinstance(target, str), "Citation must be text")
        uri = urlsplit(target)
        require(not uri.scheme and not uri.netloc and not uri.query, "External citation")
        ref = unquote(uri.path)
        require(ref == expected_ref, "Citation supports a different source")
        path = relative_file(root, ref)
        require(sha(path) == expected_hash, "Source changed")
        lines = path.read_text(encoding="utf-8").splitlines()
        fragment = unquote(uri.fragment)
        if fragment:
            match = re.fullmatch(r"L?(\d+)(?:-L?(\d+))?", fragment)
            if match:
                first, last = int(match[1]), int(match[2] or match[1])
                require(1 <= first <= last <= len(lines), "Invalid line range")
            else:
                require(path.suffix.lower() == ".md", "Heading fragment on non-Markdown")
                slugs, seen = set(), {}
                for line in lines:
                    if not re.match(r"^#{1,6}\s+", line):
                        continue
                    title = re.sub(r"^#{1,6}\s+|\s+#+\s*$", "", line).strip().lower()
                    title = re.sub(r"[^\w\s-]", "", title)
                    slug = re.sub(r"\s", "-", title)
                    number = seen.get(slug, 0)
                    seen[slug] = number + 1
                    slugs.add(slug + (f"-{number}" if number else ""))
                require(fragment in slugs, "Unknown heading")
        return True
    except (ValueError, OSError, UnicodeError, TypeError):
        return False


def structured_fact(task, response, root):
    """Independent JSON fixture oracle. It does NOT grade arbitrary model prose."""
    try:
        source = relative_file(root, task["source"])
        expected = read(source)[task["field"]]
        require(sha(source) == task["source_sha256"], "Fixture changed")
        content = decode(response["content"])
        actual = content[task["field"]]
        proof = response.get("sources", [])
        return (
            response.get("hit") is True
            and response.get("status") == "validated"
            and response.get("namespace") == task["namespace"]
            and type(actual) is type(expected)
            and actual == expected == task["expected"]
            and bool(proof)
            and all(sha(relative_file(root, s["ref"])) == s["content_hash"] for s in proof)
            and any(
                s.get("ref") == task["source"] and s.get("content_hash") == task["source_sha256"]
                for s in proof
            )
        )
    except (KeyError, TypeError, ValueError, OSError):
        return False


def git(root, *args):
    return subprocess.check_output(
        ["git", *args], cwd=root, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}, text=True
    ).strip()


def source_archive(repo, revision):
    revision = git(repo, "rev-parse", "--verify", revision + "^{commit}")
    require(re.fullmatch(r"[0-9a-f]{40}", revision) is not None, "Invalid revision")
    archive = subprocess.check_output(["git", "archive", "--format=zip", revision, "src"], cwd=repo)
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        manifest = {
            n: hashlib.sha256(zipped.read(n)).hexdigest()
            for n in zipped.namelist()
            if not n.endswith("/")
        }
    require("src/colony/store.py" in manifest, "Missing implementation")
    return revision, archive, manifest


def extract_archive(archive, destination):
    destination = Path(destination)
    destination.mkdir()
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        for entry in zipped.infolist():
            parts = entry.filename.rstrip("/").split("/")
            require(all(p not in {"", ".", ".."} for p in parts), "Unsafe archive")
            require(
                not PurePosixPath(entry.filename).is_absolute()
                and ":" not in entry.filename
                and "\\" not in entry.filename,
                "Unsafe archive path",
            )
            require((entry.external_attr >> 16) & 0o170000 != 0o120000, "Archive symlink")
        zipped.extractall(destination)


def verify_loaded(source, expected):
    source = Path(source).resolve()
    modules = {}
    for name, module in list(sys.modules.items()):
        if name != "colony" and not name.startswith("colony."):
            continue
        path = Path(module.__file__).resolve()
        require(path.is_relative_to(source), "Mixed Colony implementation: " + name)
        ref = path.relative_to(source).as_posix()
        require(expected.get(ref) == sha(path), "Loaded source differs: " + name)
        modules[name] = str(path)
    require("colony.store" in modules, "Store implementation not loaded")
    return modules


def case_ids(tasks, repetitions, clients):
    result = ["model-load"]
    for task in tasks:
        prefix = task["id"] + "/"
        result += [prefix + p for p in ["initialize", "empty-miss", "push"]]
        result += [prefix + f"hit-{i}" for i in range(repetitions)]
        result += [prefix + p for p in ["scope", "stale", "restart", "backup-restore"]]
        result += [prefix + f"concurrent-{n}" for n in clients]
    result += [
        f"rank-{count}-{length}"
        for count, length in [(0, 0)] + [(n, size) for n in [1, 4, 12] for size in [64, 512, 4000]]
    ]
    return result


def validate_plan(plan):
    require(plan.get("contract") == CONTRACT, "Wrong benchmark contract")
    body = {k: v for k, v in plan.items() if k != "plan_sha256"}
    require(digest(body) == plan.get("plan_sha256"), "Plan bytes/configuration changed")
    require(plan["arms"]["mvp"]["revision"] == MVP, "Moved MVP baseline")
    require(list(plan["arms"]) == ["mvp", "candidate"], "Wrong comparison arm order")
    require(
        type(plan["repetitions"]) is int and 1 <= plan["repetitions"] <= 100, "Invalid repetitions"
    )
    require(
        plan["clients"] and len(set(plan["clients"])) == len(plan["clients"]), "Invalid clients"
    )
    require(
        all(type(n) is int and 1 <= n <= LIMITS["clients"] for n in plan["clients"]),
        "Invalid concurrency",
    )
    require(plan["limits"] == LIMITS, "Operational ceilings differ")
    tasks = plan["tasks"]
    require(tasks and len({t["id"] for t in tasks}) == len(tasks), "Duplicate tasks")
    require(
        plan["case_ids"] == case_ids(tasks, plan["repetitions"], plan["clients"]),
        "Workload changed",
    )


def disk_bytes(root):
    """Sample live runtime sizes; only vanished descendants may be skipped.

    Source/evidence manifests deliberately retain the separate strict traversal.
    """
    root = Path(root)
    require(not linked(root), "Linked root")
    pending, total = [root], 0
    while pending:
        directory = pending.pop()
        try:
            require(not linked(directory), "Linked artifact")
            entries = sorted(directory.iterdir())
        except FileNotFoundError:
            if directory == root:
                raise
            continue
        for path in entries:
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            require(
                not stat.S_ISLNK(info.st_mode)
                and not (getattr(info, "st_file_attributes", 0) & 0x400),
                "Linked artifact",
            )
            if stat.S_ISDIR(info.st_mode):
                pending.append(path)
            elif stat.S_ISREG(info.st_mode):
                total += info.st_size
    return total


def check_budget(root, started, now, limits=LIMITS):
    require(now - started <= limits["seconds"], "Time ceiling reached")
    require(shutil.disk_usage(root).free >= limits["free_bytes"], "Free-space reserve reached")
    require(disk_bytes(root) <= limits["artifact_bytes"], "Artifact ceiling reached")


def operation_checks(plan, case, result, fixtures):
    """Reconstruct each check from raw observations and independent fixture bytes."""
    if case == "model-load":
        return {"models_loaded": result == {"models_loaded": True}}
    if case.startswith("rank-"):
        _, count, length = case.split("-")
        count, length = int(count), int(length)
        passages = [(RANK_QUESTION + ". ") * (length // 24 + 1) for _ in range(count)]
        passages = [p[: max(1, length - i % 3)] for i, p in enumerate(passages)]
        scores = result["scores"]
        return {
            "score_count": isinstance(scores, list) and len(scores) == count,
            "finite_float_scores": isinstance(scores, list)
            and all(type(s) is float and math.isfinite(s) for s in scores),
            "original_passages": result["passages"] == passages,
        }
    task_id, phase = case.split("/", 1)
    task = next(t for t in plan["tasks"] if t["id"] == task_id)
    root = Path(fixtures) / "repos" / task["repository"]

    def grounded(response):
        return {
            "structured_source_fact": structured_fact(task, response, root),
            "taint_preserved": response.get("tainted") is True,
            "provenance_present": "UNTRUSTED" in response.get("provenance", "").upper(),
        }

    if phase == "initialize":
        return {"initialized": result == {"initialized": True}}
    if phase == "empty-miss":
        return {"empty_miss": clean_miss(result)}
    if phase == "push":
        return {
            "stored": result.get("stored") is True
            and result.get("id") == f"{plan['tasks'].index(task) + 1:032x}"
        }
    if phase == "scope":
        return {"no_cross_namespace_return": clean_miss(result)}
    if phase == "stale":
        return {
            "changed_file_not_trusted": result.get("hit") is False
            or result.get("status") in {"stale", "unverified"}
        }
    if phase.startswith("concurrent-"):
        count = int(phase.rsplit("-", 1)[1])
        return {
            "all_clients_completed": isinstance(result, list) and len(result) == count,
            "all_structured_facts_grounded": isinstance(result, list)
            and all(structured_fact(task, r, root) for r in result),
            "all_trust_metadata_preserved": isinstance(result, list)
            and all(
                r.get("tainted") is True and "UNTRUSTED" in r.get("provenance", "").upper()
                for r in result
            ),
        }
    require(phase.startswith("hit-") or phase in {"restart", "backup-restore"}, "Unknown operation")
    return grounded(result)


def clean_miss(response):
    return (
        response.get("hit") is False
        and set(response) <= {"hit", "reason"}
        and response.get("reason") in {"no_relevant_trail", "no_pull_scope"}
    )


def summarize(plan, rows, fixtures):
    validate_plan(plan)
    expected = {(arm, case) for arm in plan["arms"] for case in plan["case_ids"]}
    seen = set()
    order = {arm: [] for arm in plan["arms"]}
    totals = {}
    for row in rows:
        key = (row["arm"], row["case_id"])
        require(key in expected and key not in seen, "Unexpected/duplicate observation")
        seen.add(key)
        order[row["arm"]].append(row["case_id"])
        require(row["plan_sha256"] == plan["plan_sha256"], "Observation from another plan")
        require(
            row["revision"] == plan["arms"][row["arm"]]["revision"],
            "Observation from another revision",
        )
        task = next((t for t in plan["tasks"] if row["case_id"].startswith(t["id"] + "/")), None)
        question = (
            task["question"]
            if task
            else RANK_QUESTION
            if row["case_id"].startswith("rank-")
            else None
        )
        require(row["question"] == question, "Changed question")
        namespace = (
            ("other" if row["case_id"].endswith("/scope") else task["namespace"]) if task else None
        )
        require(row["namespace"] == namespace, "Changed namespace")
        require(digest(row["result"]) == row["result_sha256"], "Raw result changed")
        for field in ["seconds", "cpu_seconds"]:
            value = row[field]
            require(
                type(value) in {float, int} and math.isfinite(value) and value >= 0,
                "Invalid duration",
            )
        memory = row["lifetime_peak_rss_bytes"]
        require(memory is None or (type(memory) is int and memory >= 0), "Invalid process memory")
        require(
            type(row["checks"]) is dict
            and row["checks"]
            and all(type(v) is bool for v in row["checks"].values()),
            "Invalid checks",
        )
        if row.get("error") is None:
            require(
                row["checks"] == operation_checks(plan, row["case_id"], row["result"], fixtures),
                "Oracle labels disagree with raw/source evidence",
            )
        else:
            require(
                row["checks"] in [{"operation_completed": False}, {"oracle_completed": False}],
                "Error hidden by successful check",
            )
        entry = totals.setdefault(
            row["arm"], {"observations": 0, "failed": 0, "seconds": 0, "cpu_seconds": 0}
        )
        entry["observations"] += 1
        entry["failed"] += bool(row.get("error")) or not all(row["checks"].values())
        entry["seconds"] += row["seconds"]
        entry["cpu_seconds"] += row["cpu_seconds"]
    require(seen == expected, "Incomplete observations; retain as incomplete")
    require(
        all(cases == plan["case_ids"] for cases in order.values()),
        "Observation history order changed",
    )

    def percentiles(samples):
        return {
            "samples": len(samples),
            "p50_seconds": statistics.median(samples) if samples else None,
            "p95_seconds": sorted(samples)[math.ceil(0.95 * len(samples)) - 1]
            if len(samples) >= 20
            else None,
            "p99_seconds": sorted(samples)[math.ceil(0.99 * len(samples)) - 1]
            if len(samples) >= 100
            else None,
        }

    for arm, entry in totals.items():
        selected = [r for r in rows if r["arm"] == arm]
        entry["warm_mixture"] = percentiles(
            [r["seconds"] for r in selected if "/hit-" in r["case_id"]]
        )
        entry["warm_by_task"] = {
            t["id"]: percentiles(
                [r["seconds"] for r in selected if r["case_id"].startswith(t["id"] + "/hit-")]
            )
            for t in plan["tasks"]
        }
        memory = [
            r["lifetime_peak_rss_bytes"]
            for r in selected
            if r["lifetime_peak_rss_bytes"] is not None
        ]
        entry["lifetime_peak_rss_bytes"] = max(memory) if memory else None
        entry["rank_strata"] = [
            {
                "case_id": r["case_id"],
                "seconds": r["seconds"],
                "cpu_seconds": r["cpu_seconds"],
                "passage_characters": [len(p) for p in r["result"]["passages"]],
            }
            for r in selected
            if r["case_id"].startswith("rank-") and r.get("error") is None
        ]
    return {
        "contract": CONTRACT,
        "role": "descriptive-local-synthetic-only",
        "arms": totals,
        "same_implementation": plan["arms"]["mvp"]["source_sha256"]
        == plan["arms"]["candidate"]["source_sha256"],
        "revisions": {arm: value["revision"] for arm, value in plan["arms"].items()},
        "optimization_claim": "not_established",
        "production_value": "not_established",
        "legacy_eight_gate_results": "not_evaluated_or_changed",
        "provider_tokens": None,
        "provider_dollars": None,
        "limitations": [
            "Scripted local operations; no agent workflow/compliance study",
            "Public synthetic fixtures, not held-out production",
            "No confidence-supported optimization claim or merge-readiness verdict",
            "Cold model load and process wall time are separate from warm operation samples",
            "Fixed MVP-then-candidate order; OS file-cache effects are not controlled",
        ],
    }
