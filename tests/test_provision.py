import hashlib
import os
import subprocess

import httpx
import pytest

from colony import provision, retrieval


@pytest.fixture
def tiny_models(monkeypatch):
    content = {
        "embed": {"config.json": b"embed", "model.onnx": b"weights"},
        "rank": {"config.json": b"rank", "onnx/model.onnx": b"reranker"},
    }
    manifest = {
        name: {
            "repo": "test/" + name,
            "revision": "a" * 40,
            "files": {
                filename: (len(data), hashlib.sha256(data).hexdigest())
                for filename, data in files.items()
            },
        }
        for name, files in content.items()
    }
    monkeypatch.setattr(provision, "MANIFEST", manifest)
    return content


def populate(root, content):
    for name, files in content.items():
        for filename, data in files.items():
            path = root / name / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)


def test_checks_are_read_only_and_detect_same_size_tampering(tmp_path, tiny_models, monkeypatch):
    missing = tmp_path / "missing"
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **k: pytest.fail("network"))
    assert not provision.check_models(missing)["ready"]
    assert not missing.exists()
    populate(tmp_path, tiny_models)
    assert provision.check_models(tmp_path)["ready"]
    (tmp_path / "embed/config.json").write_bytes(b"wrong")
    result = provision.check_models(tmp_path)
    assert not result["ready"] and result["models"][1]["ready"]


def test_explicit_environment_required_offline_and_never_mutated(
    tmp_path, tiny_models, monkeypatch
):
    monkeypatch.delenv("COLONY_EMBED_MODEL_DIR", raising=False)
    monkeypatch.delenv("COLONY_RANK_MODEL_DIR", raising=False)
    monkeypatch.setenv("COLONY_MODEL_CACHE", str(tmp_path))
    populate(tmp_path, tiny_models)
    assert provision.model_environment(None) == {}
    with pytest.raises(provision.ModelPreparationError, match="Verified local models"):
        provision.model_environment(None, offline=True)
    before = dict(os.environ)
    settings = provision.model_environment(tmp_path, offline=True)
    assert settings["COLONY_OFFLINE"] == "1" and settings["HF_HUB_OFFLINE"] == "1"
    assert dict(os.environ) == before
    for name in tiny_models:
        monkeypatch.setenv(f"COLONY_{name.upper()}_MODEL_DIR", str(tmp_path / name))
    assert provision.model_environment(None, offline=True) == settings


def test_offline_retrieval_checks_before_instantiation(monkeypatch):
    monkeypatch.setenv("COLONY_OFFLINE", "1")
    monkeypatch.delenv("COLONY_EMBED_MODEL_DIR", raising=False)
    monkeypatch.delenv("COLONY_RANK_MODEL_DIR", raising=False)
    monkeypatch.setattr(retrieval, "TextEmbedding", lambda **kw: pytest.fail("model instantiated"))
    retrieval.models.cache_clear()
    with pytest.raises(ValueError, match="Verified local models"):
        retrieval.models()


def test_offline_passes_local_only_and_legacy_options_unchanged(tmp_path, tiny_models, monkeypatch):
    populate(tmp_path, tiny_models)
    calls = []
    for name in tiny_models:
        monkeypatch.setenv(f"COLONY_{name.upper()}_MODEL_DIR", str(tmp_path / name))
    monkeypatch.setattr(retrieval, "TextEmbedding", lambda **kw: calls.append(kw))
    monkeypatch.setattr(retrieval, "TextCrossEncoder", lambda **kw: calls.append(kw))
    monkeypatch.setenv("COLONY_OFFLINE", "1")
    retrieval.models.cache_clear()
    retrieval.models()
    assert all(call["local_files_only"] is True for call in calls)
    calls.clear()
    monkeypatch.delenv("COLONY_OFFLINE")
    retrieval.models.cache_clear()
    retrieval.models()
    assert all("local_files_only" not in call for call in calls)
    retrieval.models.cache_clear()


def test_explicit_fetch_resumes_and_reruns_preserve_other_models(
    tmp_path, tiny_models, monkeypatch
):
    root = tmp_path / "dedicated"
    downloaded = []
    failed = False

    def download(url, path, expected):
        nonlocal failed
        downloaded.append(url)
        if "/rank/" in url and not failed:
            failed = True
            path.write_bytes(b"partial")
            raise OSError("secret: provider response")
        for name, files in tiny_models.items():
            if "/" + name + "/" in url:
                filename = url.split("a" * 40 + "/")[1]
                path.write_bytes(files[filename])

    monkeypatch.setattr(provision, "_download", download)
    with pytest.raises(ValueError, match="resume owned partial") as error:
        provision.fetch_models(root)
    assert "secret" not in str(error.value)
    assert (root / "embed").is_dir() and not (root / "rank").exists()
    (root / "another-model").mkdir()
    (root / "another-model/keep").write_bytes(b"untouched")
    assert provision.fetch_models(root)["ready"]
    count = len(downloaded)
    assert provision.fetch_models(root)["ready"] and len(downloaded) == count
    assert (root / "another-model/keep").read_bytes() == b"untouched"


