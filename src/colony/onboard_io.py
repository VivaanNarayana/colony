"""Narrow filesystem helpers for explicitly owned onboarding directories."""

import json
import os
import stat
import sys
import uuid
from pathlib import Path

from colony.security import private_write


def safe_path(value: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("Onboarding paths must be absolute")
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if os.name == "nt" and any(":" in item for item in part.parts[1:]):
            raise ValueError("Onboarding paths must not use alternate streams")
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Onboarding paths must not contain links or reparse points")
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise ValueError("Onboarding files must not have hard links")
    return path


def check_tree(root: Path) -> None:
    safe_path(root)
    if not root.is_dir():
        raise ValueError("Onboarding directory is invalid")
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            safe_path(Path(directory) / name)


def private_directory(path: Path) -> None:
    """Protect new Windows directories before writing any credential bytes."""
    path.mkdir(mode=0o700)
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("advapi32", use_last_error=True)
    convert = api.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.DWORD),
    ]
    convert.restype = wintypes.BOOL
    put = api.SetFileSecurityW
    put.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID]
    put.restype = wintypes.BOOL
    descriptor = wintypes.LPVOID()
    if not convert("D:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)", 1, ctypes.byref(descriptor), None):
        raise ValueError("Cannot protect onboarding directory")
    try:
        if not put(str(path), 4 | 0x80000000, descriptor):
            raise ValueError("Cannot protect onboarding directory")
    finally:
        free = ctypes.WinDLL("kernel32", use_last_error=True).LocalFree
        free.argtypes = [wintypes.LPVOID]
        free.restype = wintypes.LPVOID
        free(descriptor)


def write_new(path: Path, content: bytes) -> None:
    """Exclusive, complete publication; incomplete staging is never a public home."""
    safe_path(path)
    temporary = path.parent / (".colony-write-" + uuid.uuid4().hex)
    try:
        private_write(temporary, content)
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def publish_directory(stage: Path, target: Path) -> None:
    """Rename exclusively, including on POSIX where ordinary rename replaces empty dirs."""
    safe_path(stage)
    safe_path(target)
    if os.name == "nt":
        os.rename(stage, target)
        return
    import ctypes

    api = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        function = api.renamex_np
        function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = function(os.fsencode(stage), os.fsencode(target), 4)
    else:
        function = getattr(api, "renameat2", None)
        if function is None:
            raise ValueError("Exclusive directory publication is unavailable; preserve staging")
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        result = function(-100, os.fsencode(stage), -100, os.fsencode(target), 1)
    if result:
        raise ValueError("Cannot publish onboarding directory; preserve staging")


def read_json(path: Path) -> dict:
    safe_path(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError(
            "Onboarding receipt is missing or invalid; preserve the directory"
        ) from None
    if not isinstance(value, dict):
        raise ValueError("Onboarding receipt is invalid; preserve the directory")
    return value


def read_limited(path: Path, limit: int) -> bytes:
    path = safe_path(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise ValueError("Onboarding input must be a bounded ordinary file")
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise ValueError("Onboarding input exceeds the size limit")
    return content


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def ensure_file(path: Path, content: bytes) -> None:
    safe_path(path)
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError("Owned onboarding files conflict; preserve the directory")
    else:
        write_new(path, content)
