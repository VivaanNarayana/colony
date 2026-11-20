"""Own the candidate's task adapter until the native worker has completed."""
import json
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from .adapters import task_args


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
        yield lambda answer: None
        return
    with subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, env=env,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as child:
        try:
            ready_by = min(deadline, time.monotonic() + 30)
            while not channel.exists():
                if child.poll() is not None or time.monotonic() >= ready_by:
                    raise ValueError("Task completion host unavailable")
                time.sleep(0.03)

            def finish(answer):
                try:
                    value = json.loads(answer) if answer else {}
                except ValueError:
                    value = {}
                sources = value.get("sources", []) if isinstance(value, dict) else []
                raw = json.dumps(sources, ensure_ascii=False).encode()
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
