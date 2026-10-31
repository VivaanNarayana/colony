import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx
import uvicorn

from colony.capture import scrubbed_tail
from colony.client import Client, MemoryUnavailable
from colony.memory import MemoryConflict, MemoryForbidden, MemoryInvalid, MemoryNotFound
from colony.models import Grant, Scope, Trail
from colony.security import generate_keys, issue, private_write
from colony.validation import snapshot


def parser():
    p = argparse.ArgumentParser(description="Colony: shared memory for coding agents")
    p.add_argument(
        "--home",
        type=Path,
        default=Path(os.environ.get("COLONY_HOME", str(Path.home() / ".colony"))),
    )
    sub = p.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Create issuer keys for the team administrator")
    init.add_argument("--dir", type=Path, required=True)
    grant = sub.add_parser("issue", help="Issue a signed, expiring agent keyfile")
    grant.add_argument("--issuer", type=Path, required=True)
    grant.add_argument("--name", required=True)
    grant.add_argument("--server", default="http://127.0.0.1:7331")
    grant.add_argument("--push", action="append", default=[])
    grant.add_argument("--pull", action="append", default=[])
    grant.add_argument("--capture", choices=["none", "outputs"], default="outputs")
    grant.add_argument("--days", type=int, default=30)
    grant.add_argument("--out", type=Path, required=True)
    join = sub.add_parser("join")
    join.add_argument("keyfile", type=Path)
    join.add_argument(
        "--issuer", type=Path, required=True, help="Trusted public key, obtained separately"
    )
    serve = sub.add_parser("serve")
    serve.add_argument("--data", type=Path, required=True)
    serve.add_argument("--repo", type=Path, required=True)
    serve.add_argument("--issuer", type=Path, required=True)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=7331)
    serve.add_argument("--max-age", type=int, default=604800)
    ask = sub.add_parser("ask")
    ask.add_argument("question")
    ask.add_argument("--ns")
    ask.add_argument(
        "--evidence-files",
        action="store_true",
        help="Include bounded complete current source files when small",
    )
    push = sub.add_parser("push", help="Push a JSON trail, with optional local file fingerprints")
    push.add_argument("file", type=Path)
    push.add_argument("--ns", required=True)
    push.add_argument("--repo", type=Path, default=Path.cwd())
    push.add_argument("--source", action="append", default=[])
    run = sub.add_parser(
        "run", help="Capture only command output; never trace inputs or tool calls"
    )
    run.add_argument("--capture", choices=["outputs"], default="outputs")
    run.add_argument("--ns", required=True)
    run.add_argument("--question", required=True)
    run.add_argument("--repo", type=Path, default=Path.cwd())
    run.add_argument("--source", action="append", default=[])
    run.add_argument("cmd", nargs=argparse.REMAINDER)
    sub.add_parser("retry", help="Retry encrypted, scrubbed offline pushes")
    mcp = sub.add_parser("mcp", help="Run ask/push tools on MCP stdio")
    context = mcp.add_mutually_exclusive_group()
    context.add_argument(
        "--evidence-files", action="store_true", help="Include bounded complete small cited files"
    )
    context.add_argument("--compact", dest="evidence_files", action="store_false")
    mcp.set_defaults(evidence_files=False)
    mcp.add_argument("--workflow-namespace", help="Bind an opt-in workflow to one namespace")
    mcp.add_argument("--workflow-repo", type=Path, help="Local project for workflow evidence")
    mcp.add_argument(
        "--workflow-read-only", action="store_true", help="Expose lookup only in workflow mode"
    )
    setup = sub.add_parser("setup", help="Configure a project to use Colony with a coding agent")
    clients = setup.add_subparsers(dest="setup_client", required=True)
    codex = clients.add_parser("codex", help="Manage project-scoped Codex MCP and guidance")
    actions = codex.add_subparsers(dest="setup_action", required=True)
    install = actions.add_parser("install", help="Install managed project configuration")
    install.add_argument("--repo", type=Path, required=True)
    install.add_argument("--ns", required=True)
    install.add_argument("--dry-run", action="store_true")
    install.add_argument("--read-only", action="store_true")
    for action in ("status", "uninstall"):
        command = actions.add_parser(action)
        command.add_argument("--repo", type=Path, required=True)
        if action == "uninstall":
            command.add_argument("--dry-run", action="store_true")
    memory = sub.add_parser(
        "memory", help="Explicit inspection, feedback, and authored corrections"
    )
    operations = memory.add_subparsers(dest="memory_action", required=True)
    for action in ("inspect", "feedback", "correct", "retract", "restore"):
        operation = operations.add_parser(action)
        operation.add_argument("id")
        operation.add_argument("--ns", required=True)
        if action != "inspect":
            operation.add_argument("--note", default="")
            operation.add_argument("--operation-id")
        if action == "feedback":
            operation.add_argument(
                "--verdict", required=True, help="helpful, incorrect, or outdated"
            )
        if action in {"correct", "retract", "restore"}:
            operation.add_argument("--expected-revision", required=True)
        if action == "correct":
            operation.add_argument("file", type=Path)
            operation.add_argument("--repo", type=Path, default=Path.cwd())
            operation.add_argument("--source", action="append", default=[])
    usage = sub.add_parser(
        "usage", help="Opt-in local usage metadata and self-reported assessments"
    )
    actions = usage.add_subparsers(dest="usage_action", required=True)
    enable = actions.add_parser("enable")
    enable.add_argument("--retention-days", type=int, default=30)
    enable.add_argument("--max-events", type=int, default=10000)
    for action in ("disable", "status", "purge"):
        actions.add_parser(action)
    review = actions.add_parser(
        "review", help="Assess one observed lookup; not a truth certificate"
    )
    review.add_argument("event_id")
    review.add_argument("--verdict", required=True, choices=("useful", "incorrect", "irrelevant"))
    report = sub.add_parser("report", help="Local usage and explicitly imported matched evidence")
    report.add_argument("--since", help="Inclusive UTC date or timestamp with timezone")
    report.add_argument("--until", help="Exclusive UTC date or timestamp with timezone")
    report.add_argument("--format", choices=("text", "json"), default="text")
    report.add_argument("--output", type=Path, help="New output file; never overwrite")
    compare = report.add_subparsers(dest="report_action").add_parser(
        "compare", help="Read an explicit matched manifest/native-log inventory; no agent runs"
    )
    compare.add_argument("--manifest", type=Path, required=True)
    compare.add_argument("--logs", type=Path, required=True)
    compare.add_argument("--format", choices=("text", "json"), default=argparse.SUPPRESS)
    compare.add_argument(
        "--output", type=Path, default=argparse.SUPPRESS, help="New output file; never overwrite"
    )
    return p


