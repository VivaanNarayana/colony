"""Explicit developer commands; preparing or reporting never starts model sessions."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

from .output import OUTPUT_CONTRACT, instruction
from .protocol import CONTRACT, collector_hashes, digest, validate_manifest


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
        "contract": CONTRACT, "track": args.track,
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
    """Null labels cannot accidentally count as successful independent review."""
    public = _read(Path(packet) / "answers.json")
    private = _read(Path(packet) / "private-map.json")
    protocols = {row["protocol_sha256"] for row in public}
    if len(protocols) != 1 or {r["case"] for r in public} != {r["case"] for r in private}:
        raise ValueError("Mismatched review packet")
    return _write(output, {"protocol_sha256": protocols.pop(), "reviewer": "", "method": "",
                           "labels": [{**row, "correct": None, "grounded": None,
                                       "format_compliant": None} for row in private]})


def bind_quality(packet, review_path, output):
    """Map independently reviewed random case IDs back without showing arms to grader."""
    public = _read(Path(packet) / "answers.json")
    private = _read(Path(packet) / "private-map.json")
    review = _read(review_path)
    lookup = {row["case"]: row for row in review["labels"]}
    if (not review.get("reviewer") or not review.get("method")
            or len(lookup) != len(review["labels"])
            or len(lookup) != len(public) or len(lookup) != len(private)
            or set(lookup) != {row["case"] for row in public}
            or set(lookup) != {row["case"] for row in private}):
        raise ValueError("Independent review must label exactly the masked cases")
    protocols = {row["protocol_sha256"] for row in public}
    if len(protocols) != 1:
        raise ValueError("Mixed review protocols")
    labels = []
    for binding in private:
        label = lookup[binding["case"]]
        fields = ("correct", "grounded", "format_compliant")
        if not all(type(label.get(key)) is bool for key in fields):
            raise ValueError("Explicit Boolean independent labels required")
        answer = next(row for row in public if row["case"] == binding["case"])
        expected_instruction = instruction(answer.get("output"))
        if (answer.get("output_instruction") != expected_instruction
                or answer.get("output_sha256") != digest({"output": answer["output"],
                    "output_instruction": expected_instruction})):
            raise ValueError("Masked output contract binding changed")
        if any(not binding.get(key) or answer.get(key) != binding[key] for key in
               ("answer_sha256", "question_sha256", "source_sha256", "citation_sha256",
                "label_sha256", "output_sha256")):
            raise ValueError("Review packet bindings changed")
        if hashlib.sha256(answer["answer"].encode()).hexdigest() != binding["answer_sha256"]:
            raise ValueError("Masked answer bytes changed")
        question = answer.get("question")
        if (not isinstance(question, str)
                or hashlib.sha256(question.encode("utf-8")).hexdigest() != binding["question_sha256"]):
            raise ValueError("Masked question bytes changed")
        # Every prospective packet carries the complete grading subject. Missing
        # sources must not disable snapshot/oracle/rubric integrity checks.
        if (not isinstance(answer.get("current_sources"), list) or "snapshot" not in answer
                or not isinstance(answer.get("oracle"), dict)
                or not isinstance(answer.get("rubric"), dict)):
            raise ValueError("Masked grading evidence is incomplete")
        try:
            sources = answer["current_sources"]
            source_hashes = {source["path"]: source["sha256"] for source in sources}
            if (len(source_hashes) != len(sources)
                    or any(hashlib.sha256(source["content"].encode()).hexdigest() != source["sha256"]
                           for source in sources)
                    or digest({"snapshot": answer["snapshot"], "files": source_hashes}) != binding["source_sha256"]
                    or digest(answer["oracle"]) != binding["citation_sha256"]
                    or digest(answer["rubric"]) != binding["label_sha256"]):
                raise ValueError("Masked grading evidence changed")
        except (KeyError, TypeError, AttributeError):
            raise ValueError("Masked grading evidence is malformed") from None
        labels.append({**binding, **{key: label[key] for key in fields}})
    return _write(output, {"protocol_sha256": protocols.pop(), "reviewer": review["reviewer"],
                           "method": review["method"], "labels": labels})


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
    command = commands.add_parser("quality-template", help="Private coordinator template; null labels always fail scoring")
    command.add_argument("--packet", required=True)
    command.add_argument("--output", required=True)
    command = commands.add_parser("bind-quality", help="Bind completed arm-blind case labels for scoring")
    command.add_argument("--packet", required=True)
    command.add_argument("--review", required=True)
    command.add_argument("--output", required=True)
    command = commands.add_parser("report", help="Require complete schedule, exact logs and independent quality labels")
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
        else:
            value = report.report(args.run, args.quality, args.output)
        print(json.dumps(value, indent=2, ensure_ascii=False))
        return 1 if value.get("ok") is False or value.get("status") == "partial" else 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(json.dumps({"error": type(exc).__name__ + ": " + str(exc)}, ensure_ascii=False))
        return 1
