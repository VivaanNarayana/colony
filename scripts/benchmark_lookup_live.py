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

BOOTSTRAP = '''"""Load only this arm's frozen implementation, independent of inherited PYTHONPATH."""
import hashlib
import importlib
import json
import os
import sys
from pathlib import Path

config = json.loads(Path(__file__).with_suffix(".json").read_text(encoding="utf-8"))
source = Path(config["source"]).resolve()
sys.path.insert(0, str(source))
cli = importlib.import_module("colony.cli")
role = "mcp" if "mcp" in sys.argv[1:] else "server" if "serve" in sys.argv[1:] else "cli"
if role in {"mcp", "server"}:
    importlib.import_module("colony." + role)
paths = {name: str(Path(module.__file__).resolve())
         for name, module in sys.modules.items()
         if (name == "colony" or name.startswith("colony.")) and getattr(module, "__file__", None)}
if not all(Path(path).is_relative_to(source) for path in paths.values()):
    raise RuntimeError("Implementation import escaped its frozen source")
hashes = {p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
          for p in source.rglob("*.py")}
if hashes != config["source_sha256"]:
    raise RuntimeError("Frozen implementation changed")
proof = {"revision": config["revision"], "role": role, "pid": os.getpid(),
         "source": str(source), "module_paths": paths, "source_sha256": hashes}
Path(__file__).with_name(role + "-implementation.json").write_text(json.dumps(proof, indent=2))
cli.main()
'''


def write_bootstrap(source, path, revision):
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in source.rglob("*.py")
    }
    path.write_text(BOOTSTRAP, encoding="utf-8")
    path.with_suffix(".json").write_text(
        json.dumps(
            {"source": str(source.resolve()), "revision": revision, "source_sha256": manifest}
        ),
        encoding="utf-8",
    )
    return manifest


def record_implementation(arm, role, task_id=None):
    proof = json.loads((arm["runtime"] / f"{role}-implementation.json").read_text())
    if proof["revision"] != arm["commit"] or proof["source_sha256"] != arm["manifest"]:
        raise RuntimeError("Loaded implementation does not match pinned revision")
    if "colony." + ("mcp" if role == "mcp" else "server") not in proof["module_paths"]:
        raise RuntimeError("Expected implementation module was not loaded")
    proof["task_id"] = task_id
    path = arm["output"] / "loaded-implementations.json"
    proofs = json.loads(path.read_text()) if path.exists() else []
    proofs.append(proof)
    path.write_text(json.dumps(proofs, indent=2), encoding="utf-8")


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
    adapter = runtime / "research-adapter.py"
    adapter.write_bytes(
        subprocess.check_output(
            ["git", "show", f"{feature}:scripts/benchmark_repo_mcp.py"], cwd=live.ROOT
        )
    )
    adapter_hash = hashlib.sha256(adapter.read_bytes()).hexdigest()
    for name, revision in [("main", baseline), ("feature", feature)]:
        archive = subprocess.check_output(
            ["git", "archive", "--format=zip", revision, "src"], cwd=live.ROOT
        )
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            z.extractall(runtime / "implementation" / name)
    arms = {
        "main": {
            "runtime": runtime / "main",
            "output": output / "main",
            "source": runtime / "implementation/main/src",
            "commit": baseline,
            "port": args.port,
        },
        "feature": {
            "runtime": runtime / "feat",
            "output": output / "feature",
            "source": runtime / "implementation/feature/src",
            "commit": feature,
            "port": args.port + 1,
        },
    }
    for arm in arms.values():
        arm["bootstrap"] = arm["runtime"] / "implementation.py"
        arm["manifest"] = write_bootstrap(arm["source"], arm["bootstrap"], arm["commit"])
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
                import_isolation="Explicit frozen-source launcher; actual server/MCP module paths and hashes verified",
                research_adapter_sha256=adapter_hash,
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
                    str(arm["bootstrap"]),
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
            record_implementation(arm, "server")
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
            "research_adapter_sha256": adapter_hash,
            "order": "main then feature on even task indexes, reversed on odd indexes",
            "gates": [
                "full suite passes",
                "controlled local lookup latency lower",
                "live core-fact correctness does not decrease",
                "live gross input+output tokens lower",
                "live summed subject wall time lower",
                "feature follows requested tool protocol",
            ],
            "limits": "8 million aggregate tokens, 240s per task; no best-of runs or retries",
            "grading": "Primary-assistant core-fact review; also audit citation paths and protocol",
            "limitations": "Observed 24-question regression suite; sampling and cache variation remain",
            "implementation_verification": "Frozen exports of both revisions; explicit launcher sets sys.path and records actual module paths and source SHA256 before serving; verified for both servers and every subject MCP session",
            "model_failures": "Retain all model policy failures and finish all pairs. Research adapter delivers at most 6 operations per session; extra attempts return a budget error and their usage is counted. Feature-side protocol violations fail its gate. Execution-integrity failures still stop the run.",
        }
        (output / "protocol.json").write_text(json.dumps(protocol, indent=2))
        tokens = 0
        for n, task in enumerate(tasks):
            for name in ["main", "feature"] if n % 2 == 0 else ["feature", "main"]:
                if tokens >= live.MAX_TOKENS:
                    raise RuntimeError("Aggregate token stop reached")
                activate(arms[name])
                print(json.dumps({"arm": name, "task": task["task_id"]}), flush=True)
                row = live.run_subject(
                    task,
                    "colony",
                    implementation=arms[name]["bootstrap"],
                    max_research_calls=6,
                    repository_adapter=adapter,
                )
                record_implementation(arms[name], "mcp", task["task_id"])
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
                    path = output / "model-policy-failures.jsonl"
                    with path.open("a", encoding="utf-8") as stream:
                        stream.write(
                            json.dumps(
                                {
                                    "arm": name,
                                    "task_id": task["task_id"],
                                    "research_calls": len(research),
                                    "ask_calls": row["ask_calls"],
                                }
                            )
                            + "\n"
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
