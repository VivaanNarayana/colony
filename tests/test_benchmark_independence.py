"""Adversarial controls for the evaluator; no model thresholds or product mocks."""

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parents[1] / "scripts"


@pytest.fixture(autouse=True)
def script_path(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))


def packet_for(case, repo):
    fact = case["expected"]["evidence_contains"][0]
    text = case["files"]["ops.md"]
    (repo / "ops.md").write_bytes(text.encode())
    return {
        "hit": True,
        "status": "validated",
        "namespace": "ops",
        "sources": [{"ref": "ops.md", "content_hash": hashlib.sha256(text.encode()).hexdigest()}],
        "evidence": [{"ref": "ops.md", "line": text.splitlines().index(fact) + 1, "text": fact}],
    }


def test_constant_miss_and_constant_hit_cannot_pass_same_workload(tmp_path):
    from lookup_workload import assess, workload

    cases = workload(9127, 2)["cases"]
    assert any(assess(c, {"hit": False}, tmp_path) for c in cases)
    assert any(assess(c, {"hit": True, "status": "validated"}, tmp_path) for c in cases)
    assert workload(9127, 2) == workload(9127, 2)
    assert workload(9127, 2) != workload(9128, 2)


@pytest.mark.parametrize("corruption", ["fact", "hash", "line", "scope", "escape", "missing"])
def test_oracle_rejects_forged_evidence_even_if_both_implementations_agree(tmp_path, corruption):
    from lookup_workload import assess, workload

    case = next(c for c in workload(53, 1)["cases"] if c["family"] == "exact-fact")
    packet = packet_for(case, tmp_path)
    assert assess(case, packet, tmp_path) == []
    if corruption == "fact":
        packet["evidence"][0]["text"] = "The answer is something else."
    elif corruption == "hash":
        packet["sources"][0]["content_hash"] = "0" * 64
    elif corruption == "line":
        packet["evidence"][0]["line"] += 1
    elif corruption == "scope":
        packet["namespace"] = "private"
    elif corruption == "escape":
        packet["evidence"][0]["ref"] = "../secret.md"
    else:
        (tmp_path / "ops.md").unlink()
    assert assess(case, packet, tmp_path)


def test_oracle_rejects_current_evidence_for_stale_or_expired_packets(tmp_path):
    from lookup_workload import assess, workload

    for case in workload(53, 1)["cases"]:
        if case["family"] in {"changed-source", "deleted-source", "expired", "no-source"}:
            packet = {**case["expected"], "evidence": [{"text": "old fact"}]}
            assert assess(case, packet, tmp_path)


@pytest.mark.parametrize("family", ["changed-source", "deleted-source", "expired", "no-source"])
@pytest.mark.parametrize("namespace", ["finance", None])
def test_scope_checks_apply_to_stale_and_unverified_hits(tmp_path, family, namespace):
    from lookup_workload import assess, workload

    case = next(c for c in workload(53, 1)["cases"] if c["family"] == family)
    packet = {**case["expected"], "namespace": "ops"}
    assert assess(case, packet, tmp_path) == []
    packet["namespace"] = namespace
    assert "hit namespace is outside requested scopes" in assess(case, packet, tmp_path)


@pytest.mark.parametrize("corruption", ["missing", "hash", "escape", "absolute-escape"])
def test_every_declared_source_must_validate_without_a_displayed_excerpt(tmp_path, corruption):
    from lookup_workload import assess, workload

    case = next(c for c in workload(53, 1)["cases"] if c["family"] == "exact-fact")
    repo = tmp_path / "repo"
    repo.mkdir()
    packet = packet_for(case, repo)
    extra = repo / "extra.md"
    extra.write_bytes(b"Additional source.\n")
    source = {"ref": "extra.md", "content_hash": hashlib.sha256(extra.read_bytes()).hexdigest()}
    packet["sources"].append(source)
    assert assess(case, packet, repo) == []
    if corruption == "missing":
        extra.unlink()
    elif corruption == "hash":
        source["content_hash"] = "0" * 64
    else:
        outside = tmp_path / "outside.md"
        outside.write_bytes(extra.read_bytes())
        source["ref"] = "../outside.md" if corruption == "escape" else str(outside)
    assert assess(case, packet, repo)


@pytest.mark.parametrize("forged_first", [False, True])
def test_duplicate_source_refs_cannot_hide_a_forged_fingerprint(tmp_path, forged_first):
    from lookup_workload import assess, workload

    case = next(c for c in workload(53, 1)["cases"] if c["family"] == "exact-fact")
    packet = packet_for(case, tmp_path)
    forged = {**packet["sources"][0], "content_hash": "0" * 64}
    packet["sources"].insert(0 if forged_first else 1, forged)
    assert "source fingerprint does not match current bytes" in assess(case, packet, tmp_path)


@pytest.mark.parametrize("name", ["lookup", "trial[1]"])
@pytest.mark.parametrize("suffix", ["plan.json", "measurements.jsonl", "summary.json", "partial"])
def test_run_preflight_preserves_existing_artifacts_for_literal_names(tmp_path, name, suffix):
    from benchmark_lookup import require_fresh_run

    runtime, output = tmp_path / "fresh-runtime", tmp_path / "results"
    output.mkdir()
    artifact = output / f"{name}-{suffix}"
    artifact.write_bytes(b"Preserve original evidence.\n")
    with pytest.raises(ValueError, match="retain previous attempts"):
        require_fresh_run(runtime, output, name)
    assert artifact.read_bytes() == b"Preserve original evidence.\n"
    assert not runtime.exists()