def execute(args):
    if args.command == "usage":
        from colony.usage import UsageJournal

        journal = UsageJournal(args.home)
        if args.usage_action == "enable":
            return journal.enable(retention_days=args.retention_days, max_events=args.max_events)
        if args.usage_action == "review":
            return journal.review(args.event_id, args.verdict)
        return getattr(journal, args.usage_action)()
    if args.command == "report":
        from colony.usage_report import make_report, parse_time, render_text

        if args.report_action == "compare":
            from colony.product_value import comparison_report, render_comparison

            if args.since is not None or args.until is not None:
                raise ValueError("Comparison uses its complete frozen inventory")
            result = comparison_report(args.manifest, args.logs)
            text = render_comparison(result)
        else:
            result = make_report(
                args.home,
                since=parse_time(args.since) if args.since is not None else None,
                until=parse_time(args.until) if args.until is not None else None,
            )
            text = render_text(result)
        output = text if args.format == "text" else json.dumps(result, indent=2, allow_nan=False)
        if args.output is not None:
            from colony.report_io import checked_path

            destination = checked_path(args.output)
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            destination = checked_path(destination)
            private_write(destination, (output.rstrip("\n") + "\n").encode())
            return None
        return text if args.format == "text" else result
    if args.command == "init":
        generate_keys(args.dir)
        return {"issuer_public_key": str(args.dir / "issuer.pub")}
    if args.command == "issue":
        if not 1 <= args.days <= 365:
            raise ValueError("Expiry must be between 1 and 365 days")
        namespaces = sorted(set(args.push + args.pull))
        grant = Grant(
            member_id=uuid.uuid4().hex,
            name=args.name,
            server=args.server,
            expires_at=int(time.time()) + args.days * 86400,
            capture=args.capture,
            scopes=[
                Scope(namespace=n, push=n in args.push, pull=n in args.pull) for n in namespaces
            ],
        )
        private_write(args.out, issue(args.issuer, grant).model_dump_json(indent=2).encode())
        return {"keyfile": str(args.out), "member_id": grant.member_id}
    if args.command == "join":
        return Client.join(args.home, args.keyfile, args.issuer)
    if args.command == "serve":
        from colony.server import create_app

        uvicorn.run(
            create_app(args.data, args.repo, args.issuer, max_age=args.max_age),
            host=args.host,
            port=args.port,
            access_log=False,
        )
        return None
    if args.command == "mcp":
        from colony.mcp import serve

        serve(
            args.home,
            evidence_files=args.evidence_files,
            workflow_namespace=args.workflow_namespace,
            workflow_repo=args.workflow_repo,
            workflow_read_only=args.workflow_read_only,
        )
        return None
    if args.command == "setup":
        from colony.setup import setup_codex

        return setup_codex(
            args.home,
            args.repo,
            args.setup_action,
            namespace=getattr(args, "ns", None),
            dry_run=getattr(args, "dry_run", False),
            read_only=getattr(args, "read_only", False),
        )
    client = Client(args.home)
    if args.command == "memory":
        if args.memory_action == "inspect":
            return client.memory_inspect(args.id, args.ns)
        options = {"note": args.note, "operation_id": args.operation_id}
        if args.memory_action == "feedback":
            return client.memory_feedback(args.id, args.ns, verdict=args.verdict, **options)
        try:
            revision = int(args.expected_revision)
        except (TypeError, ValueError):
            raise MemoryInvalid() from None
        if args.memory_action == "correct":
            try:
                data = json.loads(args.file.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or set(data) - {"question", "content", "sources"}:
                    raise ValueError
                sources = data.get("sources", [])
                if not isinstance(sources, list):
                    raise ValueError
                sources = [
                    *sources,
                    *(snapshot(args.repo, ref).model_dump() for ref in args.source),
                ]
                question, content = data["question"], data["content"]
            except (KeyError, TypeError, ValueError):
                raise MemoryInvalid() from None
            return client.memory_correct(
                args.id,
                args.ns,
                expected_revision=revision,
                question=question,
                content=content,
                sources=sources,
                **options,
            )
        return client.memory_change(
            args.id,
            args.ns,
            expected_revision=revision,
            action=args.memory_action,
            **options,
        )
    if args.command == "ask":
        return client.ask(args.question, args.ns, include_evidence_files=args.evidence_files)
    if args.command == "retry":
        return client.retry()
    if args.command == "push":
        data = json.loads(args.file.read_text(encoding="utf-8"))
        data.setdefault("id", uuid.uuid4().hex)
        data["namespace"] = args.ns
        data.setdefault("sources", [])
        data["sources"].extend(snapshot(args.repo, ref).model_dump() for ref in args.source)
        return client.push(Trail.model_validate(data))
    if args.command == "run":
        cmd = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
        if not cmd:
            raise ValueError("Provide a command after --")
        # Enforce caps before launching a potentially expensive command.
        if client.keyfile.grant.capture != "outputs" or not any(
            s.namespace == args.ns and s.push for s in client.keyfile.grant.scopes
        ):
            raise ValueError("Keyfile does not permit this output capture")
        sources = [snapshot(args.repo, ref) for ref in args.source]
        print(
            "Colony capture: stdout/stderr tail will be scrubbed and shared; external "
            "reads cannot be detected, so this trail is marked tainted.",
            file=sys.stderr,
        )
        with tempfile.TemporaryFile() as output:
            process = subprocess.run(
                cmd,
                cwd=args.repo,
                stdout=output,
                stderr=subprocess.STDOUT,
                shell=False,
                check=False,
            )
            content, truncated = scrubbed_tail(output)
        trail = Trail(
            id=uuid.uuid4().hex,
            namespace=args.ns,
            question=args.question,
            content=f"Exit code: {process.returncode}\nTruncated: {truncated}\n{content}",
            sources=sources,
            capture="outputs",
            tainted=True,
        )
        result = client.push(trail)
        print(json.dumps({**result, "exit_code": process.returncode}))
        return process.returncode


def main():
    args = parser().parse_args()
    try:
        result = execute(args)
        if isinstance(result, int):
            raise SystemExit(result)
        if result is not None:
            if args.command == "report" and args.format == "text":
                print(result)
            else:
                print(json.dumps(result, indent=2))
    except (ValueError, OSError, httpx.HTTPError) as exc:
        # Never echo exception text that could contain bearer credentials or request bodies.
        if args.command == "memory" and isinstance(
            exc, (MemoryUnavailable, MemoryConflict, MemoryForbidden, MemoryInvalid, MemoryNotFound)
        ):
            print(f"Colony memory: {exc}", file=sys.stderr)
        elif args.command == "setup" and isinstance(exc, ValueError):
            # Setup normalizes config/parser/identity failures to fixed, safe messages.
            print(f"Colony setup: {exc}", file=sys.stderr)
        else:
            print(
                f"Colony error: {type(exc).__name__}. "
                "Check configuration, scopes, and server availability.",
                file=sys.stderr,
            )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
