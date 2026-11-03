"""Fail-closed collection scaffolding; simulated collections never certify savings."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from collections import Counter
from pathlib import Path, PurePosixPath


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
    return value


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _inside(root, relative):
    original = root / relative
    for ancestor in (original, *original.parents):
        if ancestor.is_relative_to(root) and (ancestor.is_symlink() or (hasattr(ancestor, "is_junction") and ancestor.is_junction())):
            raise ValueError("Linked fixture path rejected")
    value = original.resolve()
    if Path(relative).is_absolute() or not value.is_relative_to(root):
        raise ValueError("Fixture path escapes root")
    return value


def prepare(manifest_path, output_dir):
    from .protocol import validate_manifest
    manifest_path = Path(manifest_path).resolve(strict=True)
    manifest = validate_manifest(_read(manifest_path))
    output = Path(output_dir).resolve()
    if output.exists():
        raise ValueError("Output must be new; existing evidence is never overwritten")
    if os.name == "nt" and output.drive.lower() != "d:":
        raise ValueError("Evaluation evidence must use private D storage")
    repository = Path(__file__).resolve().parents[2]
    if output.is_relative_to(repository):
        raise ValueError("Output must be outside repository")
    frozen = []
    for workload in manifest["workloads"]:
        root = Path(workload["source_root"]).absolute()
        for snapshot in workload["snapshots"]:
            for relative, digest in snapshot["files"].items():
                source = _inside(root, f'{snapshot["id"]}/{relative}')
                if source.is_symlink() or _sha(source) != digest:
                    raise ValueError("Fixture hash mismatch")
                frozen.append((workload["id"], snapshot["id"], relative, source, digest))
    output.mkdir(parents=True)
    arms = {}
    for arm in ("no_colony", "colony"):
        workspace = output / "workspaces" / arm
        workspace.mkdir(parents=True)
        for workload, snapshot, relative, source, digest in frozen:
            destination = _inside(workspace, f"{workload}/{snapshot}/{relative}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            if _sha(destination) != digest:
                raise ValueError("Copied fixture hash mismatch")
        arms[arm] = {"workspace": str(workspace), "store": str(output / arm / "store")}
    run = {"protocol": manifest, "contract": "colony.token-evaluation-run/1",
           "manifest_sha256": _sha(manifest_path), "protocol_sha256": hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest(), "run_dir": str(output), "arms": arms,
           "fidelity": {"mode": "manual", "native_evidence": False},
           "limitations": ["Native execution requires explicit authorization and live preflight."], "files": {}}
    for path in output.rglob("*"):
        if path.is_file():
            run["files"][path.relative_to(output).as_posix()] = _sha(path)
    (output / "control").mkdir()
    (output / "logs").mkdir()
    _write(output / "run.json", run)
    return output / "run.json"


def _verify(run):
    manifest = run["protocol"]
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    if digest != run["protocol_sha256"]:
        raise ValueError("Frozen protocol drift")
    for path, expected_hash in manifest.get("collector", {}).get("files", {}).items():
        if not Path(path).is_absolute() or _sha(path) != expected_hash:
            raise ValueError("Frozen collector source drift")
    for arm in run["arms"].values():
        root = Path(arm["workspace"]).resolve(strict=True)
        for workload in manifest["workloads"]:
            for snapshot in workload["snapshots"]:
                for relative, digest in snapshot["files"].items():
                    if _sha(_inside(root, f'{workload["id"]}/{snapshot["id"]}/{relative}')) != digest:
                        raise ValueError("Frozen fixture drift")
    for workload in manifest["workloads"]:
        source_root = Path(workload["source_root"]).absolute()
        for snapshot in workload["snapshots"]:
            for relative, digest in snapshot["files"].items():
                if _sha(_inside(source_root, f'{snapshot["id"]}/{relative}')) != digest:
                    raise ValueError("Original fixture source drift")
    target = manifest["target"]
    code = "import hashlib,json,pathlib,colony; r=pathlib.Path(colony.__file__).parent; print(json.dumps({p.relative_to(r).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in r.rglob('*.py')}))"
    expected = target["runtime_hashes"]
    normalized = {key.removeprefix("colony/"): value for key, value in expected.items()}
    if not normalized:
        raise ValueError("Runtime hashes required")
    for relative in normalized:
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError("Invalid runtime path")
    checked = subprocess.run([target["python"], "-I", "-c", code, json.dumps(list(normalized))], capture_output=True, text=True, timeout=30, check=True)
    if json.loads(checked.stdout) != normalized:
        raise ValueError("Installed target runtime drift")
    client = manifest["client"]
    if _sha(client["executable"]) != client["sha256"]:
        raise ValueError("Client executable drift")


def preflight(run_dir):
    root = Path(run_dir).resolve()
    run = _read(root / "run.json")
    errors = []
    try:
        _verify(run)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        errors.append(type(exc).__name__ + ": " + str(exc))

    live = {}
    if not errors:
        from .native import preflight_live
        live = preflight_live(root)
        errors.extend(live.get("errors", []))
    receipt = {**live, "ok": not errors and live.get("ok", False), "native_ready": not errors and live.get("native_ready", False), "errors": errors, "native_evidence": False}
    _write(root / "preflight.json", receipt)
    return receipt


def execute(run_dir, authorization_path=None, auth_home=None, execute_native=False, *, launch_fixture=None):
    root = Path(run_dir).resolve()
    run = _read(root / "run.json")
    if execute_native:
        if not authorization_path or not auth_home:
            raise ValueError("Native execution requires authorization_path and explicit auth_home")
        authorization = _read(authorization_path)
        manifest = run["protocol"]
        expected = {"protocol_sha256": run["protocol_sha256"], "revision": manifest["target"]["revision"],
                    "model": manifest["client"]["model"], "limits": manifest["limits"]}
        if any(authorization.get(key) != value for key, value in expected.items()) or not authorization.get("owner_intent"):
            raise ValueError("Native authorization does not bind protocol, revision, model, limits and owner intent")
        _verify(run)
        for relative, digest in run["files"].items():
            if _sha(_inside(root.resolve(), relative)) != digest:
                raise ValueError("Frozen input drift")
        from .native import execute_live
        return execute_live(root, authorization, Path(auth_home))
    if launch_fixture is None:
        raise ValueError("Explicit provider-free launch_fixture required; native evidence disabled")
    journal = root / "journal.jsonl"
    if journal.exists():
        raise ValueError("Collection already started")
    start = time.monotonic()
    limits = run["protocol"]["limits"]
    attempts = 0
    tokens = 0
    status = "complete"
    def record(value):
        with journal.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, sort_keys=True) + "\n")
        control = root / "control"
        control.mkdir(exist_ok=True)
        identifier = "sim-" + str(value["attempt"])
        if value["event"] == "invocation":
            _write(control / (identifier + "-invocation.json"), value)
        else:
            row = {"id": identifier, "workload": value["workload"], "task": value["task"],
                   "arm": value["arm"], "state": "simulated_" + value["event"],
                   "seconds": time.monotonic() - start, "events": None, "events_sha256": None,
                   "native_evidence": False, "usage": value.get("result", {}).get("gross_tokens")}
            with (control / "attempts.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, sort_keys=True) + "\n")
    for arm in run["arms"]:
        for workload in run["protocol"]["workloads"]:
            for task in workload["tasks"]:
                if attempts >= limits["max_attempts"] or time.monotonic() - start >= limits["wall_seconds"] or tokens >= limits["gross_token_stop"]:
                    status = "stopped"
                    break
                attempts += 1
                context = {"attempt": attempts, "arm": arm, "workload": workload["id"], "task": task["id"], "native_evidence": False}
                record({**context, "event": "invocation", "usage": None})
                try:
                    event = launch_fixture(context)
                    usage = event.get("gross_tokens")
                    if type(usage) is not int or usage < 0:
                        status = "partial"
                    else:
                        tokens += usage
                    record({**context, "event": "result", "result": event})
                except Exception as exc:
                    status = "partial"
                    record({**context, "event": "error", "error_type": type(exc).__name__, "usage": None})
                if status != "complete":
                    break
            if status != "complete":
                break
        if status != "complete":
            break
    receipt = {"status": status, "attempts": attempts, "gross_tokens_observed": tokens, "billing": None, "certifying": False, "native_evidence": False}
    return _write(root / "collection.json", receipt)


def _event_reference(value):
    """Canonical run-relative log name; a basename always means the logs root."""
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value
            or any(ord(character) < 32 for character in value)):
        return None
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts) or not value.endswith(".events.jsonl"):
        return None
    if len(parts) == 1:
        return "logs/" + value
    if parts[0] != "logs":
        return None
    return PurePosixPath(value).as_posix()


def inventory(run_dir):
    root = Path(run_dir)
    parse_errors = []
    def read_rows(path):
        rows = []
        if path.exists():
            for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError("row is not an object")
                    rows.append(row)
                except ValueError:
                    parse_errors.append({"file": path.relative_to(root).as_posix(), "line": number})
        return rows
    records = read_rows(root / "journal.jsonl")
    invocations = {record["attempt"] for record in records if record.get("event") == "invocation" and "attempt" in record}
    results = {record["attempt"] for record in records if record.get("event") in ("result", "error") and "attempt" in record}
    native_records = []
    known_gross = 0
    usage_complete = True
    attempts_path = root / "control" / "attempts.jsonl"
    attempts = read_rows(attempts_path)
    references = [(row, _event_reference(row.get("events"))) for row in attempts]
    reference_counts = Counter(reference for _, reference in references if reference is not None)
    referenced = set(reference_counts)
    invalid_references = [row.get("id") for row, reference in references if reference is None]
    duplicate_references = sorted(reference for reference, count in reference_counts.items() if count > 1)
    for path in sorted((root / "logs").rglob("*.events.jsonl")):
        relative = path.relative_to(root).as_posix()
        result = {"events": relative, "events_sha256": _sha(path), "orphan": relative not in referenced, "usage": None}
        try:
            from colony.product_value import native_usage
            result["usage"] = native_usage(path)
        except (ImportError, OSError, ValueError) as exc:
            result["error_type"] = type(exc).__name__
        if result["usage"] is not None:
            known_gross += result["usage"]["known_usage"]["gross_tokens"]
            usage_complete = usage_complete and result["usage"]["usage_complete"]
        else:
            usage_complete = False
            # Preserve independently observed nonnegative components even when the
            # canonical reader refuses a partial/malformed stream; never certify them.
            components = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict) or event.get("type") not in {"turn.completed", "turn.failed"}:
                    continue
                counts = event.get("usage")
                if not isinstance(counts, dict):
                    continue
                for name in components:
                    count = counts.get(name)
                    if type(count) is int and count >= 0:
                        components[name] += count
            components["gross_tokens"] = components["input_tokens"] + components["output_tokens"]
            result["unverified_observed_components"] = components
            known_gross += components["gross_tokens"]
        native_records.append(result)
    invocation_files = list((root / "control").glob("*-invocation.json"))
    invocation_ids = {path.name.removesuffix("-invocation.json") for path in invocation_files}
    row_ids = {row.get("id") for row in attempts}
    orphan_invocations = sorted(invocation_ids - row_ids)
    orphan_rows = sorted(identifier for identifier in row_ids - invocation_ids if identifier is not None)
    orphan_logs = [row["events"] for row in native_records if row["orphan"]]
    existing_names = {row["events"] for row in native_records}
    missing_logs = [row.get("id") for row, reference in references if reference not in existing_names]
    usage_complete = (bool(native_records) and usage_complete and not parse_errors and not orphan_invocations
                      and not orphan_rows and not missing_logs and not orphan_logs and not (invocations - results)
                      and not invalid_references and not duplicate_references)
    return {"records": records, "attempts": attempts, "native_logs": native_records,
            "known_gross_tokens": known_gross, "usage_complete": usage_complete, "parse_errors": parse_errors,
            "orphan_invocations": orphan_invocations, "orphan_rows": orphan_rows,
            "orphan_logs": orphan_logs, "missing_logs": missing_logs,
            "invalid_log_references": invalid_references, "duplicate_log_references": duplicate_references,
            "orphan_attempts": sorted(invocations - results), "native_evidence": bool(native_records),
            "billing": None, "certifying": False}
