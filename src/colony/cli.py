import argparse
import json
import os
import shlex
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
from colony.models import GitContext, Grant, Scope, Trail
from colony.provision import ModelPreparationError
from colony.security import generate_keys, issue, private_write
from colony.validation import snapshot


class OnboardingInputError(ValueError):
    """Fixed messages from command argument/prompt validation only."""


def _onboarding_commands(result):
    """Copyable commands contain path references, never credential contents."""

    def command(*args):
        words = [sys.executable, "-E", "-P", "-m", "colony.cli", *map(str, args)]
        if os.name == "nt":
            return "& " + " ".join("'" + word.replace("'", "''") + "'" for word in words)
        return shlex.join(words)

    home = result.get("homes", {}).get("a", result.get("home"))
    commands = {}
    if "serve_args" in result:
        models = Path(result["workspace"]) / "models"
        commands["prepare_models"] = command("models", "fetch", "--dir", models)
        commands["serve_foreground"] = command(
            *result["serve_args"], "--models", models, "--offline"
        )
    if home:
        options = ["--home", home, "doctor", "--server", result["server"]]
        if result.get("namespace"):
            options.extend(["--ns", result["namespace"]])
        if result.get("repo"):
            options.extend(["--repo", result["repo"]])
        commands["check_client"] = command(*options)
    result["commands"] = commands
    return result


def _prompt_path(label):
    try:
        value = input(label + ": ").strip()
    except (EOFError, KeyboardInterrupt):
        raise OnboardingInputError("Onboarding input cancelled") from None
    if not value:
        raise OnboardingInputError("An absolute path is required")
    return Path(value)


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
    serve.add_argument(
        "--repository-id", help="Explicit logical repository ID for Git-aware clients"
    )
    serve.add_argument("--models", type=Path, help="Verified directory prepared by models fetch")
    serve.add_argument(
        "--offline", action="store_true", help="Require pinned local models; never download"
    )
    ask = sub.add_parser("ask")
    ask.add_argument("question")
    ask.add_argument("--ns")
    ask.add_argument("--repo", type=Path, default=Path.cwd())
    ask.add_argument("--repository-id", help="Enable Git-aware lookup for this repository ID")
    ask.add_argument(
        "--evidence-files",
        action="store_true",
        help="Include bounded complete current source files when small",
    )
    push = sub.add_parser("push", help="Push a JSON trail, with optional local file fingerprints")
    push.add_argument("file", type=Path)
    push.add_argument("--ns", required=True)
    push.add_argument("--repo", type=Path, default=Path.cwd())
    push.add_argument("--repository-id", help="Record Git provenance for this repository ID")
    push.add_argument("--source", action="append", default=[])
    run = sub.add_parser(
        "run", help="Capture only command output; never trace inputs or tool calls"
    )
    run.add_argument("--capture", choices=["outputs"], default="outputs")
    run.add_argument("--ns", required=True)
    run.add_argument("--question", required=True)
    run.add_argument("--repo", type=Path, default=Path.cwd())
    run.add_argument("--repository-id", help="Record Git provenance for this repository ID")
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
        "--workflow-repository-id", help="Enable Git-aware workflow for a logical repository ID"
    )
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
    install.add_argument("--repository-id", help="Enable Git-aware project workflow")
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
            operation.add_argument("--repository-id", help="Record correction Git provenance")
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
    models = sub.add_parser(
        "models", help="Explicit preparation and read-only checks of pinned models"
    )
    model_actions = models.add_subparsers(dest="model_action", required=True)
    for action in ("check", "fetch"):
        command = model_actions.add_parser(action)
        command.add_argument("--dir", type=Path, required=True)
    doctor = sub.add_parser(
        "doctor", help="Read-only local checks and optional public server health"
    )
    doctor.add_argument("--role", choices=("client", "server"), default="client")
    doctor.add_argument("--repo", type=Path)
    doctor.add_argument("--models", type=Path)
    doctor.add_argument("--server", help="Explicit public health check; sends no credential")
    doctor.add_argument("--ns")
    doctor.add_argument("--format", choices=("text", "json"), default="text")
    onboarding = sub.add_parser("onboard", help="Prepare a local team or join an existing server")
    paths = onboarding.add_subparsers(dest="onboard_action", required=True)
    initialize = paths.add_parser("init", help="Prepare private local team credentials; no daemon")
    initialize.add_argument("--workspace", type=Path)
    initialize.add_argument("--repo", type=Path)
    initialize.add_argument("--ns", default="team")
    initialize.add_argument("--server", default="http://127.0.0.1:7331")
    enrollment = paths.add_parser("join", help="Enroll with a separately trusted issuer public key")
    enrollment.add_argument("--keyfile", type=Path)
    enrollment.add_argument("--issuer", type=Path)
    enrollment.add_argument(
        "--agent-home", type=Path, help="Explicit private home (otherwise --home)"
    )
    enrollment.add_argument("--repo", type=Path)
    enrollment.add_argument("--ns")
    enrollment.add_argument("--read-only", action="store_true")
    for command in (initialize, enrollment):
        command.add_argument("--dry-run", action="store_true")
        command.add_argument("--configure-codex", action="store_true")
        command.add_argument(
            "--interactive", action="store_true", help="Prompt for paths on a terminal"
        )
    demo = sub.add_parser(
        "demo", help="Isolated two-identity CLI/HTTP/MCP demonstration; no model service"
    )
    demo.add_argument(
        "--workspace", type=Path, required=True, help="New disposable directory; never reuse"
    )
    demo.add_argument("--models", type=Path)
    demo.add_argument("--configure-codex", action="store_true")
    demo.add_argument("--timeout", type=float, default=90)
    demo.add_argument(
        "--cleanup", action="store_true", help="Remove only owned successful demo files"
    )
    return p


