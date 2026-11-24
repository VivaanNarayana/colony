"""Small, dependency-free IO primitives for explicitly selected offline reports."""

import hashlib
import json
import math
import os
import re
import stat
from pathlib import Path, PurePosixPath


def require(condition, message):
    if not condition:
        raise ValueError(message)


def jsonl_lines(data: bytes, *, errors="strict"):
    """Split on record delimiters before decoding Unicode inside JSON strings."""
    return [line.decode("utf-8", errors=errors) for line in data.splitlines()]


_SPACE = re.compile(r"[ \t\r\n]*")
_ATOM = re.compile(r"true|false|null|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")


def _outer_event_type(text):
    """Validate JSON iteratively, retaining only the root object's type field."""
    # One byte per open container. Oversized optional payloads never become
    # Python dictionaries/lists, even when deeper than the interpreter stack.
    obj_first, key, colon, obj_value, obj_tail, arr_first, arr_value, arr_tail = range(8)
    pos = _SPACE.match(text).end()
    if pos == len(text) or text[pos] != "{":
        return None
    pos += 1
    stack = bytearray([obj_first])
    seen_type = reading_type = False
    event_type = None
    while stack:
        pos = _SPACE.match(text, pos).end()
        if pos == len(text):
            return None
        char, state = text[pos], stack[-1]
        pos += 1
        if state in (obj_first, key):
            if char == "}" and state == obj_first:
                stack.pop()
                continue
            if char != '"':
                return None
            name, pos = json.decoder.scanstring(text, pos)
            if len(stack) == 1:
                reading_type = name == "type"
                if reading_type and seen_type:
                    return None
                seen_type |= reading_type
            stack[-1] = colon
        elif state == colon:
            if char != ":":
                return None
            stack[-1] = obj_value
        elif state in (obj_tail, arr_tail):
            if char == ("}" if state == obj_tail else "]"):
                stack.pop()
            elif char == ",":
                stack[-1] = key if state == obj_tail else arr_value
            else:
                return None
        elif state == arr_first and char == "]":
            stack.pop()
        else:
            stack[-1] = obj_tail if state == obj_value else arr_tail
            if char == '"':
                value, pos = json.decoder.scanstring(text, pos)
                if len(stack) == 1 and reading_type:
                    event_type = value
            elif char in "{[":
                stack.append(obj_first if char == "{" else arr_first)
            else:
                atom = _ATOM.match(text, pos - 1)
                if atom is None:
                    return None
                pos = atom.end()
    return event_type if _SPACE.match(text, pos).end() == len(text) else None


def decode_native_event(text, *, optional_responses=True, decoder=json.loads, deep_response_decoder=None):
    """Isolate a rejected optional envelope only after validating its syntax and unique root type."""
    try:
        return decoder(text)
    except (RecursionError, ValueError) as exc:
        if not optional_responses:
            raise
        try:
            kind = _outer_event_type(text)
        except ValueError:
            raise exc from None
        if kind in ("response.created", "response.completed"):
            if deep_response_decoder is not None:
                return deep_response_decoder(text)
            return {"type": kind}
        raise


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

    return parse_json(text)


def parse_json(text):
    """Reject ambiguous keys and nonfinite numbers regardless of the enclosing format."""
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
