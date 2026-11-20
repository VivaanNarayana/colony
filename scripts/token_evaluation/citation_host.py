"""Own the candidate's task adapter until the native worker has completed."""
import json
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from .adapters import task_args
from .evidence import _objects
from .task_evidence import handle


def received_references(log):
    """Only handles in successful, worker-received read results can acknowledge delivery."""
    seen = {}
    with Path(log).open(encoding="utf-8") as stream:
        for line in stream:
            event = json.loads(line)
            item = event.get("item") or {}
            if (event.get("type") != "item.completed" or item.get("type") != "mcp_tool_call"
                    or item.get("server") != "colony" or item.get("tool") != "read"):
                continue
            for value in _objects(item):
                pages = value.get("results", [value])
                if isinstance(pages, list):
                    for page in pages:
                        if isinstance(page, dict) and isinstance(page.get("content"), str) and handle(page):
                            seen[handle(page)] = None
    return list(seen)[-256:]


def enabled(protocol, arm, task):
    intent = task.get("host_intent", {})
    return (arm != "no_colony" and task["phase"] == "learn"
            and (not intent.get("direct_path") or intent.get("remember") is True)
            and "--reuse-citations" in protocol["target"].get("mcp_args", []))


def endpoint(root, identity):
    return Path(root) / "private" / (identity + ".endpoint")


def command(protocol, repo, team, task, files, channel):
    return [protocol["target"]["python"], *task_args(protocol, repo, team, task, files),
            "--completion-channel", str(channel)]


@contextmanager
def completion(args, channel, env, stderr, deadline):
    if not args:
        yield lambda answer, log=None: None
        return
    with subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, env=env,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as child:
        try:
            ready_by = min(deadline, time.monotonic() + 30)
            while not channel.exists():
                if child.poll() is not None or time.monotonic() >= ready_by:
                    raise ValueError("Task completion host unavailable")
                time.sleep(0.03)

            def finish(answer, log=None):
                from colony.citations import answer_citations

                try:
                    value = json.loads(answer) if answer else {}
                except (ValueError, RecursionError):
                    value = {}
                sources = answer_citations(value)
                payload = sources
                if log is not None:
                    payload = {"sources": sources, "received_refs": received_references(log) if answer else []}
                raw = json.dumps(payload, ensure_ascii=False).encode()
                if len(raw) > 131070:
                    raw = b"[]"
                try:
                    timeout = max(0.1, min(20, deadline - time.monotonic()))
                    out, _ = child.communicate(raw + b"\n", timeout=timeout)
                    if child.returncode == 0:
                        result = json.loads(out)
                        if isinstance(result, dict) and type(result.get("stored")) is bool:
                            return result
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    pass
                return {"stored": False, "reason": "host_unavailable"}

            yield finish
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            channel.unlink(missing_ok=True)
