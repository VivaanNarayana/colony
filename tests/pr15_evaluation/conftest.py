import os

import pytest

from scripts.pr15_evaluation import prepare

from .isolation import isolated_evaluator


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
    with isolated_evaluator(root, receipt) as historical:
        yield historical
