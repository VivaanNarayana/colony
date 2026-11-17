"""Opt-in, bounded static-import bundles through the ordinary evidence read path."""

import ast
from pathlib import PurePosixPath

from colony.evidence_refs import EvidenceError
from colony.security import scrub


class ReadDenied(EvidenceError):
    pass


class ReadAccess:
    """Trusted per-entry admission, shared by normal reads, batches and trace reads.

    A host callback reserves ONE unit before any file access, including failures.
    It must return True only when reserved. It is never a model/tool argument.
    """

    def __init__(self, evidence, admit=None):
        self.lock = evidence._lock  # One lock order with read_batch and publication.
        self.admit = admit
        self.original = evidence.read
        self.admitted = self.denied = self._active = 0
        evidence.read = self.read

    def invoke(self, function, *args, **kwargs):
        with self.lock:
            try:
                allowed = self.admit is None or self.admit() is True
            except Exception:
                allowed = False
            if not allowed:
                self.denied += 1
                raise ReadDenied("Research allowance unavailable or exhausted")
            self.admitted += 1
            self._active += 1
            try:
                return function(*args, **kwargs)
            finally:
                self._active -= 1

    def read(self, *args, **kwargs):
        with self.lock:
            if self._active:
                return self.original(*args, **kwargs)
            return self.invoke(self.original, *args, **kwargs)


def local_imports(path, page):
    """Hints from a full, delivered Python page. Never import or probe the filesystem.

    Only module.py candidates are supported. Packages, dynamic loading and runtime
    injection remain unverified; these hints do not establish a binding or absence.
    """
    if not path.endswith(".py") or not page.get("complete_file"):
        return []
    try:
        tree = ast.parse(page["content"])
    except (ValueError, SyntaxError, RecursionError):
        return []
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names, base = [x.name for x in node.names], []
        elif isinstance(node, ast.ImportFrom):
            base = list(PurePosixPath(path).parent.parts)
            if node.level:
                if node.level > len(base):
                    continue
                base = base[: len(base) - node.level + 1]
            else:
                base = []
            names = [node.module] if node.module else [x.name for x in node.names]
        else:
            continue
        for name in names:
            parts = name.split(".")
            if all(x.isidentifier() for x in parts):
                candidate = "/".join([*base, *parts]) + ".py"
                if candidate not in found:
                    found.append(candidate)
                    if len(found) == 32:
                        return found
    return found


def _public_path(path):
    # Pending/error hints must not echo secrets or arbitrary rejected path text.
    safe = (
        scrub(path) == path
        and len(path) <= 1024
        and not any(x in path for x in ("..", ":", "\\"))
        and not any(ord(c) < 32 or ord(c) == 127 for c in path)
        and not path.startswith("/")
    )
    return path if safe else None


def trace_read(evidence, access, paths, max_files):
    # Inputs are bounded by the tool schema; retain a guard for Python callers.
    if (
        not isinstance(paths, list)
        or not 1 <= len(paths) <= 8
        or any(not isinstance(p, str) or not 1 <= len(p) <= 1024 for p in paths)
        or type(max_files) is not int
        or not 1 <= max_files <= 8
    ):
        raise EvidenceError("Invalid trace request")
    with access.lock:
        before = access.admitted, access.denied
        pending = list(dict.fromkeys(paths))
        seen, results, remaining = set(), [], 16000
        while pending and len(results) < max_files and remaining >= 4:
            path = pending.pop(0)
            if path in seen:
                continue
            seen.add(path)
            try:
                # This is the same wrapped read method used by read/read_batch:
                # host allowlists and admission cannot be bypassed by expansion.
                page = access.invoke(evidence.read, path, _max_bytes=remaining)
            except EvidenceError as exc:
                page = {
                    "error": (
                        "Research allowance unavailable or exhausted"
                        if isinstance(exc, ReadDenied)
                        else "Source read failed"
                    ),
                    "evidence_ref": None,
                }
            # Ordered error identity without echoing arbitrary rejected paths.
            if "error" in page:
                page["path"] = _public_path(path)
            results.append(page)
            remaining -= len(page.get("content", "").encode("utf-8"))
            for child in local_imports(path, page):
                if child not in seen and child not in pending and len(pending) < 32:
                    pending.append(child)
        return {
            "results": results,
            "read_accounting": {
                "admitted": access.admitted - before[0],
                "denied": access.denied - before[1],
                "succeeded": sum("content" in p and "error" not in p for p in results),
            },
            "coverage": "Static module hints only; packages, dynamic imports, injected bindings "
            "and repository-wide absence remain unverified. Errors, pending paths "
            "and next_offset pages need further research when relevant.",
            "pending_paths": [_public_path(path) for path in pending[:8]],
            "pending_truncated": len(pending) > 8,
        }
