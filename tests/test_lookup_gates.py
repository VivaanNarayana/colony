import json
import sys

import pytest


@pytest.mark.parametrize(
    "metric,value,gate",
    [
        ("uncached_input_plus_output", 119, "observed_uncached_input_plus_output_improve"),
        ("uncached_input_plus_output", 100, "observed_uncached_input_plus_output_improve"),
        ("tokens", 101, "observed_live_tokens_improve"),
        ("seconds", 101, "observed_live_time_improves"),
        ("correct", 23, "correctness_not_reduced"),
        ("protocol_violations", 1, "feature_follows_tool_protocol"),
    ],
)
def test_one_regression_blocks_even_when_other_metrics_improve(monkeypatch, metric, value, gate):
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "scripts"))
    from analyze_lookup_live import evaluate_gates

    main = {
        "tokens": 100,
        "uncached_input_plus_output": 100,
        "seconds": 100,
        "correct": 24,
        "tasks": 24,
        "valid_citations": 24,
        "protocol_violations": 0,
    }
    feature = {
        **main,
        "tokens": 90,
        "uncached_input_plus_output": 90,
        "seconds": 90,
        "protocol_violations": 0,
        metric: value,
    }
    gates = evaluate_gates(
        main,
        feature,
        {
            "schema_version": 2,
            "implementation_isolated": True,
            "quality_passed": True,
            "lookup_improves": True,
            "same_implementation": False,
        },
        {"tests_passed": 90, "tests_failed": 0, "ruff_passed": True},
        True,
        {key: [0.01, 0.2] for key in ["tokens", "seconds", "uncached_input_plus_output"]},
    )
    assert len(gates) == 8
    assert [name for name, passed in gates.items() if not passed] == [gate]


def test_failed_analysis_retains_report_and_fails_command(monkeypatch, tmp_path):
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "scripts"))
    import analyze_lookup_live as analyzer

    local = tmp_path / "local.json"
    local.write_text("{}")
    report = {
        "all_gates_passed": False,
        "blocking_gates": ["observed_uncached_input_plus_output_improve"],
    }
    monkeypatch.setattr(analyzer, "compare", lambda *args, **kwargs: report)
    monkeypatch.setattr(sys, "argv", ["analyze", "--results", str(tmp_path), "--local", str(local)])
    with pytest.raises(SystemExit) as error:
        analyzer.main()
    assert error.value.code == 1
    assert json.loads((tmp_path / "comparison.json").read_text()) == report
