"""Isolated paired warm-lookup development benchmark with independent answer checks.

No provider calls. Exit 1 for a quality failure, inconclusive result, or A/A control.
Every observation is retained; setup, model warmup and pushes are outside timing.
"""

import argparse
import hashlib
import io
import json
import os
import queue
import random
import subprocess
import sys
import threading
import zipfile
from collections import defaultdict
from pathlib import Path

from benchmark_statistics import reduction_interval, supported_improvement
from lookup_workload import assess, workload

ROOT = Path(__file__).resolve().parents[1]
HARNESS = [
    "benchmark_lookup.py",
    "benchmark_statistics.py",
    "lookup_workload.py",
    "lookup_benchmark_worker.py",
]


def git(repo, *args):
    return subprocess.check_output(["git", *args], cwd=repo)


def export_source(repo, revision, destination):
    archive = git(repo, "archive", "--format=zip", revision, "src")
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        z.extractall(destination)
    source = destination / "src"
    manifest = {
        p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in source.rglob("*.py")
    }
    return source, manifest


def harness_hashes():
    return {
        name: hashlib.sha256((ROOT / "scripts" / name).read_bytes()).hexdigest() for name in HARNESS
    }


def require_fresh_run(runtime, output, name):
    prefix = os.path.normcase(name + "-")
    if runtime.exists() or (
        output.exists()
        and any(os.path.normcase(path.name).startswith(prefix) for path in output.iterdir())
    ):
        raise ValueError("Use fresh runtime and output names; retain previous attempts")


