"""Freeze/replay bounded offline local diagnostics; never launch a model-provider subject.

No command changes legacy gates or emits a merge-readiness/production-savings claim.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from production_delivery_core import (
    CONTRACT,
    LIMITS,
    MVP,
    case_ids,
    check_budget,
    decode,
    digest,
    execution_environment,
    extract_archive,
    file_manifest,
    fixture_tasks,
    git,
    put,
    read,
    require,
    sha,
    source_archive,
    summarize,
    validate_plan,
    verify_environment,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "benchmarks/production_delivery"
SCRIPTS = ["production_delivery.py", "production_delivery_core.py", "production_delivery_worker.py"]


def models_from_environment():
    result = {}
    for name in ["COLONY_EMBED_MODEL_DIR", "COLONY_RANK_MODEL_DIR"]:
        require(
            name in os.environ, "Explicit preprovisioned local model directory required: " + name
        )
        directory = Path(os.environ[name]).resolve()
        manifest = file_manifest(directory)
        require(any(Path(n).suffix.lower() == ".onnx" for n in manifest), "No local ONNX weights")
        for relative, value in manifest.items():
            result[str(directory / relative)] = value
    return result


def freeze(repo, candidate, destination, repetitions=25, clients=(1, 2, 4)):
    require(
        Path(repo).resolve() == ROOT, "Use the harness repository; alternate clones unsupported"
    )
    require(not git(ROOT, "status", "--porcelain"), "Commit benchmark source before freezing")
    arms = {}
    for name, ref in [("mvp", MVP), ("candidate", candidate)]:
        revision, _, manifest = source_archive(repo, ref)
        arms[name] = {"revision": revision, "source_sha256": manifest}
    plan = {
        "contract": CONTRACT,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "role": "public-synthetic-local-diagnostic",
        "benchmark_commit": git(ROOT, "rev-parse", "HEAD"),
        "harness_sha256": {n: sha(ROOT / "scripts" / n) for n in SCRIPTS},
        "fixture_sha256": file_manifest(FIXTURES),
        "tasks": fixture_tasks(FIXTURES),
        "arms": arms,
        "repetitions": repetitions,
        "clients": list(clients),
        "limits": LIMITS,
        "models_sha256": models_from_environment(),
        "model_environment": {
            k: os.environ[k] for k in ["COLONY_EMBED_MODEL_DIR", "COLONY_RANK_MODEL_DIR"]
        },
        "environment": execution_environment(),
        "comparison_boundary": "candidate vs fixed PR1, not Colony vs no Colony",
        "live_subjects": "not_implemented; separately authorized future protocol required",
    }
    plan["case_ids"] = case_ids(plan["tasks"], repetitions, clients)
    plan["plan_sha256"] = digest(plan)
    validate_plan(plan)
    put(destination, plan)
    return plan


def verify_proofs(plan, output):
    for arm, expected in plan["arms"].items():
        _, _, original = source_archive(ROOT, expected["revision"])
        require(original == expected["source_sha256"], "Claimed source hashes differ from Git")
        proof = read(output / arm / "loaded-implementation.json")
        require(
            proof["revision"] == expected["revision"]
            and proof["source_sha256"] == expected["source_sha256"],
            "Wrong loaded implementation proof",
        )
        require(
            proof["plan_sha256"] == plan["plan_sha256"]
            and proof["observations"] == len(plan["case_ids"]),
            "Incomplete/mixed source proof",
        )
        require(
            proof["execution_environment"] == plan["environment"],
            "Worker environment differs from frozen plan",
        )
        source = (output / "PRIVATE-runtime" / arm / "implementation").resolve()
        require(file_manifest(source) == expected["source_sha256"], "Source bytes changed")
        require("colony.store" in proof["module_paths"], "Missing store import proof")
        for name, path in proof["module_paths"].items():
            path = Path(path).resolve()
            require(path.is_relative_to(source), "Mixed import root")
            require(
                expected["source_sha256"].get(path.relative_to(source).as_posix()) == sha(path),
                "Loaded bytes differ: " + name,
            )


def report(plan_file, output, report_file=None):
    plan, output = read(plan_file), Path(output)
    validate_plan(plan)
    require(read(output / "execution.json")["state"] == "completed", "Incomplete collection")
    require(
        read(output / "execution.json")["plan_sha256"] == plan["plan_sha256"],
        "Execution belongs to another plan",
    )
    require(file_manifest(FIXTURES) == plan["fixture_sha256"], "Fixture changes invalidate replay")
    execution = read(output / "execution.json")
    require(
        execution["execution_environment"] == plan["environment"], "Collector environment differs"
    )
    require(
        [a["arm"] for a in execution["attempts"]] == list(plan["arms"])
        and all(a["exit_code"] == 0 for a in execution["attempts"]),
        "Missing/failed/reordered worker attempts",
    )
    require(
        type(execution["whole_process_seconds"]) in {int, float}
        and 0 <= execution["whole_process_seconds"] <= plan["limits"]["seconds"],
        "Invalid collection wall time",
    )
    require(fixture_tasks(FIXTURES) == plan["tasks"], "Fixture task contract changed")
    for name, value in plan["harness_sha256"].items():
        require(sha(ROOT / "scripts" / name) == value, "Harness changed since plan")
    verify_proofs(plan, output)
    rows = []
    for arm in plan["arms"]:
        rows.extend(
            decode(line)
            for line in (output / arm / "observations.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        )
    result = summarize(plan, rows, FIXTURES)
    result["plan_sha256"] = plan["plan_sha256"]
    result["observations_sha256"] = {
        arm: sha(output / arm / "observations.jsonl") for arm in plan["arms"]
    }
    result["whole_process_seconds"] = read(output / "execution.json")["whole_process_seconds"]
    put(report_file or output / "summary.json", result)
    return result


def collect_local(repo, plan_file, output):
    require(
        Path(repo).resolve() == ROOT, "Use the harness repository; alternate clones unsupported"
    )
    plan, output = read(plan_file), Path(output).resolve()
    validate_plan(plan)
    actual_environment = verify_environment(plan["environment"])
    require(not output.exists(), "Use a NEW output directory; no resume/overwrite")
    for name, value in plan["harness_sha256"].items():
        require(sha(ROOT / "scripts" / name) == value, "Harness changed")
    require(file_manifest(FIXTURES) == plan["fixture_sha256"], "Fixture changed")
    require(fixture_tasks(FIXTURES) == plan["tasks"], "Fixture task contract changed")
    for path, value in plan["models_sha256"].items():
        require(sha(Path(path)) == value, "Pinned local model changed")
    output.mkdir()
    put(output / "frozen-plan.json", plan)
    started, process = time.monotonic(), None
    record = {
        "contract": CONTRACT,
        "plan_sha256": plan["plan_sha256"],
        "state": "incomplete",
        "attempts": [],
        "provider_calls": 0,
        "execution_environment": actual_environment,
    }
    try:
        check_budget(output, started, time.monotonic())
        runtime = output / "PRIVATE-runtime"
        runtime.mkdir()
        for arm, expected in plan["arms"].items():
            private, public = runtime / arm, output / arm
            private.mkdir()
            public.mkdir()
            revision, archive, manifest = source_archive(repo, expected["revision"])
            require(
                revision == expected["revision"] and manifest == expected["source_sha256"],
                "Git source differs",
            )
            extract_archive(archive, private / "implementation")
            work = private / "state"
            work.mkdir()
            env = {
                **os.environ,
                **plan["model_environment"],
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            env.pop("PYTHONPATH", None)
            args = [
                sys.executable,
                str(ROOT / "scripts/production_delivery_worker.py"),
                str(Path(plan_file).resolve()),
                arm,
                str(private / "implementation"),
                str(FIXTURES),
                str(work),
                str(public),
            ]
            attempt = {
                "arm": arm,
                "started_utc": datetime.now(timezone.utc).isoformat(),
                "command": args,
            }
            record["attempts"].append(attempt)
            with (
                (public / "stdout.txt").open("x", encoding="utf-8") as stdout,
                (public / "stderr.txt").open("x", encoding="utf-8") as stderr,
            ):
                process = subprocess.Popen(args, cwd=private, env=env, stdout=stdout, stderr=stderr)
                while process.poll() is None:
                    check_budget(output, started, time.monotonic())
                    time.sleep(0.05)
                attempt["exit_code"] = process.returncode
                require(process.returncode == 0, "Worker failed; retain all partial observations")
                process = None
            check_budget(output, started, time.monotonic())
        record["state"] = "completed"
    except Exception as exc:
        record["error"] = type(exc).__name__ + ": " + str(exc)
        raise
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            record["attempts"][-1]["exit_code"] = process.returncode
            record["attempts"][-1]["terminated_by_ceiling_or_error"] = True
        record["whole_process_seconds"] = time.monotonic() - started
        record["finished_utc"] = datetime.now(timezone.utc).isoformat()
        put(output / "execution.json", record)
    return report(plan_file, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("freeze", help="Freeze local diagnostic inputs; no measurements")
    prepare.add_argument("--repo", type=Path, default=ROOT)
    prepare.add_argument("--candidate", default=MVP)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--repetitions", type=int, default=25)
    local = commands.add_parser(
        "run-local", help="Collect bounded offline measurements; never a live agent trial"
    )
    local.add_argument("--repo", type=Path, default=ROOT)
    local.add_argument("--plan", type=Path, required=True)
    local.add_argument("--output", type=Path, required=True)
    replay = commands.add_parser("report", help="Audit existing local observations; no subjects")
    replay.add_argument("--plan", type=Path, required=True)
    replay.add_argument("--output", type=Path, required=True)
    replay.add_argument(
        "--report-file",
        type=Path,
        required=True,
        help="NEW report file; old summaries remain immutable",
    )
    args = parser.parse_args()
    if args.command == "freeze":
        result = freeze(args.repo, args.candidate, args.output, args.repetitions)
        print(json.dumps({"plan_sha256": result["plan_sha256"], "measurement_launched": False}))
    elif args.command == "run-local":
        result = collect_local(args.repo, args.plan, args.output)
        print(json.dumps(result, indent=2))
        return int(any(a["failed"] for a in result["arms"].values()))
    else:
        result = report(args.plan, args.output, args.report_file)
        print(json.dumps(result, indent=2))
        return int(any(a["failed"] for a in result["arms"].values()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