def test_run_preflight_allows_fresh_paths_and_unrelated_names(tmp_path):
    from benchmark_lookup import require_fresh_run

    runtime, output = tmp_path / "fresh-runtime", tmp_path / "results"
    require_fresh_run(runtime, output, "trial[1]")
    assert not runtime.exists() and not output.exists()
    output.mkdir()
    unrelated = output / "trial1-plan.json"
    unrelated.write_bytes(b"A different run.\n")
    require_fresh_run(runtime, output, "trial[1]")
    assert unrelated.read_bytes() == b"A different run.\n"
    assert not runtime.exists()


def test_run_preflight_rejects_existing_runtime_and_matching_directories(tmp_path):
    from benchmark_lookup import require_fresh_run

    runtime, output = tmp_path / "runtime", tmp_path / "results"
    runtime.mkdir()
    with pytest.raises(ValueError, match="retain previous attempts"):
        require_fresh_run(runtime, output, "lookup")
    output.mkdir()
    (output / "trial[1]-partial").mkdir()
    with pytest.raises(ValueError, match="retain previous attempts"):
        require_fresh_run(tmp_path / "fresh-runtime", output, "trial[1]")


@pytest.mark.skipif(os.name != "nt", reason="Windows paths are case-insensitive")
def test_run_preflight_rejects_windows_case_aliases(tmp_path):
    from benchmark_lookup import require_fresh_run

    artifact = tmp_path / "TRIAL[1]-plan.json"
    artifact.write_bytes(b"Preserve original evidence.\n")
    with pytest.raises(ValueError, match="retain previous attempts"):
        require_fresh_run(tmp_path / "fresh-runtime", tmp_path, "trial[1]")
    assert artifact.read_bytes() == b"Preserve original evidence.\n"


@pytest.mark.parametrize(
    "packet", [None, {}, {"hit": 0}, {"hit": True, "status": "validated", "evidence": None}]
)
def test_malformed_packets_are_recorded_as_quality_failures(tmp_path, packet):
    from lookup_workload import assess, workload

    case = next(c for c in workload(53, 1)["cases"] if c["family"] == "exact-fact")
    assert assess(case, packet, tmp_path)


@pytest.mark.parametrize("selected", ["main", "feature"])
def test_local_worker_isolates_all_transitive_modules(tmp_path, selected):
    packages = {}
    for name in ["main", "feature"]:
        source = tmp_path / name / "src"
        package = source / "colony"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text(f"MARKER = {name!r}\n")
        for module in ["store", "context", "retrieval", "models", "security", "validation"]:
            (package / f"{module}.py").write_text(f"from colony import MARKER\nVALUE = {name!r}\n")
        packages[name] = source
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(SCRIPTS / "lookup_benchmark_worker.py"),
            "--source",
            str(packages[selected]),
            "--root",
            str(tmp_path / "run"),
            "--probe-only",
        ],
        env={**os.environ, "PYTHONPATH": str(packages["feature"])},
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    proof = json.loads(result.stdout)["proof"]
    for module in ["store", "context", "retrieval", "models", "security", "validation"]:
        path = Path(proof["module_paths"]["colony." + module])
        assert path.is_relative_to(packages[selected])
        assert (
            proof["source_sha256"][f"colony/{module}.py"]
            == hashlib.sha256(path.read_bytes()).hexdigest()
        )
        assert f"VALUE = {selected!r}" in path.read_text()


def test_noise_zero_cost_and_invalid_measurements_do_not_establish_improvement():
    from benchmark_statistics import reduction_interval, supported_improvement

    groups = [{"main": {"cost": 100}, "feature": {"cost": v}} for v in [60, 90, 100, 140]]
    interval = reduction_interval(groups, "cost")
    assert sum(p["feature"]["cost"] for p in groups) < 400  # Observed point win.
    assert interval[0] < 0 < interval[1]
    assert not supported_improvement(400, 390, interval)
    assert not supported_improvement(0, 0, [0.1, 0.2])
    assert not supported_improvement(100, 90, None)
    assert not supported_improvement(100, 90, [float("nan"), 0.2])
    assert reduction_interval(groups[:1], "cost") is None
    broken = copy.deepcopy(groups)
    broken[0]["main"]["cost"] = float("inf")
    with pytest.raises(ValueError):
        reduction_interval(broken, "cost")


def test_citation_loss_and_zero_quality_block_a_faster_cheaper_candidate():
    from analyze_lookup_live import evaluate_gates

    main = {
        "tasks": 5,
        "correct": 5,
        "valid_citations": 5,
        "protocol_violations": 0,
        "tokens": 100,
        "seconds": 100,
        "uncached_input_plus_output": 100,
    }
    feature = {**main, "tokens": 50, "seconds": 50, "uncached_input_plus_output": 50}
    local = {
        "schema_version": 2,
        "quality_passed": True,
        "lookup_improves": True,
        "implementation_isolated": True,
        "same_implementation": False,
    }
    verification = {"tests_passed": 1, "tests_failed": 0, "ruff_passed": True}
    intervals = {k: [0.1, 0.7] for k in ["tokens", "seconds", "uncached_input_plus_output"]}
    assert all(evaluate_gates(main, feature, local, verification, True, intervals).values())
    feature["valid_citations"] = 4
    assert not evaluate_gates(main, feature, local, verification, True, intervals)[
        "correctness_not_reduced"
    ]
    feature["correct"] = main["correct"] = 0
    assert not evaluate_gates(main, feature, local, verification, True, intervals)[
        "correctness_not_reduced"
    ]
    feature["correct"] = 5
    feature["valid_citations"] = 5
    main["protocol_violations"] = 1
    assert not evaluate_gates(main, feature, local, verification, True, intervals)[
        "feature_follows_tool_protocol"
    ]
