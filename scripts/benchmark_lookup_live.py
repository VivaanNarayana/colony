"""Alternate pinned main/feature implementations in actual Colony CLI sessions.

Uses the original task set, subject prompts and native usage accounting. Both
implementations start with empty independent stores over the same frozen corpus.
No retries or best-of selection: an incomplete trial is retained and fails closed.
"""

import argparse
import hashlib
import io
import json
import os
import subprocess
import time
import zipfile
from pathlib import Path

import httpx
import live_benchmark as live


def git(*args):
    return subprocess.check_output(["git", *args], cwd=live.ROOT, text=True).strip()


def activate(arm):
    live.RUN, live.OUT, live.PORT = arm["runtime"], arm["output"], arm["port"]
    os.environ["PYTHONPATH"] = str(arm["source"])


def wait_ready(process, port):
    for _ in range(1500):
        if process.poll() is not None:
            raise RuntimeError("Benchmark server exited; inspect its private server log")
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.2).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.1)
    raise RuntimeError("Server startup timed out")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True, help="Fresh private runtime directory")
    parser.add_argument("--output", type=Path, required=True, help="Fresh result directory")
    parser.add_argument("--baseline-ref", default="origin/main")
    parser.add_argument("--port", type=int, default=17561)
    args = parser.parse_args()
    runtime, output = args.work.resolve(), args.output.resolve()
    if runtime.exists() or output.exists():
        parser.error("Use fresh runtime and output directories; incomplete runs must be retained")
    if git("status", "--porcelain", "--untracked-files=no"):
        parser.error("Commit tracked changes before measuring the feature implementation")
    if not live.CODEX:
        parser.error("An authorized Codex CLI ChatGPT login is required")
    baseline = git("rev-parse", args.baseline_ref)
    feature = git("rev-parse", "HEAD")
    runtime.mkdir(parents=True)
    output.mkdir(parents=True)
    reference = runtime / "implementation"
    archive = subprocess.check_output(
        ["git", "archive", "--format=zip", baseline, "src"], cwd=live.ROOT
    )
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        z.extractall(reference)
    arms = {
        "main": {
            "runtime": runtime / "main",
            "output": output / "main",
            "source": reference / "src",
            "commit": baseline,
            "port": args.port,
        },
        "feature": {
            "runtime": runtime / "feat",
            "output": output / "feature",
            "source": live.ROOT / "src",
            "commit": feature,
            "port": args.port + 1,
        },
    }
    live.REF = baseline
    processes, logs = [], []
    original_pythonpath = os.environ.get("PYTHONPATH")
    try:
        for name, arm in arms.items():
            activate(arm)
            tasks = live.setup()
            metadata_path = live.OUT / "metadata.json"
            metadata = json.loads(metadata_path.read_text())
            metadata.update(
                implementation_commit=arm["commit"],
                arm=name,
                method="24 main/feature pairs; alternating first arm; independent empty stores",
                excluded_preflight="None; every completed task is retained; no retries",
            )
            metadata_path.write_text(json.dumps(metadata, indent=2))
            manifest = {
                p.relative_to(live.RUN / "repo").as_posix(): hashlib.sha256(
                    p.read_bytes()
                ).hexdigest()
                for p in (live.RUN / "repo").rglob("*")
                if p.is_file()
            }
            (live.OUT / "corpus-sha256.json").write_text(json.dumps(manifest, indent=2))
            log = (live.RUN / "server.log").open("w", encoding="utf-8")
            logs.append(log)
            process = subprocess.Popen(
                [
                    str(live.PYTHON),
                    "-m",
                    "colony.cli",
                    "serve",
                    "--repo",
                    str(live.RUN / "repo"),
                    "--data",
                    str(live.RUN / "server"),
                    "--issuer",
                    str(live.RUN / "admin/issuer.pub"),
                    "--port",
                    str(live.PORT),
                ],
                stdout=log,
                stderr=log,
            )
            processes.append(process)
            wait_ready(process, live.PORT)
            subprocess.run(
                [
                    str(live.PYTHON),
                    "-m",
                    "colony.cli",
                    "--home",
                    str(live.RUN / "agent"),
                    "join",
                    str(live.RUN / "member.json"),
                    "--issuer",
                    str(live.RUN / "admin/issuer.pub"),
                ],
                check=True,
                capture_output=True,
            )
        protocol = {
            "baseline_commit": baseline,
            "feature_commit": feature,
            "corpus_commit": baseline,
            "tasks": [t["task_id"] for t in tasks],
            "sessions": 48,
            "order": "main then feature on even task indexes, reversed on odd indexes",
            "gates": [
                "full suite passes",
                "controlled local lookup latency lower",
                "live core-fact correctness does not decrease",
                "live gross input+output tokens lower",
                "live summed subject wall time lower",
            ],
            "limits": "8 million aggregate tokens, 240s per task; no best-of runs or retries",
            "grading": "Primary-assistant core-fact review; also audit citation paths and protocol",
            "limitations": "Observed 24-question regression suite; sampling and cache variation remain",
        }
        (output / "protocol.json").write_text(json.dumps(protocol, indent=2))
        tokens = 0
        for n, task in enumerate(tasks):
            for name in ["main", "feature"] if n % 2 == 0 else ["feature", "main"]:
                if tokens >= live.MAX_TOKENS:
                    raise RuntimeError("Aggregate token stop reached")
                activate(arms[name])
                print(json.dumps({"arm": name, "task": task["task_id"]}), flush=True)
                row = live.run_subject(task, "colony")
                tokens += row["tokens"]
                events = [
                    json.loads(s)
                    for s in (live.OUT / f"{task['task_id']}.colony.events.jsonl")
                    .read_text(encoding="utf-8")
                    .splitlines()
                    if s.startswith("{")
                ]
                research = [
                    e["item"]
                    for e in events
                    if e.get("type") == "item.completed" and e["item"].get("server") == "repository"
                ]
                if len(research) > 6 or row["ask_calls"] != 1:
                    raise RuntimeError(
                        "Tool-budget/ask-once protocol violation; retained as failure"
                    )
        (output / "completed.json").write_text(json.dumps({"sessions": 48, "tokens": tokens}))
    finally:
        for process in processes:
            process.terminate()
            process.wait(timeout=15)
        for log in logs:
            log.close()
        if original_pythonpath is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = original_pythonpath


if __name__ == "__main__":
    main()
