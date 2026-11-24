import ast
import hashlib
import os
import stat
from contextlib import ExitStack, contextmanager
from pathlib import Path, PurePosixPath

from colony.models import Source

MAX_SOURCE_BYTES = 2_000_000

if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _FileBasicInfo(ctypes.Structure):
        _fields_ = [("CreationTime", ctypes.c_int64), ("LastAccessTime", ctypes.c_int64),
                    ("LastWriteTime", ctypes.c_int64), ("ChangeTime", ctypes.c_int64),
                    ("FileAttributes", wintypes.DWORD)]

    _get_file_info = ctypes.WinDLL("kernel32", use_last_error=True).GetFileInformationByHandleEx
    _get_file_info.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.POINTER(_FileBasicInfo), wintypes.DWORD)
    _get_file_info.restype = wintypes.BOOL


class SourcePathDenied(ValueError):
    """A fixed public error for a host-restricted source."""


class PinnedSourcePaths:
    """Bind each requested name to one authorized target for a single operation."""

    def __init__(self, allowed):
        self.allowed = allowed
        self.targets = {}
        self.observed_only = False

    def __call__(self, ref):
        return self.allowed(ref)

    def check_target(self, ref, path):
        target = str(path)
        if not self.observed_only:
            self.targets.setdefault(ref, target)
        if self.targets.get(ref) != target:
            raise SourcePathDenied("Observed source target changed")


def _require_allowed(predicate, ref):
    try:
        allowed = predicate(ref) is True
    except Exception:
        allowed = False
    if not allowed:
        raise SourcePathDenied("Source path unavailable")


def repo_file(root: Path, ref: str, *, path_allowed=None, expected_target=None) -> Path:
    # Resolve symlinks too; deny Windows paths even when the host is Linux.
    if "\\" in ref or ":" in ref or PurePosixPath(ref).is_absolute():
        raise ValueError("Source must be a repository-relative POSIX path")
    if path_allowed is not None:
        _require_allowed(path_allowed, ref)
    try:
        path = (root / ref).resolve()
        if expected_target is not None and str(path) != expected_target:
            raise SourcePathDenied("Observed source target changed")
        if path_allowed is None:
            path.relative_to(root.resolve())
        else:
            boundary = root.absolute()
            relative = path.relative_to(boundary).as_posix()
            if path.parts[:len(boundary.parts)] != boundary.parts:
                raise SourcePathDenied("Source path unavailable")
            if relative != ref:
                _require_allowed(path_allowed, relative)
    except RuntimeError as exc:  # Symlink loop on Python 3.11/3.12.
        raise ValueError("Source path unavailable") from exc
    if not path.is_file() or path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("Source is missing or exceeds 2 MB")
    if isinstance(path_allowed, PinnedSourcePaths):
        path_allowed.check_target(ref, path)
    return path


def _windows_handle_path(stream):
    """Get the normalized path of the already-open handle, before reading bytes."""
    final_path = ctypes.windll.kernel32.GetFinalPathNameByHandleW
    final_path.argtypes = (wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD)
    final_path.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    size = final_path(msvcrt.get_osfhandle(stream.fileno()), buffer, len(buffer), 0)
    if not 0 < size < len(buffer):
        raise SourcePathDenied("Source path unavailable")
    return buffer.value


def _check_windows_handle_path(path, stream):
    # Path equality folds case even in case-sensitive directories.
    expected = str(path)
    if not expected.startswith("\\\\?\\"):
        expected = ("\\\\?\\UNC\\" + expected[2:] if expected.startswith("\\\\")
                    else "\\\\?\\" + expected)
    if _windows_handle_path(stream) != expected:
        raise SourcePathDenied("Source path unavailable")


