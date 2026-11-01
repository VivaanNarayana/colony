"""Explicit, isolated onboarding using Colony's existing issuer and client mechanisms.

Receipts contain paths and fixed status only. Bootstrap prepares identities locally;
the first authenticated server request registers them through the existing server.
"""

import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from colony.client import Client
from colony.models import Grant, Keyfile, Scope
from colony.onboard_io import (
    check_tree,
    ensure_file,
    json_bytes,
    private_directory,
    publish_directory,
    read_json,
    read_limited,
    safe_path,
    write_new,
)
from colony.security import generate_keys, issue, verify
from colony.setup import setup_codex

_BOOTSTRAP = ".colony-bootstrap.json"
_BOOTSTRAP_PLAN = ".colony-bootstrap-plan.json"
_JOIN = ".colony-enrollment.json"


class _IssuerSnapshot:
    """Supply already bounded issuer bytes to the existing signature verifier."""

    def __init__(self, content: bytes):
        self.content = content

    def read_bytes(self) -> bytes:
        return self.content


def _namespace(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", value):
        raise ValueError("A valid namespace is required")
    return value


def _server(value: str, *, bootstrap: bool = False):
    try:
        parsed = urlsplit(value)
        port = parsed.port
        invalid = (
            not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or (bootstrap and parsed.path not in ("", "/"))
            or "?" in value
            or "#" in value
            or "\\" in value
            or any(ord(char) < 33 or ord(char) == 127 for char in value)
            or (port is not None and not 1 <= port <= 65535)
        )
        loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        invalid |= (
            (parsed.scheme != "http" or not loopback)
            if bootstrap
            else (parsed.scheme != "https" and not (parsed.scheme == "http" and loopback))
        )
        if invalid:
            raise ValueError()
    except (TypeError, ValueError):
        message = (
            "Bootstrap server must be loopback HTTP"
            if bootstrap
            else ("Server must use HTTPS, or loopback HTTP, without credentials or URL suffixes")
        )
        raise ValueError(message) from None
    return parsed


def _destination(value: Path) -> Path:
    path = safe_path(value)
    if not path.name or not path.parent.is_dir():
        raise ValueError("Onboarding destination needs an existing parent directory")
    return path


def _repo(value: Path) -> Path:
    path = safe_path(value)
    if not path.is_dir():
        raise ValueError("Project directory must exist")
    return path


def _separate(home: Path, repo: Path) -> None:
    if home == repo or home.is_relative_to(repo) or repo.is_relative_to(home):
        raise ValueError("Onboarding credentials must be outside the project directory")


def _stage(path: Path, kind: str) -> Path:
    return safe_path(path.parent / ("." + path.name + ".colony-" + kind))


def _configuration(home, repo, namespace, requested, dry_run, read_only=False):
    if not requested:
        return {"status": "not_requested"}
    if dry_run and not home.exists():
        override = safe_path(repo / "AGENTS.override.md")
        instructions = (
            override
            if override.exists() and override.read_bytes().strip()
            else (repo / "AGENTS.md")
        )
        config = safe_path(repo / ".codex" / "config.toml")
        safe_path(instructions)
        return {"status": "preview", "files": [str(config), str(instructions)], "action": "install"}
    try:
        result = setup_codex(
            home, repo, "install", namespace=namespace, dry_run=dry_run, read_only=read_only
        )
    except (OSError, ValueError, TypeError) as exc:
        if "rollback incomplete" in str(exc):
            return {
                "status": "failed",
                "message": "Credentials preserved. Project setup rollback "
                "is incomplete; preserve .colony-recovery-* files for manual recovery.",
            }
        return {
            "status": "failed",
            "message": "Credentials preserved. Resolve project setup "
            "conflicts, then rerun with configure_codex; setup reports rollback failures separately.",
        }
    return {"status": "preview" if dry_run else "configured", **result}


def _issuer(directory: Path, *, readonly: bool):
    private, public = directory / "issuer.pem", directory / "issuer.pub"
    if not directory.exists():
        if readonly:
            raise ValueError("Owned onboarding issuer is incomplete")
        private_directory(directory)
    if not private.exists():
        if readonly or any(directory.iterdir()):
            raise ValueError("Issuer creation incomplete; preserve staging and use a new workspace")
        generate_keys(directory)
    key = serialization.load_pem_private_key(private.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("Invalid onboarding issuer")
    expected = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    if readonly:
        if not public.exists() or public.read_bytes() != expected:
            raise ValueError("Owned onboarding issuer is invalid")
    else:
        ensure_file(public, expected)


def _prepared_home(home: Path, administrator: Path, grant: Grant, *, readonly: bool):
    if not home.exists():
        if readonly:
            raise ValueError("Owned onboarding credentials are incomplete")
        private_directory(home)
    identity = issue(administrator / "issuer.pem", grant)
    expected = {
        "identity.json": identity.model_dump_json().encode(),
        "issuer.pub": (administrator / "issuer.pub").read_bytes(),
    }
    for name, content in expected.items():
        if readonly and not (home / name).exists():
            raise ValueError("Owned onboarding credentials are incomplete")
        ensure_file(home / name, content)
    queue = home / "queue.key"
    if not queue.exists():
        if readonly:
            raise ValueError("Owned onboarding credentials are incomplete")
        write_new(queue, Fernet.generate_key())
    Fernet(queue.read_bytes())
    verify(identity, home / "issuer.pub")


def _bootstrap_receipt(workspace, repo, namespace, server, expires_at, *, status="prepared"):
    return {
        "format": "colony-bootstrap-v1",
        "workspace": str(workspace),
        "repo": str(repo),
        "namespace": namespace,
        "server": server,
        "expires_at": expires_at,
        "status": status,
    }


def initialize(
    workspace: Path,
    repo: Path,
    *,
    namespace="team",
    server="http://127.0.0.1:7331",
    dry_run=False,
    configure_codex=False,
) -> dict:
    """Prepare one private issuer and isolated A (publish) / B (lookup) homes.

    The workspace must be new, or carry an exact matching Colony receipt. Completed
    staging resumes automatically. Incomplete issuer creation is retained for recovery.
    No server, model, process, or global configuration is started or changed.
    """
    workspace, repo = _destination(workspace), _repo(repo)
    _separate(workspace, repo)
    namespace = _namespace(namespace)
    parsed = _server(server, bootstrap=True)
    server = server.rstrip("/")
    stage = _stage(workspace, "bootstrap")
    reused = workspace.exists()
    location = workspace if reused else stage
    existing = location.exists()
    if reused and stage.exists():
        raise ValueError("Conflicting onboarding staging exists; preserve both directories")
    if existing:
        check_tree(location)
        receipt = read_json(location / (_BOOTSTRAP if reused else _BOOTSTRAP_PLAN))
        expiry = receipt.get("expires_at")
        if type(expiry) is not int or receipt != _bootstrap_receipt(
            workspace,
            repo,
            namespace,
            server,
            expiry,
            status="prepared" if reused else "initializing",
        ):
            raise ValueError("Workspace is unowned or conflicts with requested onboarding")
    else:
        receipt = _bootstrap_receipt(
            workspace, repo, namespace, server, int(time.time()) + 30 * 86400
        )
    administrator = workspace / "administrator"
    homes = {"a": workspace / "agent-a", "b": workspace / "agent-b"}
    result = {
        "ok": True,
        "dry_run": dry_run,
        "reused": reused,
        "status": "planned" if dry_run and not reused else "prepared",
        "workspace": str(workspace),
        "repo": str(repo),
        "issuer": str(administrator),
        "data": str(workspace / "data"),
        "homes": {k: str(v) for k, v in homes.items()},
        "server": server,
        "namespace": namespace,
        "serve_args": [
            "serve",
            "--repo",
            str(repo),
            "--data",
            str(workspace / "data"),
            "--issuer",
            str(administrator / "issuer.pub"),
            "--host",
            parsed.hostname,
            "--port",
            str(parsed.port or 80),
        ],
        "next_steps": [
            "Start serve_args in a foreground terminal.",
            "Applying bootstrap prepares local identities; server authentication is a separate step."
            if dry_run and not reused
            else "Identities are prepared locally; server authentication has not been checked.",
            "Use agent-a to publish and agent-b to look up evidence.",
        ],
    }
    if not dry_run or reused:
        try:
            if not existing:
                private_directory(stage)
                write_new(
                    stage / _BOOTSTRAP_PLAN, json_bytes({**receipt, "status": "initializing"})
                )
            _issuer(location / "administrator", readonly=reused)
            for label in ("a", "b"):
                grant = Grant(
                    member_id="agent-" + label,
                    name="Agent " + label.upper(),
                    server=server,
                    expires_at=receipt["expires_at"],
                    capture="none",
                    scopes=[Scope(namespace=namespace, pull=True, push=label == "a")],
                )
                _prepared_home(
                    location / ("agent-" + label),
                    location / "administrator",
                    grant,
                    readonly=reused,
                )
            if not reused:
                check_tree(stage)
                prepared = {**receipt, "status": "prepared"}
                ensure_file(stage / _BOOTSTRAP, json_bytes(prepared))
                publish_directory(stage, workspace)
        except (OSError, ValueError, TypeError):
            raise ValueError(
                "Bootstrap incomplete. Preserve the owned workspace/staging directory; "
                "rerun the same request to resume. If it conflicts or issuer creation "
                "is incomplete, use a new explicit workspace for recovery."
            ) from None
    result["codex"] = _configuration(homes["a"], repo, namespace, configure_codex, dry_run)
    result["ok"] = result["codex"]["status"] != "failed"
    return result


def enroll(
    home: Path,
    keyfile: Path,
    issuer: Path,
    *,
    repo=None,
    namespace=None,
    read_only=False,
    dry_run=False,
    configure_codex=False,
) -> dict:
    """Verify a separately trusted issuer, enroll through Client.join, then publish home.

    Matching complete identities can be reused using local verification. A retained
    partial enrollment never claims rollback of remote server membership.
    """
    home, keyfile, issuer = _destination(home), safe_path(keyfile), safe_path(issuer)
    if repo is not None:
        repo = _repo(repo)
        _separate(home, repo)
    if configure_codex and (repo is None or namespace is None):
        raise ValueError("Codex setup requires an explicit project and namespace")
    if namespace is not None:
        namespace = _namespace(namespace)
    try:
        key = Keyfile.model_validate_json(read_limited(keyfile, 65536))
        trusted = read_limited(issuer, 16384)
        grant = verify(key, _IssuerSnapshot(trusted))
        _server(grant.server)
    except (OSError, ValueError, TypeError):
        raise ValueError(
            "Enrollment requires a valid keyfile and separately trusted issuer"
        ) from None
    if namespace is not None and (
        not any(s.namespace == namespace and s.pull for s in grant.scopes)
        or (not read_only and not any(s.namespace == namespace and s.push for s in grant.scopes))
    ):
        raise ValueError("Identity lacks requested namespace permissions; use read_only for lookup")
    stage = _stage(home, "enrollment")
    reused = home.exists()
    receipt = {
        "format": "colony-enrollment-v1",
        "home": str(home),
        "keyfile": str(keyfile),
        "issuer": str(issuer),
        "status": "enrollment_requested",
    }
    location = home if reused else stage
    complete = False
    if reused and stage.exists():
        raise ValueError("Conflicting enrollment staging exists; preserve both directories")
    if location.exists():
        check_tree(location)
        if not reused and read_json(location / _JOIN) != receipt:
            raise ValueError("Enrollment staging conflicts; preserve the directory")
        present = [
            name
            for name in ("identity.json", "issuer.pub", "queue.key")
            if (location / name).exists()
        ]
        if not reused and not present:
            pass  # An enrollment request without credential writes can be retried safely.
        else:
            try:
                if (
                    Keyfile.model_validate_json(read_limited(location / "identity.json", 65536))
                    != key
                    or read_limited(location / "issuer.pub", 16384) != trusted
                ):
                    raise ValueError()
                verify(key, _IssuerSnapshot(trusted))
                Fernet((location / "queue.key").read_bytes())
                complete = True
            except (OSError, ValueError, TypeError):
                raise ValueError(
                    "Existing enrollment is conflicting or incomplete. Preserve it and use "
                    "a new explicit home; remote membership may already exist."
                ) from None
    result = {
        "ok": True,
        "dry_run": dry_run,
        "reused": reused,
        "status": "locally_verified" if reused else "planned" if dry_run else "enrolled",
        "home": str(home),
        "issuer": str(issuer),
        "repo": str(repo) if repo else None,
        "server": grant.server,
        "namespace": namespace,
        "next_steps": [
            "Use this explicit home for Colony commands.",
            "Reused identities are verified locally; server enrollment is not rechecked."
            if reused
            else "The server must be reachable when applying enrollment.",
        ],
    }
    if not dry_run and not reused:
        try:
            if not stage.exists():
                private_directory(stage)
                write_new(stage / _JOIN, json_bytes(receipt))
            if not complete:
                # Client.join reads paths. Keep its inputs fixed across the remote request
                # rather than rereading caller-owned files after validating them above.
                incoming_key = stage / ".colony-incoming-key.json"
                incoming_issuer = stage / ".colony-incoming-issuer.pub"
                ensure_file(incoming_key, key.model_dump_json().encode())
                ensure_file(incoming_issuer, trusted)
                Client.join(stage, incoming_key, incoming_issuer)
            for name in (".colony-incoming-key.json", ".colony-incoming-issuer.pub"):
                (stage / name).unlink(missing_ok=True)
            check_tree(stage)
            publish_directory(stage, home)
        except Exception:
            raise ValueError(
                "Enrollment incomplete. Preserve the staging directory; remote membership "
                "may already exist. Rerun the same request to publish complete credentials, "
                "or use a new explicit home if local credentials are incomplete."
            ) from None
    result["codex"] = _configuration(home, repo, namespace, configure_codex, dry_run, read_only)
    result["ok"] = result["codex"]["status"] != "failed"
    return result
