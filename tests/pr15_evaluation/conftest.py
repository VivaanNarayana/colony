import importlib
import importlib.util
import json
import os
import sys
from types import SimpleNamespace

import pytest

from scripts.pr15_evaluation import prepare


@pytest.fixture
def local_o200k_tokenizer(monkeypatch):
    configured = os.environ.get("COLONY_TEST_CONTEXT_TOKENIZER")
    if not configured:
        pytest.skip("Explicit verified local tokenizer fixture unavailable")
    pytest.importorskip("tiktoken")
    monkeypatch.setenv("COLONY_CONTEXT_TOKENIZER", configured)
    return configured


@pytest.fixture(scope="session")
def evaluator(tmp_path_factory):
    root = tmp_path_factory.mktemp("pr15-overlay") / "export"
    receipt = prepare(root)
    sys.path.insert(0, str(root))
    try:
        evidence = importlib.import_module("scripts.token_evaluation.evidence")
        research = importlib.import_module("scripts.token_evaluation.research")
        path = root / "tests/token_evaluations/test_product_integration.py"
        spec = importlib.util.spec_from_file_location("pr15_real_product", path)
        product = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(product)
        catalog = json.loads((path.parent / "fixtures/catalog.json").read_text(encoding="utf-8"))
        yield SimpleNamespace(root=root, receipt=receipt, evidence=evidence,
                              research=research, product=product, catalog=catalog)
    finally:
        sys.path.remove(str(root))
        for name in list(sys.modules):
            if name == "scripts.token_evaluation" or name.startswith("scripts.token_evaluation."):
                del sys.modules[name]
