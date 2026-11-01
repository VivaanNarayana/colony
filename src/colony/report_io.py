"""Small, dependency-free IO primitives for explicitly selected offline reports."""

import hashlib
import json
import math
import os
import stat
from pathlib import Path, PurePosixPath


def require(condition, message):
    if not condition:
        raise ValueError(message)


def decode(text):
    # Enforce a depth independent of the host's Python recursion limit, while
    # ignoring bracket characters and escapes inside JSON strings.
    require(isinstance(text, str), "Invalid report JSON text")
    depth, quoted, escaped = 0, False, False
    for character in text:
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif character == '"':
            quoted = True
        elif character in "{[":
            depth += 1
            require(depth <= 64, "Report JSON nesting limit")
        elif character in "}]":
            depth -= 1

    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "Duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("Nonfinite JSON constant: " + value)

    def finite_float(value):
        parsed = float(value)
        require(math.isfinite(parsed), "Nonfinite JSON number")
        return parsed

    try:
        return json.loads(
            text, object_pairs_hook=pairs, parse_constant=invalid_constant, parse_float=finite_float
        )
    except (ValueError, RecursionError, OverflowError):
        raise ValueError("Invalid or unsupported report JSON") from None


def linked(path):
    info = Path(path).lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def checked_path(path):
    """Reject symlinks and Windows reparse points in every existing component."""
    path = Path(os.path.abspath(path))
    for component in reversed((path, *path.parents)):
        if component.exists() or component.is_symlink():
            require(not linked(component), "Linked source/output path")
    return path


def bounded_bytes(path, limit):
    path = checked_path(path)
    require(path.is_file() and path.stat().st_size <= limit, "File size/type limit")
    # O_NOFOLLOW closes the last-component race where the platform supports it.
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(fd, "rb") as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "Nonregular source")
        value = stream.read(limit + 1)
    require(len(value) <= limit, "File size limit")
    checked_path(path)
    return value


def sha(path):
    return hashlib.sha256(bounded_bytes(path, 64 << 20)).hexdigest()


def read(path):
    return decode(bounded_bytes(path, 1 << 20).decode("utf-8"))


def put(path, value):
    path = checked_path(path)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def relative_file(root, ref):
    require(isinstance(ref, str) and ref and ref.isprintable(), "Invalid source ref")
    require("\\" not in ref and ":" not in ref, "Non-relative source ref")
    require(all(p not in {"", ".", ".."} for p in ref.split("/")), "Unsafe source ref")
    require(not PurePosixPath(ref).is_absolute(), "Absolute source ref")
    root = checked_path(root)
    path = checked_path(root.joinpath(*ref.split("/")))
    require(path.is_relative_to(root) and path.is_file(), "Missing/outside source")
    return path


def files_under(root):
    root = checked_path(root)
    require(root.is_dir(), "Missing logs directory")
    pending, count = [root], 0
    while pending:
        directory = pending.pop()
        checked_path(directory)
        entries = []
        with os.scandir(directory) as iterator:
            for entry in iterator:
                count += 1
                require(count <= 4096, "Log inventory entry limit")
                entries.append(Path(entry.path))
        for path in sorted(entries):
            checked_path(path)
            if path.is_dir():
                pending.append(path)
            elif path.is_file():
                yield path
