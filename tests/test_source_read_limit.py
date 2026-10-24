"""Enforce the evidence byte limit on the actual read, including file growth."""

import hashlib
from pathlib import Path

import pytest

import colony.context as context
import colony.validation as validation
from colony.models import Source


def source_for(data):
    return Source(ref="policy.txt", content_hash=hashlib.sha256(data).hexdigest())


def grow_after_check(monkeypatch, root, data):
    original = validation.repo_file

    def checked_path(root, ref):
        path = original(root, ref)
        path.write_bytes(data)
        return path

    monkeypatch.setattr(validation, "repo_file", checked_path)
    # The old context implementation imports this function directly.
    if hasattr(context, "repo_file"):
        monkeypatch.setattr(context, "repo_file", checked_path)
    (root / "policy.txt").write_bytes(b"small")


@pytest.mark.parametrize("operation", ["snapshot", "validate", "context", "strict_context"])
def test_growth_after_preliminary_check_cannot_bypass_limit(tmp_path, monkeypatch, operation):
    oversized = b"x" * (validation.MAX_SOURCE_BYTES + 1)
    source = source_for(oversized)  # Even the complete matching hash cannot admit it.
    grow_after_check(monkeypatch, tmp_path, oversized)
    if operation == "snapshot":
        with pytest.raises(ValueError, match="exceeds 2 MB"):
            validation.snapshot(tmp_path, source.ref)
    elif operation == "validate":
        status, checks = validation.validate(tmp_path, [source])
        assert status == "stale" and checks[0]["result"] == "stale"
    elif operation == "context":
        assert context.source_lines(tmp_path, source) == []
    else:
        with pytest.raises(context.SourceChanged, match="Evidence unavailable"):
            context.source_lines(tmp_path, source, strict=True)


@pytest.mark.parametrize("size", [0, validation.MAX_SOURCE_BYTES - 1, validation.MAX_SOURCE_BYTES])
def test_stable_source_at_or_below_limit_is_not_truncated(tmp_path, size):
    data = b"x" * size
    (tmp_path / "policy.txt").write_bytes(data)
    source = validation.snapshot(tmp_path, "policy.txt")
    assert source.content_hash == hashlib.sha256(data).hexdigest()
    assert validation.validate(tmp_path, [source])[0] == "validated"
    assert context.source_lines(tmp_path, source, strict=True) == ([data.decode()] if data else [])


def test_stable_oversized_file_stays_rejected(tmp_path):
    data = b"x" * (validation.MAX_SOURCE_BYTES + 1)
    (tmp_path / "policy.txt").write_bytes(data)
    source = source_for(data)
    with pytest.raises(ValueError, match="exceeds 2 MB"):
        validation.snapshot(tmp_path, source.ref)
    assert validation.validate(tmp_path, [source])[0] == "stale"
    assert context.source_lines(tmp_path, source) == []
    with pytest.raises(context.SourceChanged):
        context.source_lines(tmp_path, source, strict=True)


@pytest.mark.parametrize("operation", ["snapshot", "validate", "context"])
def test_actual_repository_read_is_bounded(tmp_path, monkeypatch, operation):
    data = b"Archive retention is seventeen days."
    (tmp_path / "policy.txt").write_bytes(data)
    source = source_for(data)
    original = Path.open
    requests = []

    class Reader:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def read(self, size=-1):
            requests.append(size)
            return self.stream.read(size)

    def tracked_open(path, *args, **kwargs):
        stream = original(path, *args, **kwargs)
        mode = args[0] if args else kwargs.get("mode", "r")
        if path == tmp_path / "policy.txt" and mode == "rb":
            return Reader(stream)
        return stream

    monkeypatch.setattr(Path, "open", tracked_open)
    if operation == "snapshot":
        validation.snapshot(tmp_path, source.ref)
    elif operation == "validate":
        assert validation.validate(tmp_path, [source])[0] == "validated"
    else:
        assert context.source_lines(tmp_path, source) == [data.decode()]
    assert requests == [validation.MAX_SOURCE_BYTES + 1]