def execute(args):
    if args.command == "models":
        from colony.provision import check_models, fetch_models

        return (check_models if args.model_action == "check" else fetch_models)(args.dir)
    if args.command == "doctor":
        from colony.doctor import diagnose

        return diagnose(
            args.home,
            role=args.role,
            repo=args.repo,
            models_dir=args.models,
            server=args.server,
            namespace=args.ns,
        )
    if args.command == "onboard":
        from colony.onboarding import enroll, initialize

        if args.interactive:
            if not sys.stdin.isatty():
                raise OnboardingInputError("Interactive onboarding requires a terminal")
            if args.onboard_action == "init":
                for field, label in (
                    ("workspace", "New private workspace (absolute path)"),
                    ("repo", "Existing project (absolute path)"),
                ):
                    if getattr(args, field) is None:
                        setattr(args, field, _prompt_path(label))
            else:
                for field, label in (
                    ("keyfile", "Signed keyfile (absolute path)"),
                    ("issuer", "Separately trusted issuer public key (absolute path)"),
                ):
                    if getattr(args, field) is None:
                        setattr(args, field, _prompt_path(label))
                if args.agent_home is None:
                    args.agent_home = _prompt_path("New private agent home (absolute path)")
        if args.onboard_action == "init":
            if args.workspace is None or args.repo is None:
                raise OnboardingInputError("Provide --workspace and --repo, or use --interactive")
            return _onboarding_commands(
                initialize(
                    args.workspace,
                    args.repo,
                    namespace=args.ns,
                    server=args.server,
                    dry_run=args.dry_run,
                    configure_codex=args.configure_codex,
                )
            )
        if args.keyfile is None or args.issuer is None:
            raise OnboardingInputError("Provide --keyfile and --issuer, or use --interactive")
        if args.agent_home is None:
            raise OnboardingInputError(
                "Provide --agent-home as an explicit absolute private directory"
            )
        return _onboarding_commands(
            enroll(
                args.agent_home,
                args.keyfile,
                args.issuer,
                repo=args.repo,
                namespace=args.ns,
                read_only=args.read_only,
                dry_run=args.dry_run,
                configure_codex=args.configure_codex,
            )
        )
    if args.command == "demo":
        from colony.demo import run_demo

        return run_demo(
            args.workspace,
            models_dir=args.models,
            configure_codex=args.configure_codex,
            timeout=args.timeout,
            cleanup=args.cleanup,
        )
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

        if args.models is not None or args.offline:
            from colony import retrieval
            from colony.provision import model_environment

            os.environ.update(model_environment(args.models, offline=args.offline))
            retrieval.models.cache_clear()
            retrieval.index_version.cache_clear()

        uvicorn.run(
            create_app(
                args.data,
                args.repo,
                args.issuer,
                max_age=args.max_age,
                repository_id=args.repository_id,
            ),
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
            workflow_repository_id=args.workflow_repository_id,
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
            repository_id=getattr(args, "repository_id", None),
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
            git_context = None
            if args.repository_id is not None:
                from colony.git_context import capture

                git_context = capture(args.repo, args.repository_id)
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
            if git_context is not None:
                from colony.git_client import checked_sources
                from colony.models import Source

                checked_sources(args.repo, [Source.model_validate(source) for source in sources])
                if capture(args.repo, args.repository_id) != git_context:
                    raise MemoryInvalid()
                options["git_context"] = git_context
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
        options = (
            {"repo": args.repo, "repository_id": args.repository_id}
            if args.repository_id is not None
            else {}
        )
        return client.ask(
            args.question, args.ns, include_evidence_files=args.evidence_files, **options
        )
    if args.command == "retry":
        return client.retry()
    if args.command == "push":
        context = None
        if args.repository_id is not None:
            from colony.git_context import capture

            context = capture(args.repo, args.repository_id)
        data = json.loads(args.file.read_text(encoding="utf-8"))
        data.setdefault("id", uuid.uuid4().hex)
        data["namespace"] = args.ns
        data.setdefault("sources", [])
        data["sources"].extend(snapshot(args.repo, ref).model_dump() for ref in args.source)
        if context is not None:
            try:
                if (
                    data.get("git_context") is not None
                    and GitContext.model_validate(data["git_context"]) != context
                ):
                    raise ValueError()
            except (TypeError, ValueError):
                raise ValueError(
                    "Git context differs from the current publication context"
                ) from None
            data["git_context"] = context.model_dump()
        options = (
            {"repo": args.repo, "repository_id": args.repository_id}
            if args.repository_id is not None
            else {}
        )
        return client.push(Trail.model_validate(data), **options)
    if args.command == "run":
        context = None
        if args.repository_id is not None:
            from colony.git_context import capture

            context = capture(args.repo, args.repository_id)
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
            git_context=context,
        )
        options = (
            {"repo": args.repo, "repository_id": args.repository_id}
            if args.repository_id is not None
            else {}
        )
        result = client.push(trail, **options)
        print(json.dumps({**result, "exit_code": process.returncode}))
        return process.returncode


def main():
    args = parser().parse_args()
    try:
        result = execute(args)
        if isinstance(result, int):
            raise SystemExit(result)
        if result is not None:
            if args.command == "doctor" and args.format == "text":
                print("Colony doctor: " + ("checks passed" if result["ok"] else "needs attention"))
                for check in result["checks"]:
                    print(f"[{check['status']}] {check['name']}: {check['detail']}")
            elif args.command == "report" and args.format == "text":
                print(result)
            else:
                print(json.dumps(result, indent=2))
            if args.command in {"doctor", "onboard", "demo"} and not result["ok"]:
                raise SystemExit(1)
            if args.command == "models" and not result["ready"]:
                raise SystemExit(1)
    except (ValueError, OSError, httpx.HTTPError) as exc:
        # Never echo exception text that could contain bearer credentials or request bodies.
        if isinstance(exc, (OnboardingInputError, ModelPreparationError)):
            # These types are generated only from fixed messages in the new paths.
            print(f"Colony {args.command}: {exc}", file=sys.stderr)
        elif args.command == "memory" and isinstance(
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
