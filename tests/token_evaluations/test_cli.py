"""Provider-free command boundaries and blinded-review binding controls."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from scripts.token_evaluation import cli


def write(path, value):
    path.write_bytes(json.dumps(value).encode())


def packet(tmp_path):
    value = {"case": "random", "answer": "current fact", "protocol_sha256": "protocol",
             "answer_sha256": hashlib.sha256(b"current fact").hexdigest(),
             "source_sha256": "source", "citation_sha256": "citation", "label_sha256": "label"}
    write(tmp_path / "answers.json", [value])
    write(tmp_path / "private-map.json", [{"id": "work-task-colony", **{k: v for k, v in value.items()
        if k not in {"answer", "protocol_sha256"}}}])
    write(tmp_path / "review.json", {"reviewer": "independent", "method": "arm-blind source review",
        "labels": [{"case": "random", "correct": True, "grounded": True, "format_compliant": True}]})
    return tmp_path


def test_bind_review_without_exposing_arm_to_grader(tmp_path):
    root = packet(tmp_path)
    output = root / "quality.json"
    cli.bind_quality(root, root / "review.json", output)
    value = json.loads(output.read_text())
    assert value["labels"][0]["id"] == "work-task-colony"
    assert value["labels"][0]["correct"] is True
    assert "colony" not in (root / "answers.json").read_text()


@pytest.mark.parametrize("corruption", ["changed_answer", "missing_label", "unknown", "duplicate", "truthy"])
def test_bind_rejects_corrupt_or_incomplete_review(tmp_path, corruption):
    root = packet(tmp_path)
    review = json.loads((root / "review.json").read_text())
    if corruption == "changed_answer":
        answers = json.loads((root / "answers.json").read_text())
        answers[0]["answer"] = "altered"
        write(root / "answers.json", answers)
    elif corruption == "missing_label":
        review["labels"][0].pop("grounded")
    elif corruption == "unknown":
        review["labels"][0]["case"] = "unknown"
    elif corruption == "duplicate":
        review["labels"] *= 2
    else:
        review["labels"][0]["correct"] = 1
    write(root / "review.json", review)
    with pytest.raises(ValueError):
        cli.bind_quality(root, root / "review.json", root / "quality.json")
    assert not (root / "quality.json").exists()


def test_quality_template_is_explicitly_ungraded_and_no_overwrite(tmp_path):
    root = packet(tmp_path)
    output = root / "quality.json"
    cli.quality_template(root, output)
    value = json.loads(output.read_text())
    assert value["labels"][0]["correct"] is None
    assert value["reviewer"] == ""
    with pytest.raises(FileExistsError):
        cli.quality_template(root, output)


def test_bind_rejects_changed_grading_source_or_oracle(tmp_path):
    root = packet(tmp_path)
    answer = json.loads((root / "answers.json").read_text())[0]
    answer.update(current_sources=[{"path": "source.py", "sha256": hashlib.sha256(b"source").hexdigest(),
                                    "content": "source"}], snapshot="v1", oracle={}, rubric={})
    answer["source_sha256"] = cli.digest({"snapshot": "v1", "files": {"source.py": answer["current_sources"][0]["sha256"]}})
    answer["citation_sha256"] = answer["label_sha256"] = cli.digest({})
    write(root / "answers.json", [answer])
    write(root / "private-map.json", [{"id": "work-task-colony", **{k: answer[k] for k in
        ("case", "answer_sha256", "source_sha256", "citation_sha256", "label_sha256")}}])
    cli.bind_quality(root, root / "review.json", root / "good-quality.json")
    answer["current_sources"][0]["content"] = "wrong source"
    write(root / "answers.json", [answer])
    with pytest.raises(ValueError, match="grading evidence changed"):
        cli.bind_quality(root, root / "review.json", root / "bad-quality.json")


def test_run_cannot_be_selected_without_explicit_native_opt_in():
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["run", "--run", "dir", "--authorization", "auth", "--auth-home", "home"])


def test_template_rejects_installed_revision_mismatch_before_client(tmp_path, monkeypatch):
    catalog = tmp_path / "catalog.json"
    write(catalog, {"workloads": []})
    target = tmp_path / "python.exe"
    target.touch()
    args = SimpleNamespace(catalog=catalog, target_python=target, target_repo=tmp_path, revision="main")
    responses = iter([b"a" * 40 + b"\n", b"src/colony/__init__.py\n", b"clean git bytes"])
    monkeypatch.setattr(cli, "_git", lambda *a: next(responses))
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout='{"colony/__init__.py":"different"}'))
    with pytest.raises(ValueError, match="exactly match"):
        cli.template(args)


def test_help_is_provider_free(capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["--help"])
    assert caught.value.code == 0
    assert "preflight" in capsys.readouterr().out
