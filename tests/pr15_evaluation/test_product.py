"""Real offline product journeys and deliberate broken-runtime countercontrols."""

import hashlib

import pytest

from colony.models import ContextBudget, Source


@pytest.mark.parametrize("workload", ["gateway", "worker"])
@pytest.mark.parametrize("encoding,maximum", [("utf8_bytes", 4096), ("o200k_base", 2048)])
def test_actual_adaptive_journey(evaluator, tmp_path, monkeypatch, workload, encoding, maximum):
    budget = ContextBudget(max_tokens=maximum, encoding=encoding)
    evaluator.product.test_authenticated_product_scenarios(
        tmp_path, monkeypatch, workload, budget=budget
    )


@pytest.mark.parametrize("workload", ["gateway", "worker"])
def test_tight_byte_budget_is_an_explicit_limited_diagnostic(
    evaluator, tmp_path, monkeypatch, workload
):
    evaluator.product.test_authenticated_product_scenarios(
        tmp_path, monkeypatch, workload,
        budget=ContextBudget(max_tokens=2048, encoding="utf8_bytes"), expect_limited=True,
    )


@pytest.mark.parametrize("mutation,expected", [
    ("push_noop", "publication must persist one actual Store trail"),
    ("always_miss", "warm reuse requires a validated product hit"),
])
def test_broken_adaptive_memory_fails_its_contract(
    evaluator, tmp_path, monkeypatch, mutation, expected
):
    from colony.store import Store

    if mutation == "push_noop":
        def noop(self, trail, grant):
            return {"stored": True, "id": trail.id}
        monkeypatch.setattr(Store, "push", noop)
    else:
        monkeypatch.setattr(Store, "ask", lambda self, *a, **kw: {
            "hit": False, "reason": "no_relevant_trail",
        })
    with pytest.raises(AssertionError, match=expected):
        evaluator.product.test_authenticated_product_scenarios(
            tmp_path, monkeypatch, "gateway",
            budget=ContextBudget(max_tokens=2048, encoding="o200k_base"),
        )


def test_explicit_global_passage_selection_is_not_replaced_by_bundle_expansion(tmp_path):
    from colony.context import excerpts

    sources = []
    for name, text in [("selected.py", "VALUE = 1\n"), ("other.py", "OTHER = 2\n")]:
        raw = text.encode()
        (tmp_path / name).write_bytes(raw)
        sources.append(Source(ref=name, content_hash=hashlib.sha256(raw).hexdigest()))
    result = excerpts(
        tmp_path, sources, "VALUE", preferred=[("selected.py", 1)],
        claims="VALUE is 1", whole_files=True, adaptive=True,
    )
    assert {item["ref"] for item in result} == {"selected.py"}
