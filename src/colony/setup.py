"""Reversible, project-local Codex setup. Never copy credentials into project files."""

import hashlib
import json
import math
import os
import re
import stat
import sys
import tempfile
import tomllib
from contextlib import suppress
from pathlib import Path

from colony.client import Client
from colony.workflow import WORKFLOW_GUIDANCE

_TAG = b"colony:setup"
_WINDOWS = os.name == "nt"


class _RecoveryRequired(OSError):
    """A native replacement needs manual recovery; retain its protected backup."""


def _windows_dacl(source: Path, destination: Path) -> None:
    """Protect staging bytes with the original DACL before writing any content."""
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("advapi32", use_last_error=True)
    get = api.GetFileSecurityW
    get.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    get.restype = wintypes.BOOL
    needed = wintypes.DWORD()
    get(str(source), 4, None, 0, ctypes.byref(needed))
    if not needed.value:
        raise OSError("Cannot read Windows file permissions")
    descriptor = ctypes.create_string_buffer(needed.value)
    if not get(str(source), 4, descriptor, len(descriptor), ctypes.byref(needed)):
        raise OSError("Cannot read Windows file permissions")
    control, revision = wintypes.WORD(), wintypes.DWORD()
    inspect = api.GetSecurityDescriptorControl
    inspect.argtypes = [
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    inspect.restype = wintypes.BOOL
    if not inspect(descriptor, ctypes.byref(control), ctypes.byref(revision)):
        raise OSError("Cannot inspect Windows file permissions")
    put = api.SetFileSecurityW
    put.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID]
    put.restype = wintypes.BOOL
    protection = 0x80000000 if control.value & 0x1000 else 0x20000000
    if not put(str(destination), 4 | protection, descriptor):
        raise OSError("Cannot preserve Windows file permissions")


def _win_replace_primitive(path: Path, replacement: Path, backup: Path) -> None:
    import ctypes
    from ctypes import wintypes

    replace = ctypes.WinDLL("kernel32", use_last_error=True).ReplaceFileW
    replace.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
    ]
    replace.restype = wintypes.BOOL
    if not replace(str(path), str(replacement), str(backup), 0, None, None):
        raise OSError("Windows file replacement failed")


def _recovery_path(path: Path) -> Path:
    fd, name = tempfile.mkstemp(prefix=".colony-recovery-", dir=path.parent)
    os.close(fd)
    backup = Path(name)
    backup.unlink()
    return backup


def _windows_replace(path: Path, replacement: Path) -> None:
    backup = _recovery_path(path)
    try:
        _win_replace_primitive(path, replacement, backup)
    except OSError:
        if backup.exists():
            # Link creates the missing destination exclusively and retains the original ACL.
            # Never replace a destination another writer may have created meanwhile.
            try:
                os.link(backup, path)
                backup.unlink()
            except OSError:
                raise _RecoveryRequired("Protected recovery copy retained") from None
        raise
    else:
        with suppress(OSError):
            backup.unlink()