class Worker:
    def __init__(self, source, manifest, root, spec):
        self.log = root.with_suffix(".stderr.txt").open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-u",
                str(ROOT / "scripts/lookup_benchmark_worker.py"),
                "--source",
                str(source),
                "--root",
                str(root),
                "--spec",
                str(spec),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.log,
            text=True,
            encoding="utf-8",
            env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
        )
        self.messages = queue.Queue()

        def collect():
            for line in self.process.stdout:
                self.messages.put(line)
            self.messages.put(None)

        threading.Thread(target=collect, daemon=True).start()
        try:
            self.proof = self.read()["proof"]
            if self.proof["source_sha256"] != manifest or not all(
                Path(p).is_relative_to(source.resolve())
                for p in self.proof["module_paths"].values()
            ):
                raise ValueError("Worker did not load the complete selected revision")
            if self.read() != {"ready": True}:
                raise ValueError("Worker did not finish initialization")
        except BaseException:
            self.close()
            raise

    def read(self):
        try:
            line = self.messages.get(timeout=240)
        except queue.Empty as exc:
            raise RuntimeError("Benchmark worker timed out; see retained stderr") from exc
        if line is None:
            raise RuntimeError("Benchmark worker exited; see retained stderr")
        return json.loads(line)

    def ask(self, task_id):
        self.process.stdin.write(json.dumps({"task_id": task_id}) + "\n")
        self.process.stdin.flush()
        return self.read()

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=15)
        self.process.stdout.close()
        self.process.stdin.close()
        self.log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="lookup")
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline-ref", default="origin/main")
    parser.add_argument("--feature-ref", default="HEAD")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20261021)
    parser.add_argument("--variants", type=int, default=3)
    args = parser.parse_args()
    if (
        args.repetitions < 2
        or args.variants < 1
        or not args.name
        or Path(args.name).name != args.name
    ):
        parser.error("Use at least two repetitions, one variant and a plain run name")
    repo, runtime, output = args.repo.resolve(), args.runtime.resolve(), args.output.resolve()
    require_fresh_run(runtime, output, args.name)
    if git(repo, "status", "--porcelain", "--untracked-files=no", "--", "src").strip():
        raise ValueError("Commit application changes before benchmarking")
    runtime.mkdir(parents=True)
    output.mkdir(parents=True, exist_ok=True)
    spec = workload(args.seed, args.variants)
    spec_bytes = json.dumps(spec, sort_keys=True).encode("utf-8")
    spec_path = runtime / "workload.json"
    spec_path.write_bytes(spec_bytes)
    plan = {
        "schema_version": 2,
        "evaluation_role": "public-development-contracts",
        "baseline_commit": git(repo, "rev-parse", args.baseline_ref).decode().strip(),
        "feature_commit": git(repo, "rev-parse", args.feature_ref).decode().strip(),
        "seed": args.seed,
        "repetitions": args.repetitions,
        "workload_sha256": hashlib.sha256(spec_bytes).hexdigest(),
        "harness_sha256": harness_hashes(),
    }
    (output / f"{args.name}-plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    (output / f"{args.name}-workload.json").write_bytes(spec_bytes)
    workers, manifests, rows = {}, {}, []
    try:
        for arm, key in [("main", "baseline_commit"), ("feature", "feature_commit")]:
            source, manifests[arm] = export_source(repo, plan[key], runtime / (arm + "-source"))
            workers[arm] = Worker(source, manifests[arm], runtime / arm, spec_path)
        rng = random.Random(args.seed)
        with (output / f"{args.name}-measurements.jsonl").open("w", encoding="utf-8") as log:
            for repeat in range(args.repetitions):
                cases = rng.sample(spec["cases"], len(spec["cases"]))
                for index, case in enumerate(cases):
                    arms = ["main", "feature"] if (repeat + index) % 2 == 0 else ["feature", "main"]
                    for arm in arms:
                        observation = workers[arm].ask(case["task_id"])
                        errors = assess(
                            case, observation["result"], runtime / arm / case["task_id"] / "repo"
                        )
                        row = {
                            "repeat": repeat,
                            "task_id": case["task_id"],
                            "family": case["family"],
                            "mode": arm,
                            "oracle_errors": errors,
                            **observation,
                        }
                        rows.append(row)
                        log.write(json.dumps(row) + "\n")
                        log.flush()
                print(f"Completed repetition {repeat + 1}/{args.repetitions}", flush=True)
        if harness_hashes() != plan["harness_sha256"]:
            raise ValueError("Harness changed during the run")
        clusters = defaultdict(lambda: {arm: {"seconds": 0.0} for arm in workers})
        modes = {}
        for row in rows:
            clusters[row["family"]][row["mode"]]["seconds"] += row["seconds"]
        interval = reduction_interval(clusters.values(), "seconds", seed=args.seed)
        for arm in workers:
            selected = [r for r in rows if r["mode"] == arm]
            modes[arm] = {
                "lookups": len(selected),
                "oracle_passes": sum(not r["oracle_errors"] for r in selected),
                **{
                    k: sum(r[k] for r in selected)
                    for k in ["seconds", "query_embeddings", "reranker_calls", "reranked_passages"]
                },
            }
        quality = all(not r["oracle_errors"] for r in rows)
        same = manifests["main"] == manifests["feature"]
        improved = (
            not same
            and quality
            and supported_improvement(
                modes["main"]["seconds"], modes["feature"]["seconds"], interval
            )
        )
        summary = {
            **plan,
            "modes": modes,
            "same_implementation": same,
            "implementation_isolated": True,
            "loaded_implementations": {arm: w.proof for arm, w in workers.items()},
            "quality_passed": quality,
            "lookup_improves": improved,
            "lookup_time_reduction": 1 - modes["feature"]["seconds"] / modes["main"]["seconds"],
            "latency_reduction_95pct": interval,
            "limitations": [
                "Public development fixtures, not held-out production evidence.",
                "Warm asks only; setup, push, cold start and concurrency are not timed.",
                "Family bootstrap is descriptive; repeated runs are not independent confirmation.",
            ],
        }
        (output / f"{args.name}-summary.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {k: v for k, v in summary.items() if k != "loaded_implementations"}, indent=2
            )
        )
        return 0 if improved else 1
    finally:
        for worker in workers.values():
            worker.close()


if __name__ == "__main__":
    raise SystemExit(main())
