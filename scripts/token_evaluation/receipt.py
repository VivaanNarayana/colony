"""Validate retained native declarations, not process or provider attestation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

_FORWARDED = {
    "TEMP", "TMP", "HOME", "USERPROFILE", "XDG_CACHE_HOME", "HF_HOME",
    "FASTEMBED_CACHE_PATH", "COLONY_MODEL_CACHE", "PYTHONPYCACHEPREFIX",
    "TIKTOKEN_CACHE_DIR", "COLONY_EMBED_MODEL_DIR", "COLONY_RANK_MODEL_DIR",
    "COLONY_CONTEXT_TOKENIZER", "COLONY_TEST_CONTEXT_TOKENIZER", "HF_HUB_OFFLINE",
    "TRANSFORMERS_OFFLINE",
}
_REQUIRED_FORWARDED = _FORWARDED - {
    "COLONY_EMBED_MODEL_DIR", "COLONY_RANK_MODEL_DIR", "COLONY_CONTEXT_TOKENIZER",
    "COLONY_TEST_CONTEXT_TOKENIZER",
}


def _require(condition, message):
    if not condition:
        raise ValueError("Native receipt " + message)


def _settings(args):
    _require(isinstance(args, list) and all(isinstance(v, str) for v in args),
             "arguments are malformed")
    settings = {}
    for index, flag in enumerate(args):
        if flag != "-c":
            continue
        _require(index + 1 < len(args) and "=" in args[index + 1],
                 "configuration override is malformed")
        key, text = args[index + 1].split("=", 1)
        _require(key and key not in settings, "configuration override is duplicated")
        try:
            settings[key] = json.loads(text)
        except ValueError:
            raise ValueError("Native receipt configuration override is malformed") from None
    return settings


def _prompt(root, invocation, protocol, task, arm):
    from .native import _prompt as build_prompt

    path = root / "prompts" / (invocation["id"] + ".txt")
    try:
        _require(not any(p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction())
                         for p in (path, *path.parents) if p.is_relative_to(root)),
                 "retained prompt is linked")
        raw = path.read_bytes()
        _require(hashlib.sha256(raw).hexdigest() == invocation.get("prompt_sha256"),
                 "retained prompt hash changed")
        text = raw.decode("utf-8")
    except (OSError, UnicodeError):
        raise ValueError("Native receipt retained prompt is missing or invalid") from None
    empty_hash = hashlib.sha256(b"").hexdigest()
    guidance_hash = invocation.get("guidance_sha256")
    if arm == "no_colony" or guidance_hash == empty_hash:
        _require(guidance_hash == empty_hash, "unexpected or missing captured guidance")
        guidance = ""
    else:
        # Isolate the captured guidance before the task text. Rebuild the rest
        # from the frozen recipe, never live AGENTS files.
        prefix, suffix = build_prompt(protocol, task, "\0").split("\0", 1)
        _require(text.startswith(prefix) and text.endswith(suffix),
                 "frozen prompt body changed")
        guidance = text[len(prefix):len(text) - len(suffix)]
        _require(bool(guidance) and hashlib.sha256(guidance.encode()).hexdigest() == guidance_hash,
                 "captured guidance hash changed")
    _require(text == build_prompt(protocol, task, guidance), "frozen prompt body changed")


def validate_native_receipts(root, run, manifest, rows):
    """Require a consistent frozen recipe and the collector's full joint schedule.

    Retained argument strings and environment *names* are declarations. This does
    not establish inherited environment values, actual process behavior, daemon
    execution or provider billing. Relocated read-only replay uses captured launch
    paths from run.run_dir while reading prompt bytes in the replay directory.
    """
    from .native import _client_args

    root = Path(root).resolve()
    launch = run.get("run_dir")
    _require(isinstance(launch, str) and Path(launch).is_absolute(), "launch root is missing")
    launch = Path(launch)
    expected = []
    tasks = {}
    for wi, workload in enumerate(manifest["workloads"]):
        for ti, task in enumerate(workload["tasks"]):
            tasks[(workload["id"], task["id"])] = (workload, task)
            arms = ("no_colony", "colony") if (wi + ti) % 2 == 0 else ("colony", "no_colony")
            expected.extend((arm, workload["id"], task["id"]) for arm in arms)
    _require([(r["arm"], r["workload"], r["task"]) for r in rows] == expected,
             "joint attempt order changed")
    common_forwarded = None
    for row in rows:
        try:
            invocation = json.loads((root / "control" / (row["id"] + "-invocation.json")).read_text(encoding="utf-8"))
            args = invocation.get("args")
            settings = _settings(args)
            names = settings.get("mcp_servers.repository.env_vars")
            _require(isinstance(names, list) and all(isinstance(n, str) for n in names)
                     and len(names) == len(set(names))
                     and _REQUIRED_FORWARDED <= set(names) <= _FORWARDED,
                     "forwarded environment names are invalid")
            if common_forwarded is None:
                common_forwarded = names
            _require(names == common_forwarded, "forwarded environment names differ across attempts")
            workload, task = tasks[(row["workload"], row["task"])]
            snapshot = next(s for s in workload["snapshots"] if s["id"] == task["snapshot"])
            repo = launch / "active" / row["arm"] / row["workload"]
            workspace = launch / "sessions" / row["id"]
            _require(invocation.get("client_workspace") == str(workspace), "client workspace changed")
            role = "a" if task["phase"] == "learn" else "b"
            config = invocation.get("configuration")
            _require((config is None if row["arm"] == "no_colony" else
                      isinstance(config, dict) and config.get("mode") == "managed"
                      and config.get("role") == role), "configuration role changed")
            team = {"homes": {r: str(launch / "private" / row["workload"] / "team" / ("agent-" + r))
                              for r in ("a", "b")}}
            env = {"CODEX_HOME": str(launch / "private/client"), **dict.fromkeys(names, "")}
            expected_args = _client_args(manifest, repo, team, task, launch, row["arm"], env,
                                         snapshot["files"], client_workspace=workspace)
            _require(args == expected_args, "arguments differ from the frozen configuration")
            _prompt(root, invocation, manifest, task, row["arm"])
        except (OSError, KeyError, TypeError, StopIteration):
            raise ValueError("Native receipt is incomplete or malformed") from None
