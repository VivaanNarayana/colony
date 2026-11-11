"""Explicit developer commands; preparing or reporting never starts model sessions."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

from .output import OUTPUT_CONTRACT
from .protocol import CONTRACT, collector_hashes, digest, validate_manifest
from .quality import CONTRACT as QUALITY_CONTRACT
from .quality import (
    advice_template,
    notes_template,
    render_html,
    review_policy,
    validate_advice,
    validate_notes,
    validate_packet,
)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path, value):
    path = Path(path).absolute()
    if os.name == "nt" and path.drive.lower() != "d:":
        raise ValueError("Generated evaluation files must use D storage")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write((json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    return str(path)


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          check=True, timeout=30).stdout


def template(args):
    """Freeze explicit runtime/client/collector pins without launching a native session."""
    catalog_path = Path(args.catalog).resolve(strict=True)
    catalog = _read(catalog_path)
    target_python = str(Path(args.target_python).resolve(strict=True))
    target_repo = Path(args.target_repo).resolve(strict=True)
    revision = _git(target_repo, "rev-parse", "--verify", args.revision + "^{commit}").decode().strip()
    names = _git(target_repo, "ls-tree", "-r", "--name-only", revision, "--", "src/colony").decode().splitlines()
    expected = {name.removeprefix("src/"): hashlib.sha256(
        _git(target_repo, "cat-file", "blob", revision + ":" + name)).hexdigest()
        for name in names if name.endswith(".py")}
    code = ("import hashlib,json,pathlib,colony; r=pathlib.Path(colony.__file__).parent; "
            "print(json.dumps({'colony/'+p.relative_to(r).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() "
            "for p in r.rglob('*.py')}))")
    installed = json.loads(subprocess.run([target_python, "-I", "-c", code], capture_output=True,
                                         text=True, check=True, timeout=30).stdout)
    if not expected or installed != expected:
        raise ValueError("Installed target Python sources must exactly match the declared Git revision; use a clean LF export/install")
    executable = str(Path(args.client).resolve(strict=True))
    version = subprocess.run([executable, "--version"], capture_output=True, text=True,
                             check=True, timeout=15).stdout.strip()
    workloads = catalog["workloads"]
    if args.workload:
        wanted = set(args.workload)
        workloads = [w for w in workloads if w["id"] in wanted]
        if {w["id"] for w in workloads} != wanted:
            raise ValueError("Unknown workload")
    for workload in workloads:
        workload["source_root"] = str((catalog_path.parent / workload["source_root"]).resolve(strict=True))
        if args.track == "natural":
            for task in workload["tasks"]:
                task["expected_use"] = "optional"
    value = {
        "contract": CONTRACT, "track": args.track, "quality_review": review_policy(),
        "target": {"revision": revision, "python": target_python, "runtime_hashes": installed,
                   "mcp_args": args.mcp_arg, "setup_args": args.setup_arg, "server_args": args.server_arg},
        "client": {"executable": executable, "sha256": _sha(executable), "version": version,
                   "model": args.model, "effort": args.effort},
        "collector": {"files": collector_hashes()},
        "limits": {"max_attempts": 2 * sum(len(w["tasks"]) for w in workloads),
                   "session_seconds": args.session_seconds, "wall_seconds": args.wall_seconds,
                   "gross_token_stop": args.gross_token_stop, "disk_bytes": args.disk_bytes,
                   "min_free_bytes": args.min_free_bytes, "retries": 0},
        "research": {"max_calls": args.research_calls},
        "output": {"contract": OUTPUT_CONTRACT, "answer_policy": args.answer_policy,
                   "format": args.format},
        "provenance": catalog.get("provenance"), "workloads": workloads,
    }
    validate_manifest(value)
    return {"manifest": _write(args.output, value), "protocol_sha256": digest(value),
            "native_sessions_started": 0}


def quality_template(packet, output):
    """Public-only template; never expose private attempt IDs to reviewers."""
    return _write(output, advice_template(_read(Path(packet) / "answers.json")))


def bind_quality(packet, review_path, output):
    """Bind advisory findings; semantic quality remains unresolved."""
    public = _read(Path(packet) / "answers.json")
    private = _read(Path(packet) / "private-map.json")
    advice = _read(review_path)
    validate_packet(public, private)
    validate_advice(public, advice)
    return _write(output, {"contract": QUALITY_CONTRACT,
        "protocol_sha256": public[0]["protocol_sha256"], "policy": review_policy(),
        "labels": private, "reviews": advice["reviews"]})


def human_review(packet, review_path, output, notes_path=None):
    public = _read(Path(packet) / "answers.json")
    advice = _read(review_path)
    notes = _read(notes_path) if notes_path else None
    content = render_html(public, advice, notes)
    path = Path(output).absolute()
    if os.name == "nt" and path.drive.lower() != "d:":
        raise ValueError("Generated evaluation files must use D storage")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(content)
    return str(path)


def bind_human_notes(packet, notes_path, output):
    public = _read(Path(packet) / "answers.json")
    notes = _read(notes_path)
    validate_notes(public, notes)
    return _write(output, notes)


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    command = commands.add_parser("template", help="Freeze explicit pins; only local version/import/Git reads")
    command.add_argument("--catalog", default=str(Path(__file__).resolve().parents[2] / "tests/token_evaluations/fixtures/catalog.json"))
    for flag in ("target-python", "target-repo", "revision", "client", "model", "output"):
        command.add_argument("--" + flag, required=True)
    command.add_argument("--track", choices=("natural", "controlled"), required=True)
    command.add_argument("--workload", action="append", help="Select a declared lineage; default both")
    command.add_argument("--effort", choices=("minimal", "low", "medium", "high", "xhigh"), default="low")
    command.add_argument("--answer-policy", choices=("last_agent_message", "all_agent_messages"), default="last_agent_message")
    command.add_argument("--format", choices=("free_text", "json"), default="free_text")
    for flag in ("mcp-arg", "setup-arg", "server-arg"):
        command.add_argument("--" + flag, action="append", default=[], help="Explicit allowlisted option/value; use --FLAG=--option")
    for flag, default in (("session-seconds", 120), ("wall-seconds", 1800), ("gross-token-stop", 500000),
                          ("disk-bytes", 1 << 30), ("min-free-bytes", 2 << 30), ("research-calls", 6)):
        command.add_argument("--" + flag, type=int, default=default)
    command = commands.add_parser("prepare", help="Copy and hash fixtures into a new private run directory")
    command.add_argument("--manifest", required=True)
    command.add_argument("--output", required=True)
    for name, help_text in (("preflight", "Provider-free actual runtime/MCP/configuration checks"),
                            ("inventory", "Retain known costs even for incomplete collections")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--run", required=True)
    command = commands.add_parser("run", help="ONLY after separately authorized bounded native collection")
    command.add_argument("--run", required=True)
    command.add_argument("--execute-native", action="store_true", required=True)
    command.add_argument("--authorization", required=True)
    command.add_argument("--auth-home", required=True)
    command = commands.add_parser("mask", help="Create arm-masked answers plus a private binding map")
    command.add_argument("--run", required=True)
    command.add_argument("--output", required=True)
    command = commands.add_parser("quality-template", help="Public-only advisory template; no pass/fail labels")
    command.add_argument("--packet", required=True)
    command.add_argument("--output", required=True)
    command = commands.add_parser("bind-quality", help="Bind advisory findings to frozen subjects")
    command.add_argument("--packet", required=True)
    command.add_argument("--review", required=True)
    command.add_argument("--output", required=True)
    command = commands.add_parser("human-template", help="Create nonbinding human note template")
    command.add_argument("--packet", required=True)
    command.add_argument("--output", required=True)
    command = commands.add_parser("bind-human-notes", help="Retain additive notes; never change grades")
    command.add_argument("--packet", required=True)
    command.add_argument("--notes", required=True)
    command.add_argument("--output", required=True)
    command = commands.add_parser("human-review", help="Offline masked HTML inspection; launches nothing")
    command.add_argument("--packet", required=True)
    command.add_argument("--review", required=True)
    command.add_argument("--notes")
    command.add_argument("--output", required=True)
    command = commands.add_parser("report", help="Verify complete accounting and mechanical checks; unresolved semantics block acceptance")
    command.add_argument("--run", required=True)
    command.add_argument("--quality", required=True)
    command.add_argument("--output", required=True)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        from . import report, runner
        if args.command == "template":
            value = template(args)
        elif args.command == "prepare":
            value = {"run": str(runner.prepare(args.manifest, args.output))}
        elif args.command == "preflight":
            value = runner.preflight(args.run)
        elif args.command == "inventory":
            value = runner.inventory(args.run)
        elif args.command == "run":
            value = runner.execute(args.run, args.authorization, args.auth_home, args.execute_native)
        elif args.command == "mask":
            value = report.mask(args.run, args.output)
        elif args.command == "quality-template":
            value = {"quality_template": quality_template(args.packet, args.output)}
        elif args.command == "bind-quality":
            value = {"quality": bind_quality(args.packet, args.review, args.output)}
        elif args.command == "human-template":
            value = {"human_template": _write(args.output, notes_template(_read(Path(args.packet) / "answers.json")))}
        elif args.command == "bind-human-notes":
            value = {"human_notes": bind_human_notes(args.packet, args.notes, args.output)}
        elif args.command == "human-review":
            value = {"human_review": human_review(args.packet, args.review, args.output, args.notes)}
        else:
            value = report.report(args.run, args.quality, args.output)
        print(json.dumps(value, indent=2, ensure_ascii=False))
        return 1 if value.get("ok") is False or value.get("status") == "partial" else 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"error": type(exc).__name__ + ": " + str(exc)}, ensure_ascii=False))
        return 1
