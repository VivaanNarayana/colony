import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "benchmark", Path(__file__).parents[1] / "scripts/benchmark.py"
)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def test_task_set_and_paired_metrics():
    assert len(benchmark.tasks()) == 24
    rows = [
        {
            "task_id": "one",
            "mode": mode,
            "tokens": tokens,
            "seconds": 10,
            "tool_calls": 4,
            "ask_calls": 2,
            "matches": 1,
            "validated": 1,
            "wrong_reused": 0,
            "correct": True,
        }
        for mode, tokens in [("baseline", 100), ("colony", 70)]
    ]
    result = benchmark.report(rows)
    assert result["reuse_rate"] == 0.5
    assert result["validated_hit_rate"] == 1
    assert result["savings"]["tokens"]["reduction"] == pytest.approx(0.3)
    with pytest.raises(ValueError):
        benchmark.report(rows[:1])
    with pytest.raises(ValueError):
        benchmark.report(rows + rows)
    with pytest.raises(ValueError):
        benchmark.report([{**r, "tokens": -1} for r in rows])