def _safe_path(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    if any(
        part.is_symlink() or getattr(part, "is_junction", lambda: False)()
        for part in [path, *path.parents]
    ):
        raise ValueError("Setup paths must not contain links")
    if os.path.normcase(str(path.resolve())) != os.path.normcase(str(path)):
        raise ValueError("Setup paths must not contain links")
    return path


def _read(path: Path) -> bytes | None:
    _safe_path(path)
    return path.read_bytes() if path.exists() else None


def _parts(data: bytes, markdown: bool) -> tuple[bytes, tuple[int, int, bytes, bool] | None]:
    """Return untouched bytes and a verified owned span, including its separator."""
    if _TAG not in data:
        return data, None
    opening, closing = (b"<!-- ", b" -->") if markdown else (b"# ", b"")
    expression = (
        rb"(?m)^"
        + re.escape(opening)
        + rb"colony:setup:v1 begin sha256=([a-f0-9]{64}) pad=([012]) created=([01])"
        + re.escape(closing)
        + rb"\r?\n(.*?)^"
        + re.escape(opening)
        + rb"colony:setup:end"
        + re.escape(closing)
        + rb"(?:\r?\n|$)"
    )
    matches = list(re.finditer(expression, data, re.DOTALL))
    if len(matches) != 1 or data.count(_TAG) != 2:
        raise ValueError("Malformed or duplicate Colony setup markers")
    match = matches[0]
    newline = b"\r\n" if b"\r\n" in match[0] else b"\n"
    padding = newline * int(match[2])
    start = match.start() - len(padding)
    if start < 0 or data[start : match.start()] != padding:
        raise ValueError("Colony setup separator was edited")
    if hashlib.sha256(b"v1:" + match[3] + padding + match[4]).hexdigest().encode() != match[1]:
        raise ValueError("Colony setup block was edited")
    return data[:start] + data[match.end() :], (start, match.end(), padding, match[3] == b"1")


def _render(data: bytes, body: str, markdown: bool, *, created: bool = False) -> bytes:
    untouched, span = _parts(data, markdown)
    newline = b"\r\n" if b"\r\n" in data else b"\n"
    padding = span[2] if span else newline * (0 if not data else 1 if data.endswith(b"\n") else 2)
    encoded = body.replace("\r\n", "\n").rstrip("\n").encode().replace(b"\n", newline) + newline
    created = span[3] if span else created
    digest = hashlib.sha256(b"v1:" + str(int(created)).encode() + padding + encoded).hexdigest()
    opening, closing = ("<!-- ", " -->") if markdown else ("# ", "")
    block = (
        padding
        + (
            f"{opening}colony:setup:v1 begin sha256={digest} pad={len(padding) // len(newline)}"
            f" created={int(created)}{closing}"
        ).encode()
        + newline
        + encoded
        + f"{opening}colony:setup:end{closing}".encode()
        + newline
    )
    if span:
        return data[: span[0]] + block + data[span[1] :]
    return untouched + block


def _toml(data: bytes) -> dict:
    try:
        return tomllib.loads(data.decode("utf-8"))
    except (UnicodeError, tomllib.TOMLDecodeError):
        raise ValueError("Invalid Codex TOML configuration") from None


def _toml_values(value):
    # TOML accepts NaN; independent parses must compare equal for unchanged NaN values.
    if isinstance(value, dict):
        return {key: _toml_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_toml_values(item) for item in value]
    return ("float", "nan") if isinstance(value, float) and math.isnan(value) else value


def _unmanaged_config(data: bytes, remaining: bytes, managed: bool) -> dict:
    document = _toml(data)
    if not managed:
        return document
    try:
        rest = _toml(remaining)
    except ValueError:
        # The full document was valid; splicing the block caused the parse failure.
        raise ValueError("Colony MCP configuration extends outside the managed block") from None
    servers = document.get("mcp_servers", {})
    if not isinstance(servers, dict) or "colony" not in servers:
        raise ValueError("Colony MCP configuration extends outside the managed block")
    del servers["colony"]
    # An empty parent table may have existed before installation or be implicit.
    for config in (document, rest):
        if config.get("mcp_servers") == {}:
            del config["mcp_servers"]
    if _toml_values(document) != _toml_values(rest):
        raise ValueError("Colony MCP configuration extends outside the managed block")
    return rest


def _atomic(path: Path, content: bytes, mode: int | None, expected: bytes | None) -> None:
    """Recheck staged snapshots; existing-file OS replacement remains best effort against races."""
    fd, temporary = tempfile.mkstemp(prefix=".colony-setup-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            if _WINDOWS and mode is not None:
                _windows_dacl(path, Path(temporary))
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode if mode is not None else 0o600)
        if _read(path) != expected:
            raise ValueError("Setup files changed concurrently")
        if expected is None:
            # Publish exclusively: a file created after the snapshot check must survive.
            os.link(temporary, path)
        elif _WINDOWS:
            _windows_replace(path, Path(temporary))
        else:
            os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _apply(changes: list[tuple[Path, bytes | None, bytes | None]]) -> None:
    """Compare before replace; roll back only files still equal to our own output."""
    completed = []
    directories = []
    try:
        for path, before, after in changes:
            if _read(path) != before:
                raise ValueError("Setup files changed concurrently")
            mode = stat.S_IMODE(path.stat().st_mode) if before is not None else None
            if not path.parent.exists():
                path.parent.mkdir(mode=0o700)
                directories.append(path.parent)
            removed = None
            if after is None:
                # Retain the original inode/ACL until the entire uninstall succeeds.
                removed = _recovery_path(path)
                os.replace(path, removed)
                try:
                    unchanged = _read(removed) == before
                except (OSError, ValueError):
                    unchanged = False
                if not unchanged:
                    try:
                        _safe_path(removed)
                        os.link(removed, path)
                        removed.unlink()
                    except (OSError, ValueError):
                        raise _RecoveryRequired("Protected recovery copy retained") from None
                    raise ValueError("Setup files changed concurrently")
            else:
                _atomic(path, after, mode, before)
            completed.append((path, before, after, mode, removed))
        # Recheck moved deletions after other files have been processed, before cleanup.
        if any(
            removed is not None and _read(removed) != before
            for _, before, _, _, removed in completed
        ):
            raise ValueError("Setup files changed concurrently")
    except Exception as exc:
        rollback_failed = isinstance(exc, _RecoveryRequired)
        for path, before, after, mode, removed in reversed(completed):
            try:
                if _read(path) != after:
                    rollback_failed = True
                    continue
                if removed is not None:
                    _safe_path(removed)
                    os.link(removed, path)
                    removed.unlink()
                elif before is None:
                    path.unlink()
                else:
                    _atomic(path, before, mode, after)
            except (OSError, ValueError):
                rollback_failed = True
        for directory in reversed(directories):
            with suppress(OSError):
                directory.rmdir()
        message = (
            "Setup failed; rollback incomplete. Preserve .colony-recovery-* files in the "
            "project for manual recovery."
            if rollback_failed
            else "Setup failed; rolled back"
        )
        raise ValueError(message) from None
    for _, _, _, _, removed in completed:
        if removed is not None:
            with suppress(OSError):
                removed.unlink()


def setup_codex(
    home: Path,
    repo: Path,
    action: str,
    *,
    namespace: str | None = None,
    dry_run: bool = False,
    read_only: bool = False,
) -> dict:
    """Preview/apply owned project config and guidance; status/removal need no identity."""
    if action not in {"install", "status", "uninstall"}:
        raise ValueError("Unsupported setup action")
    repo = _safe_path(repo)
    if not repo.is_dir():
        raise ValueError("Project directory does not exist")
    config = _safe_path(repo / ".codex" / "config.toml")
    instructions = [_safe_path(repo / name) for name in ("AGENTS.md", "AGENTS.override.md")]
    paths = [config, *instructions]
    originals = {path: _read(path) for path in paths}
    parts = {path: _parts(originals[path] or b"", path != config) for path in paths}
    # Comments do not end TOML tables. Removing a block must preserve every other
    # value's semantic owner, not merely leave a syntactically valid document.
    base = _unmanaged_config(originals[config] or b"", parts[config][0], bool(parts[config][1]))
    owned = [path for path in paths if parts[path][1]]
    instruction_owners = [path for path in instructions if path in owned]
    if len(instruction_owners) > 1:
        raise ValueError("Conflicting Colony instruction blocks")
    notices = [
        "Codex must trust the project and start a fresh session to load setup.",
        "Nested overrides and instruction-size limits can affect guidance; compliance is best effort.",
    ]
    effective = instructions[1] if (originals[instructions[1]] or b"").strip() else instructions[0]
    if action == "status":
        return {
            "action": action,
            "installed": config in owned and bool(instruction_owners),
            "partial": bool(owned) and not (config in owned and instruction_owners),
            "shadowed": bool(instruction_owners) and instruction_owners[0] != effective,
            "files": [str(path.relative_to(repo)) for path in owned],
            "notices": notices,
        }
    outputs = dict(originals)
    if action == "install":
        home = _safe_path(home)
        if not namespace or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", namespace):
            raise ValueError("A valid namespace is required")
        try:
            client = Client(home)
        except (OSError, ValueError, TypeError):
            raise ValueError("A valid joined Colony identity is required") from None
        scopes = client.keyfile.grant.scopes
        if not any(s.namespace == namespace and s.pull for s in scopes) or (
            not read_only and not any(s.namespace == namespace and s.push for s in scopes)
        ):
            raise ValueError("Identity lacks required namespace permissions")
        servers = base.get("mcp_servers", {})
        if not isinstance(servers, dict) or "colony" in servers:
            raise ValueError("Unmanaged Colony MCP configuration conflicts with setup")
        # Preserve a venv executable symlink: resolving it can select the base environment.
        executable = Path(os.path.abspath(sys.executable))
        if not executable.is_file():
            raise ValueError("Python executable does not exist")
        arguments = [
            "-E",
            "-P",
            "-m",
            "colony.cli",
            "--home",
            str(home),
            "mcp",
            "--workflow-namespace",
            namespace,
            "--workflow-repo",
            str(repo),
        ]
        if read_only:
            arguments.append("--workflow-read-only")
        table = (
            "[mcp_servers.colony]\ncommand = "
            + json.dumps(str(executable), ensure_ascii=False)
            + "\nargs = "
            + json.dumps(arguments, ensure_ascii=False)
            + "\ncwd = "
            + json.dumps(str(repo), ensure_ascii=False)
        )
        outputs[config] = _render(
            originals[config] or b"", table, False, created=originals[config] is None
        )
        _toml(outputs[config])
        if instruction_owners and instruction_owners[0] != effective:
            raise ValueError("Instructions are shadowed; remove existing setup before reinstalling")
        mode = "read-only; never publish" if read_only else "publish new evidence-backed findings"
        guidance = f"Colony workflow: namespace `{namespace}`; {mode}.\n\n{WORKFLOW_GUIDANCE}"
        if read_only:
            guidance += "\n\nThis connection is read-only. Do not call push or publish findings."
        outputs[effective] = _render(
            originals[effective] or b"", guidance, True, created=originals[effective] is None
        )
    else:
        for path in owned:
            remaining = parts[path][0]
            outputs[path] = remaining if remaining or not parts[path][1][3] else None
        _toml(outputs[config] or b"")
    changes = [
        (path, originals[path], outputs[path]) for path in paths if originals[path] != outputs[path]
    ]
    if changes and not dry_run:
        _apply(changes)
    return {
        "action": action,
        "dry_run": dry_run,
        "changed": bool(changes),
        "files": [str(path.relative_to(repo)) for path, _, _ in changes],
        "notices": notices,
    }
