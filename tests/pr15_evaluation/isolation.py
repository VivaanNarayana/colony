"""Private imports for immutable historical evaluator controls.

Only the historical *test helper's* absolute imports are rebound in memory. The
exported evaluator and its collector fingerprints are never rewritten. Canonical
``scripts.token_evaluation`` imports, module objects and sys.path are untouched.
"""

import ast
import importlib
import importlib.util
import json
import sys
import uuid
from contextlib import contextmanager
from types import SimpleNamespace


class _HistoricalImports(ast.NodeTransformer):
    def __init__(self, package):
        self.package = package

    def visit_ImportFrom(self, node):
        original = "scripts.token_evaluation"
        if node.module == original or (node.module or "").startswith(original + "."):
            node.module = self.package + node.module[len(original):]
        return node


@contextmanager
def isolated_evaluator(root, receipt):
    """Expose one private package; remove only its own names when finished."""
    package = "_colony_pr15_history_" + uuid.uuid4().hex
    directory = root / "scripts/token_evaluation"
    spec = importlib.util.spec_from_file_location(
        package, directory / "__init__.py", submodule_search_locations=[str(directory)]
    )
    historical = importlib.util.module_from_spec(spec)
    sys.modules[package] = historical
    try:
        spec.loader.exec_module(historical)

        def module(name):
            if not isinstance(name, str) or not name.isidentifier():
                raise ValueError("Expected one historical module name")
            return importlib.import_module(package + "." + name)

        path = root / "tests/token_evaluations/test_product_integration.py"
        product_spec = importlib.util.spec_from_file_location(package + "._product_control", path)
        product = importlib.util.module_from_spec(product_spec)
        sys.modules[product_spec.name] = product
        # This helper imports the frozen research/evidence modules by their old
        # absolute names. Rebind just those import statements, retaining its exact
        # assertions, fixtures, __file__ and product imports.
        tree = _HistoricalImports(package).visit(ast.parse(path.read_bytes(), filename=str(path)))
        exec(compile(tree, str(path), "exec"), product.__dict__)
        catalog = json.loads((path.parent / "fixtures/catalog.json").read_text(encoding="utf-8"))
        yield SimpleNamespace(root=root, receipt=receipt, evidence=module("evidence"),
                              research=module("research"), product=product, catalog=catalog,
                              module=module)
    finally:
        for name in list(sys.modules):
            if name == package or name.startswith(package + "."):
                del sys.modules[name]