def test_refuses_arbitrary_existing_root_and_completed_tampering(
    tmp_path, tiny_models, monkeypatch
):
    (tmp_path / "unrelated").write_bytes(b"keep")
    monkeypatch.setattr(provision, "_download", lambda *a: pytest.fail("network"))
    with pytest.raises(ValueError, match="empty root"):
        provision.fetch_models(tmp_path)
    assert (tmp_path / "unrelated").read_bytes() == b"keep"
    owned = tmp_path / "owned"
    populate(owned, tiny_models)
    (owned / provision._ROOT_MARKER).write_bytes(provision._ROOT_BYTES)
    for name in tiny_models:
        (owned / name / provision._OWNER_MARKER).write_bytes(provision._owner(name))
        (owned / name / provision._COMPLETE_MARKER).write_bytes(provision._owner(name))
    (owned / "rank/config.json").write_bytes(b"evil")
    with pytest.raises(ValueError, match="artifacts changed"):
        provision.fetch_models(owned)


def test_download_disables_implicit_credentials_and_rejects_bad_size(tmp_path, monkeypatch):
    options = []
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"too long"))
    original = httpx.Client

    def client(**kwargs):
        options.append(kwargs)
        return original(transport=transport, **kwargs)

    monkeypatch.setattr(provision.httpx, "Client", client)
    with pytest.raises(ValueError, match="pinned size"):
        provision._download(
            "https://huggingface.co/test/model", tmp_path / "partial", (2, "0" * 64)
        )
    assert options == [{"trust_env": False, "follow_redirects": False, "timeout": 60}]


def test_download_rejects_credential_redirect_without_request(tmp_path, monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://user:secret@evil.test/model"})

    original = httpx.Client
    monkeypatch.setattr(
        provision.httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs),
    )
    with pytest.raises(ValueError, match="Untrusted"):
        provision._download(
            "https://huggingface.co/test/model", tmp_path / "partial", (1, "0" * 64)
        )
    assert len(requests) == 1 and "authorization" not in requests[0].headers


@pytest.mark.parametrize("kind", ["symlink", "junction"])
def test_linked_roots_are_refused(tmp_path, tiny_models, kind):
    real = tmp_path / "real"
    populate(real, tiny_models)
    linked = tmp_path / "linked"
    if kind == "junction":
        if os.name != "nt":
            pytest.skip("Windows junction test")
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(linked), str(real)], capture_output=True
        )
        assert result.returncode == 0
    else:
        try:
            linked.symlink_to(real, target_is_directory=True)
        except OSError:
            pytest.skip("Symlink permission unavailable")
    assert not provision.check_models(linked)["ready"]
    with pytest.raises(ValueError, match="without links"):
        provision.fetch_models(linked)


def test_hardlinked_artifact_rejected(tmp_path, tiny_models):
    populate(tmp_path, tiny_models)
    path = tmp_path / "embed/config.json"
    os.link(path, tmp_path / "alias")
    assert not provision.check_models(tmp_path)["ready"]


def test_unexpected_value_error_is_normalized(tmp_path, tiny_models, monkeypatch):
    def fail(*args):
        raise ValueError("secret provider URL and response")

    monkeypatch.setattr(provision, "_download", fail)
    with pytest.raises(provision.ModelPreparationError, match="resume owned partial") as error:
        provision.fetch_models(tmp_path / "models")
    assert "secret" not in str(error.value)


def test_partial_artifact_hardlink_refused_without_modification(tmp_path, tiny_models, monkeypatch):
    root = tmp_path / "models"
    root.mkdir()
    (root / provision._ROOT_MARKER).write_bytes(provision._ROOT_BYTES)
    partial = root / ".embed.partial"
    partial.mkdir()
    (partial / provision._OWNER_MARKER).write_bytes(provision._owner("embed"))
    outside = tmp_path / "unrelated"
    outside.write_bytes(b"do not modify")
    os.link(outside, partial / "config.json")
    monkeypatch.setattr(provision, "_download", lambda *args: pytest.fail("network"))
    with pytest.raises(ValueError, match="without links"):
        provision.fetch_models(root)
    assert outside.read_bytes() == b"do not modify"


def test_manifest_pins_actual_existing_model_identities():
    assert provision.MANIFEST["embed"]["repo"] == "qdrant/bge-small-en-v1.5-onnx-q"
    assert provision.MANIFEST["embed"]["revision"] == retrieval.EMBED_REVISION
    assert provision.MANIFEST["rank"]["repo"] == retrieval.RANK_MODEL
    assert provision.MANIFEST["rank"]["revision"] == retrieval.RANK_REVISION
    assert provision.MANIFEST["embed"]["files"]["model_optimized.onnx"][0] == 66465124
    assert provision.MANIFEST["rank"]["files"]["onnx/model.onnx"][0] == 90992115
