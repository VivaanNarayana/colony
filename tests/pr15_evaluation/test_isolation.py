"""Historical checks must not consume or evict the current evaluator package."""

import ast
import copy
import hashlib
import importlib
import subprocess
import sys
from pathlib import Path

import pytest

from .isolation import _HistoricalImports, isolated_evaluator


def test_frozen_modules_are_private_without_rebinding_current(evaluator):
    current = {name: importlib.import_module("scripts.token_evaluation." + name)
               for name in ("protocol", "evidence", "research", "runner", "report", "native")}
    for name, canonical in current.items():
        frozen = evaluator.module(name)
        assert frozen is not canonical
        assert Path(frozen.__file__).resolve().parent == (
            evaluator.root / "scripts/token_evaluation").resolve()
        assert sys.modules[canonical.__name__] is canonical
    assert evaluator.product.create_server is evaluator.research.create_server
    assert evaluator.product.test_authenticated_product_scenarios.__globals__["__file__"] == str(
        evaluator.root / "tests/token_evaluations/test_product_integration.py")


def test_private_teardown_preserves_current_modules_and_export_bytes(evaluator):
    names = ("protocol", "evidence", "research", "runner", "report", "native")
    current = {"scripts.token_evaluation." + name: importlib.import_module(
        "scripts.token_evaluation." + name) for name in names}
    before_path = list(sys.path)
    before_modules = set(sys.modules)
    with isolated_evaluator(evaluator.root, evaluator.receipt) as historical:
        for name in names:
            assert historical.module(name) is not current["scripts.token_evaluation." + name]
        private_names = set(sys.modules) - before_modules
        assert any(name.startswith("_colony_pr15_history_") for name in private_names)
    assert sys.path == before_path
    assert all(sys.modules[name] is value for name, value in current.items())
    assert not any(name in sys.modules for name in private_names
                   if name.startswith("_colony_pr15_history_"))
    for name, expected in evaluator.receipt["files"].items():
        assert hashlib.sha256((evaluator.root / name).read_bytes()).hexdigest() == expected


@pytest.mark.parametrize("order", ["current_first", "historical_first"])
def test_import_orders_in_fresh_process_preserve_canonical_package(evaluator, order):
    helper = Path(__file__).with_name("isolation.py")
    # A fresh interpreter makes the historical-first case genuinely start before
    # any current evaluator import. No native/preflight/model boundary is invoked.
    script = r"""
import importlib
import importlib.util
import json
import sys
from pathlib import Path
helper, root, order = sys.argv[1:]
spec = importlib.util.spec_from_file_location("history_isolation_control", helper)
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)
root = Path(root)
names = ("protocol", "evidence", "research", "runner", "report", "native")
def canonical():
    modules = {name: importlib.import_module("scripts.token_evaluation." + name) for name in names}
    for name, module in modules.items():
        assert Path(module.__file__).resolve().parent == Path.cwd() / "scripts/token_evaluation"
    return modules
before_path = list(sys.path)
current = canonical() if order == "current_first" else None
with loader.isolated_evaluator(root, {}) as historical:
    frozen = {name: historical.module(name) for name in names}
    current = current or canonical()
    assert all(frozen[name] is not current[name] for name in names)
    assert all(Path(module.__file__).resolve().parent == root / "scripts/token_evaluation"
               for module in frozen.values())
    assert historical.product.create_server is frozen["research"].create_server
    assert frozen["evidence"].__name__ in historical.product.test_authenticated_product_scenarios.__code__.co_names
assert sys.path == before_path
assert all(sys.modules[module.__name__] is module for module in current.values())
assert not any(name.startswith("_colony_pr15_history_") for name in sys.modules)
assert current["protocol"].CONTRACT != frozen["protocol"].CONTRACT
print(json.dumps({"order": order, "canonical_preserved": True}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(helper), str(evaluator.root), order],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True,
        encoding="utf-8", timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"canonical_preserved": true' in result.stdout


def test_helper_transform_changes_only_historical_import_module_names(evaluator):
    path = evaluator.root / "tests/token_evaluations/test_product_integration.py"
    original = ast.parse(path.read_bytes(), filename=str(path))
    expected = copy.deepcopy(original)
    changes = []
    for node in ast.walk(expected):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "scripts.token_evaluation."):
            changes.append((node.lineno, node.module))
            node.module = "private_history" + node.module[len("scripts.token_evaluation"):]
    actual = _HistoricalImports("private_history").visit(copy.deepcopy(original))
    assert {name for _, name in changes} == {
        "scripts.token_evaluation.research", "scripts.token_evaluation.evidence"}
    # Includes locations, assertions, call arguments, aliases and every AST field.
    assert ast.dump(actual, include_attributes=True) == ast.dump(expected, include_attributes=True)
    assert ast.dump(original, include_attributes=True) != ast.dump(actual, include_attributes=True)
