"""Bounded source selection for explicit saved-finding recall."""

import ast

from pydantic_core import to_json

RECALL_PREFERENCE = "colony-recall-dependency"
MAX_RECALL_BYTES = 512


def _called_import_paths(span):
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


def suggested_path(evidence, refs, validated):
    """Choose one missing attached file from direct calls in delivered evidence."""
    usable = {s["ref"] for s in evidence if s.get("complete_file") or s["text"].strip()}
    missing = (refs & validated) - usable
    if not missing:
        return None
    called = set()
    for span in evidence:
        if span["ref"] in validated:
            called.update(_called_import_paths(span))
    targets = called & missing
    if len(targets) != 1:
        return None
    path = targets.pop()
    hint = {"suggested_read": {"path": path}}
    if path.removesuffix(".py") + "/__init__.py" in refs or len(to_json(hint)) > 128:
        return None
    return path
