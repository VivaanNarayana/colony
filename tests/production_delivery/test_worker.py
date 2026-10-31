"""Real worker/Store control with FAKE models/clocks; no performance collection."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import production_delivery_core as core  # noqa: E402

FIXTURES = SCRIPTS.parent / "benchmarks/production_delivery"

BOOTSTRAP = r"""
import sys,types,os,hashlib,re,itertools
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import production_delivery_worker as worker
class Vector:
    def __init__(self,text):
        self.data=[0.0]*384
        for word in re.findall(r"\w+",text.lower()):
            self.data[int(hashlib.sha256(word.encode()).hexdigest(),16)%384]+=1.0
    def tolist(self):return self.data
class Embedding:
    def __init__(self,**kwargs):
        self.model=types.SimpleNamespace(_model_dir=os.environ['COLONY_EMBED_MODEL_DIR'])
    def query_embed(self,text):yield Vector(text)
    def passage_embed(self,text):
        for value in ([text] if isinstance(text,str) else text):yield Vector(value)
class Ranker:
    def __init__(self,**kwargs):pass
    def rerank(self,question,passages):
        if os.environ.get('TEST_MUTATE_FAKE_MODEL') == '1':
            (Path(os.environ['COLONY_RANK_MODEL_DIR'])/'model_optimized.onnx').write_text('changed')
        return [10.0]*len(passages)
fake=types.ModuleType('fastembed');fake.TextEmbedding=Embedding
rerank=types.ModuleType('fastembed.rerank')
cross=types.ModuleType('fastembed.rerank.cross_encoder');cross.TextCrossEncoder=Ranker
sys.modules.update({'fastembed':fake,'fastembed.rerank':rerank,'fastembed.rerank.cross_encoder':cross})
ticks=itertools.count()
worker.time.monotonic=lambda:0.0
worker.time.perf_counter=lambda:next(ticks)/100
worker.time.process_time=lambda:0.0
worker.peak_rss=lambda:None
# The test uses disposable tiny inputs; operational ceiling controls are separately tested.
worker.check_budget=lambda *args:None
worker.run(worker.read(sys.argv[2]),'mvp',sys.argv[3],sys.argv[4],sys.argv[5],sys.argv[6])
"""


@pytest.mark.parametrize("mutate_model", [False, True])
def test_worker_executes_real_store_lifecycle_with_fake_inference_and_clocks(
    tmp_path, mutate_model
):
    revision, archive, manifest = core.source_archive(SCRIPTS.parent, core.MVP)
    source = tmp_path / "implementation"
    core.extract_archive(archive, source)
    models = tmp_path / "FAKE-models"
    models.mkdir()
    for name in [
        "model_optimized.onnx",
        "config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
    ]:
        (models / name).write_text("fake model fixture only")
    tasks = core.fixture_tasks(FIXTURES)
    plan = {
        "contract": core.CONTRACT,
        "arms": {
            a: {"revision": revision, "source_sha256": manifest} for a in ["mvp", "candidate"]
        },
        "tasks": tasks,
        "repetitions": 1,
        "clients": [1, 2],
        "limits": core.LIMITS,
        "case_ids": core.case_ids(tasks, 1, [1, 2]),
        "fixture_sha256": core.file_manifest(FIXTURES),
        "models_sha256": {str(p): core.sha(p) for p in models.iterdir()},
        "environment": core.execution_environment(),
    }
    plan["plan_sha256"] = core.digest(plan)
    plan_file = tmp_path / "plan.json"
    core.put(plan_file, plan)
    work, output = tmp_path / "PRIVATE-unit-state", tmp_path / "FAKE-observations"
    work.mkdir()
    output.mkdir()
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "COLONY_EMBED_MODEL_DIR": str(models),
        "COLONY_RANK_MODEL_DIR": str(models),
        "TEST_MUTATE_FAKE_MODEL": "1" if mutate_model else "0",
    }
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            BOOTSTRAP,
            str(SCRIPTS),
            str(plan_file),
            str(source),
            str(FIXTURES),
            str(work),
            str(output),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if mutate_model:
        assert result.returncode != 0
        assert "Pinned local model changed during collection" in result.stderr
        assert (output / "observations.jsonl").is_file()
        assert not (output / "loaded-implementation.json").exists()
        return
    assert result.returncode == 0, result.stderr
    rows = [core.decode(line) for line in (output / "observations.jsonl").read_text().splitlines()]
    assert [r["case_id"] for r in rows] == plan["case_ids"]
    assert all(r["error"] is None and all(r["checks"].values()) for r in rows), [
        r for r in rows if r["error"] or not all(r["checks"].values())
    ]
    assert all(r["cpu_seconds"] == 0 and r["lifetime_peak_rss_bytes"] is None for r in rows)
    proof = core.read(output / "loaded-implementation.json")
    assert proof["source_sha256"] == manifest
    assert proof["execution_environment"] == plan["environment"]
    assert "colony.store" in proof["module_paths"]
    assert (work / "api-timeout/restored/colony.db").is_file()
    assert (work / "cache-capacity/restored/storage.key").is_file()


def test_worker_rejects_model_hash_change_before_import_or_observations(tmp_path, monkeypatch):
    # Preconditions are exercised without importing the application/model constructors.
    import production_delivery_worker as worker
    from test_contract import sample_plan

    plan = sample_plan()
    for name in list(sys.modules):
        if name == "colony" or name.startswith("colony."):
            monkeypatch.delitem(sys.modules, name)
    source = tmp_path / "source"
    source.mkdir()
    (source / "fixture.txt").write_text("Source identity placeholder; never imported")
    for arm in plan["arms"].values():
        arm["source_sha256"] = core.file_manifest(source)
    model = tmp_path / "fake-model.onnx"
    model.write_bytes(b"changed fake model")
    plan["models_sha256"] = {str(model): "0" * 64}
    plan["tasks"] = core.fixture_tasks(FIXTURES)
    plan["case_ids"] = core.case_ids(plan["tasks"], 1, [1])
    plan["fixture_sha256"] = core.file_manifest(FIXTURES)
    plan["environment"] = core.execution_environment()
    plan["plan_sha256"] = core.digest({k: v for k, v in plan.items() if k != "plan_sha256"})
    with pytest.raises(ValueError, match="Pinned local model differs"):
        worker.run(plan, "mvp", source, FIXTURES, tmp_path, tmp_path)
    assert not (tmp_path / "observations.jsonl").exists()
