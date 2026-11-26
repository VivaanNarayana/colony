"""Opt-in, bounded static-import bundles through the ordinary evidence read path."""

import ast
from pathlib import PurePosixPath

from colony.evidence_refs import EvidenceError, _PreparedRead
from colony.security import scrub
from colony.validation import repo_file


class ReadDenied(EvidenceError):
    pass


class ReadAccess:
    """Trusted per-entry admission, shared by normal reads, batches and trace reads.

    A host callback reserves ONE unit before any source-content attempt, including failures.
    It must return True only when reserved. It is never a model/tool argument.
    """

    def __init__(self, evidence, admit=None):
        self.evidence = evidence
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


def local_imports(path, prepared):
    """Hints from a full, staged Python page. Never import or probe the filesystem.

    Only module.py candidates are supported. Packages, dynamic loading and runtime
    injection remain unverified; these hints do not establish a binding or absence.
    """
    if not path.endswith(".py") or not prepared.whole_range or prepared.offset:
        return []
    source = prepared.content.decode("utf-8")
    if len(source) != prepared.span_length:
        return []
    try:
        tree = ast.parse(source)
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


def called_import_paths(span):
    """Find direct calls to imported functions in a small, visible module prefix.

    Only flat modules and straight-line function bodies are supported. This is a
    navigation hint, so ambiguous syntax can be skipped without fetching bytes.
    """
    ref, text = span["ref"], span["text"]
    if (span["line"] != 1 or len(text) > 4096 or not ref.endswith(".py")
            or not ref.removesuffix(".py").isidentifier()):
        return set()
    try:
        body = ast.parse(text).body
    except (SyntaxError, ValueError, RecursionError):
        return set()

    imports = {}
    functions = []
    defined = set()
    for node in body:
        if isinstance(node, ast.ImportFrom) and not node.level and node.module.isidentifier():
            for alias in node.names:
                name = alias.asname or alias.name
                if name == "*" or name in defined:
                    return set()
                defined.add(name)
                imports[name] = node.module + ".py"
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name not in defined:
            defined.add(node.name)
            functions.append(node)
        else:
            return set()

    paths = set()
    for fn in functions:
        if not all(isinstance(stmt, (ast.Assign, ast.Expr, ast.Return)) for stmt in fn.body):
            continue
        # Function parameters and assignments can shadow a module-level import.
        local = {arg.arg for arg in ast.walk(fn.args) if isinstance(arg, ast.arg)}
        local.update(n.id for n in ast.walk(fn)
                     if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store))
        for stmt in fn.body:
            call = stmt.value
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
                name = call.func.id
                if name in imports and name not in local:
                    paths.add(imports[name])
    return paths


def _local_candidate(access, path):
    """Select exact repo-file candidates using authorized metadata, never source bytes.

    This is not import resolution or evidence of a runtime binding. The eventual
    content read still needs the host wrapper, admission and current authorization.
    Missing, unsafe and unavailable candidates are all unresolved, not absence facts.
    """
    evidence = access.evidence
    # A custom read wrapper has an opaque path policy unless its host supplies
    # the corresponding metadata predicate. Explicit reads still use the wrapper.
    if evidence.path_allowed is None and evidence.read != access.read:
        return False
    try:
        if _public_path(path) is None:
            return False
        evidence._authorize()
        repo_file(evidence.workflow.repo, path, path_allowed=evidence.path_allowed)
        return True
    except (OSError, ValueError, RuntimeError):
        return False


def _trace_error(path, exc):
    return {"error": ("Research allowance unavailable or exhausted"
                      if isinstance(exc, ReadDenied) else "Source read failed"),
            "path": _public_path(path), "evidence_ref": None}


def _deliver_trace(evidence, paths, staged, imports):
    # Replay reachability from the explicit roots. A withheld page contributes
    # neither handles nor import names, even if its dependencies were staged.
    reachable = set(paths)
    queue = list(dict.fromkeys(paths))
    delivered = {}
    for path in queue:
        if path not in staged:
            continue
        value = staged[path]
        if isinstance(value, _PreparedRead):
            try:
                value = evidence._complete_read(value, len(value.content))
            except EvidenceError as exc:
                value = _trace_error(path, exc)
            else:
                for child in imports[path]:
                    if child not in reachable:
                        reachable.add(child)
                        queue.append(child)
        delivered[path] = value
    return [delivered[path] for path in staged if path in delivered], reachable


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
        staged, imports, unresolved = {}, {}, {}
        remaining = 16000
        while pending and len(staged) < max_files and remaining >= 4:
            path = pending.pop(0)
            try:
                # This is the same wrapped read method used by read/read_batch:
                # host allowlists and admission cannot be bypassed by expansion.
                value = access.invoke(evidence.read, path, _max_bytes=remaining, _defer_reference=True)
                if not isinstance(value, _PreparedRead) or value.owner is not evidence or value.ref != path:
                    raise EvidenceError("Invalid prepared evidence")
            except EvidenceError as exc:
                staged[path] = _trace_error(path, exc)
                continue
            staged[path] = value
            remaining -= len(value.content)
            imports[path] = local_imports(path, value)
            for child in imports[path]:
                if (child not in staged and child not in pending and child not in unresolved
                        and len(pending) < 32):
                    if _local_candidate(access, child):
                        pending.append(child)
                    else:
                        unresolved[child] = None
        results, reachable = _deliver_trace(evidence, paths, staged, imports)
        pending = [path for path in pending if path in reachable]
        if evidence.path_allowed is not None:
            for path in pending.copy():
                if path not in paths and not _local_candidate(access, path):
                    pending.remove(path)
                    unresolved[path] = None
        unresolved_paths = [_public_path(path) for path in unresolved if path in reachable]
        result = {
            "results": results,
            "read_accounting": {
                "admitted": access.admitted - before[0],
                "denied": access.denied - before[1],
                "succeeded": sum("content" in p and "error" not in p for p in results),
            },
            "coverage": "Static module hints only; packages, dynamic imports, injected bindings "
            "and repository-wide absence remain unverified. Errors, unresolved/pending paths "
            "and next_offset pages need further research when relevant.",
            "pending_paths": [_public_path(path) for path in pending[:8]],
            "pending_truncated": len(pending) > 8,
        }
        if unresolved_paths:
            result.update(unresolved_paths=unresolved_paths[:8],
                          unresolved_truncated=len(unresolved_paths) > 8)
        return result
