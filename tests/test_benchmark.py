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


def test_wrong_topic_reuse_counts_against_raw_returns_without_becoming_useful():
    rows = [
        dict(
            task_id="one",
            mode=mode,
            tokens=100,
            seconds=1,
            tool_calls=2,
            ask_calls=2,
            matches=0,
            validated=0,
            raw_returns=2,
            wrong_reused=int(mode == "colony"),
            correct=mode == "baseline",
        )
        for mode in ["baseline", "colony"]
    ]
    result = benchmark.report(rows)
    assert result["reuse_rate"] == 0
    assert result["wrong_reuse_rate"] == 0.5
    assert result["correct_tasks"]["colony"] == 0