def file_version(path: Path):
    """Identify the authorized file and revision using metadata only."""
    if os.name == "nt":
        # Windows stat().st_ctime can be the creation time. Query ChangeTime
        # on the same handle as the file identity, including after utime().
        with path.open("rb") as stream:
            _check_windows_handle_path(path, stream)
            info = os.fstat(stream.fileno())
            basic = _FileBasicInfo()
            handle = msvcrt.get_osfhandle(stream.fileno())
            if not _get_file_info(handle, 0, ctypes.byref(basic), ctypes.sizeof(basic)):  # FileBasicInfo
                raise ctypes.WinError(ctypes.get_last_error())
            changed = basic.ChangeTime
            if changed <= 0:
                raise ValueError("File change time unavailable")
    else:
        info = path.stat()
        changed = info.st_ctime_ns
    return (str(path), info.st_dev, info.st_ino, changed, info.st_mtime_ns, info.st_size)


@contextmanager
def open_source_file(path: Path):
    """Open an authorized, resolved repo_file path and verify the readable object."""
    with ExitStack() as stack:
        if os.name == "nt":
            stream = stack.enter_context(path.open("rb"))
            _check_windows_handle_path(path, stream)
        else:
            # Walk from the filesystem anchor with pinned directory descriptors.
            # A renamed/relinked parent cannot redirect the next component.
            traverse = getattr(os, "O_PATH", getattr(os, "O_SEARCH", os.O_RDONLY))
            flags = traverse | os.O_DIRECTORY | os.O_NOFOLLOW
            directory = os.open(path.anchor, flags)
            try:
                for part in path.parts[1:-1]:
                    child = os.open(part, flags, dir_fd=directory)
                    os.close(directory)
                    directory = child
                fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            finally:
                os.close(directory)
            stream = stack.enter_context(os.fdopen(fd, "rb"))
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SOURCE_BYTES:
            raise ValueError("Source is missing or exceeds 2 MB")
        yield stream


def repo_contents(root: Path, ref: str, *, path_allowed=None, expected_target=None) -> tuple[Path, bytes]:
    """Authorize the requested and resolved names, then read the authorized object once."""
    path = repo_file(root, ref, path_allowed=path_allowed, expected_target=expected_target)
    if path_allowed is None:
        return path, path.read_bytes()
    with open_source_file(path) as stream:
        data = stream.read(MAX_SOURCE_BYTES + 1)
        if len(data) > MAX_SOURCE_BYTES:
            raise ValueError("Source exceeds 2 MB")
        return path, data


def snapshot(root: Path, ref: str) -> Source:
    return Source(
        ref=ref, content_hash=hashlib.sha256(repo_file(root, ref).read_bytes()).hexdigest()
    )


def validate(root: Path, sources: list[Source], *, path_allowed=None) -> tuple[str, list[dict]]:
    if not sources:
        return "unverified", []
    checks = []
    for source in sources:
        result = "unverified"
        reason = "Only repository file evidence is supported in the MVP"
        if source.kind == "file":
            try:
                path, data = repo_contents(root, source.ref, path_allowed=path_allowed)
                result = (
                    "validated"
                    if hashlib.sha256(data).hexdigest() == source.content_hash
                    else "stale"
                )
                reason = "File fingerprint matches" if result == "validated" else "File changed"
                if (
                    result == "validated"
                    and source.contains is not None
                    and source.contains not in data.decode("utf-8")
                ):
                    result, reason = "stale", "Expected configuration snippet is absent"
                if result == "validated" and source.symbol is not None:
                    if path.suffix != ".py":
                        result, reason = "unverified", "Symbol checks currently support Python only"
                    else:
                        tree = ast.parse(data)
                        names = {
                            n.name
                            for n in ast.walk(tree)
                            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                        }
                        if source.symbol not in names:
                            result, reason = "stale", "Expected Python symbol is absent"
            except (OSError, ValueError, SyntaxError) as exc:
                result, reason = "stale", f"Evidence unavailable ({type(exc).__name__})"
        checks.append({"ref": source.ref, "result": result, "reason": reason})
    status = (
        "stale"
        if any(c["result"] == "stale" for c in checks)
        else ("validated" if all(c["result"] == "validated" for c in checks) else "unverified")
    )
    return status, checks
