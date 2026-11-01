"""Read-only diagnostics. A public health response is not authenticated verification."""

import importlib.metadata
import json
import re
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from colony.models import Keyfile
from colony.provision import _safe, check_models, model_environment
from colony.security import verify


def _health(server: str) -> tuple[str, str]:
    try:
        parsed = urlsplit(server)
        if (
            not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.scheme not in {"http", "https"}
            or (
                parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            )
            or any(ord(char) < 33 for char in server)
        ):
            return (
                "fail",
                "Server URL must use HTTPS or loopback HTTP without credentials or query.",
            )
        _ = parsed.port  # Validate malformed ports before opening a connection.
        with (
            httpx.Client(trust_env=False, follow_redirects=False, timeout=5) as client,
            client.stream("GET", server.rstrip("/") + "/health") as response,
        ):
            if response.status_code != 200:
                return (
                    "fail",
                    "Public health did not return success; authenticated access is untested.",
                )
            data = b""
            for chunk in response.iter_bytes(1024):
                data += chunk
                if len(data) > 4096:
                    return (
                        "fail",
                        "Public health response is invalid; authenticated access is untested.",
                    )

            document = json.loads(data)
            if not isinstance(document, dict) or document.get("status") != "ok":
                return (
                    "fail",
                    "Public health response is invalid; authenticated access is untested.",
                )
        return (
            "pass",
            "Public health responded; identity, namespace access, and retrieval are untested.",
        )
    except Exception:
        return "fail", "Public health check failed; authenticated access is untested."


def diagnose(
    home: Path,
    *,
    role: str = "client",
    repo: Path | None = None,
    models_dir: Path | None = None,
    server: str | None = None,
    namespace: str | None = None,
) -> dict:
    """Inspect local capabilities without creating homes, databases, queues, or caches."""
    checks = []

    def add(name, status, detail):
        checks.append({"name": name, "status": status, "detail": detail})

    if role not in {"client", "server"}:
        add("role", "fail", "Choose client or server diagnostics.")
        return {"ok": False, "checks": checks}
    add(
        "python",
        "pass" if sys.version_info >= (3, 11) else "fail",
        "Python meets the declared minimum."
        if sys.version_info >= (3, 11)
        else "Python 3.11 or newer is required.",
    )
    add(
        "platform",
        "warn",
        "This Python/platform combination needs the disposable demo for end-to-end verification.",
    )
    dependencies = (
        "colony-memory",
        "fastembed",
        "huggingface-hub",
        "onnxruntime",
        "sqlite-vec",
        "cryptography",
        "httpx",
        "fastapi",
        "mcp",
    )
    try:
        for package in dependencies:
            importlib.metadata.version(package)
        add(
            "packages",
            "pass",
            "Required package metadata is installed; model execution is untested.",
        )
    except Exception:
        add(
            "packages",
            "fail",
            "Required package metadata is missing or unreadable; reinstall the package.",
        )
    try:
        import sqlite_vec

        with sqlite3.connect(":memory:") as connection:
            connection.enable_load_extension(True)
            try:
                sqlite_vec.load(connection)
                connection.execute("select vec_version()").fetchone()
            finally:
                connection.enable_load_extension(False)
        add("sqlite_vec", "pass", "SQLite vector extension loaded in an in-memory database.")
    except Exception:
        add(
            "sqlite_vec",
            "fail",
            "SQLite vector extension could not load; persistent stores were not opened.",
        )
    try:
        home = _safe(home)
        identity, issuer = _safe(home / "identity.json"), _safe(home / "issuer.pub")
        if identity.stat().st_size > 65536 or issuer.stat().st_size > 16384:
            raise ValueError("oversized")
        keyfile = Keyfile.model_validate_json(identity.read_bytes())
        grant = verify(keyfile, issuer)
        add(
            "identity",
            "pass",
            "Local identity signature and expiration verified against the local issuer.",
        )
        if namespace is None:
            add(
                "scopes",
                "warn",
                "Namespace permissions were not selected; specify --ns to check them.",
            )
        elif not re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", namespace):
            add("scopes", "fail", "Namespace is invalid.")
        else:
            readable = any(scope.namespace == namespace and scope.pull for scope in grant.scopes)
            writable = any(scope.namespace == namespace and scope.push for scope in grant.scopes)
            if readable and writable:
                add("scopes", "pass", "Local identity grants namespace pull and push permissions.")
            elif readable:
                add(
                    "scopes",
                    "warn",
                    "Local identity grants namespace pull only; publishing is unavailable.",
                )
            else:
                add("scopes", "fail", "Local identity lacks namespace pull permission.")
    except Exception:
        add(
            "identity",
            "warn" if role == "server" else "fail",
            "Local identity is missing, invalid, expired, or unsafe; use explicit client onboarding.",
        )
        add("scopes", "skipped", "Namespace permissions require a valid local identity.")
    if role == "server" or models_dir is not None:
        if models_dir is not None:
            report = check_models(models_dir)
            ready = report["ready"]
        else:
            try:
                model_environment(None, offline=True)
                ready = True
            except Exception:
                ready = False
        add(
            "models",
            "pass" if ready else "fail",
            "Pinned model artifacts verified; ONNX execution is untested."
            if ready
            else "Pinned local models are missing or invalid; run colony models fetch --dir MODEL_ROOT.",
        )
    else:
        add("models", "skipped", "Client-only diagnostics do not require local model artifacts.")
    if repo is not None:
        try:
            from colony.setup import setup_codex

            status = setup_codex(home, repo, "status")
            installed = status["installed"] and not status["partial"] and not status["shadowed"]
            add(
                "codex_setup",
                "pass" if installed else "warn",
                "Owned project setup is installed; Codex trust and loading are untested."
                if installed
                else "Project setup is absent, partial, or shadowed; inspect colony setup codex status.",
            )
        except Exception:
            add(
                "codex_setup",
                "fail",
                "Project setup could not be read safely; no files were changed.",
            )
    else:
        add(
            "codex_setup",
            "skipped",
            "Project setup was not selected; specify --repo to inspect it.",
        )
    if server is not None:
        status, detail = _health(server)
        add("public_health", status, detail)
    else:
        add(
            "public_health",
            "skipped",
            "No network check requested; specify --server for public health only.",
        )
    return {"ok": all(check["status"] != "fail" for check in checks), "checks": checks}
