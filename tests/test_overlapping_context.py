"""Keep separately selected facts when only their context margins overlap."""

import pytest

from colony.context import SourceChanged, excerpts
from colony.validation import snapshot


def recipe(tmp_path, *, second="Only after export approval.", padding=0):
    lines = [f"Background note {i}." for i in range(1, 41)]
    for i in (7, 9, 11, 13, 28, 30):
        lines[i] = ""
    lines[8] = "Archives expire after 23 days."
    lines[12] = second
    lines[29] = "The separate ledger must retain its audit receipt."
    lines[14] += "x" * padding
    (tmp_path / "policy.md").write_text("\n".join(lines), encoding="utf-8")
    source = snapshot(tmp_path, "policy.md")
    chunks = [("policy.md", n, lines[n - 1]) for n in (9, 13, 30)]
    return source, chunks


def view(tmp_path, source, chunks, **kwargs):
    return excerpts(tmp_path, [source], "When may archives expire?",
                    preferred=[(r, n) for r, n, _ in chunks], candidate_chunks=chunks, **kwargs)


@pytest.mark.parametrize("condition", [
    "Only after export approval.",
    "Expiry is blocked until the remote ledger acknowledges delivery.",
    "This is a demonstration; production retention remains 91 days.",
])
def test_overlapping_selected_condition_survives_without_displacing_other_fact(tmp_path, condition):
    source, chunks = recipe(tmp_path, second=condition)
    baseline = view(tmp_path, source, [chunks[0], chunks[2]])
    actual = view(tmp_path, source, chunks)
    assert len(actual) == len(baseline) == 2
    assert actual[1] == baseline[1]
    assert baseline[0]["text"] in actual[0]["text"]
    assert condition in actual[0]["text"]
    assert sum(len(p["text"]) for p in actual) <= 2800
    assert all(len(p["text"]) <= 2400 and not p["complete_file"] for p in actual)
    assert actual[0]["line"] == baseline[0]["line"]


def test_overbudget_overlap_does_not_evict_existing_source_bytes(tmp_path):
    source, chunks = recipe(tmp_path, padding=2400)
    baseline = view(tmp_path, source, [chunks[0], chunks[2]])
    assert view(tmp_path, source, chunks) == baseline


def test_fully_contained_duplicate_does_not_change_context(tmp_path):
    source, chunks = recipe(tmp_path)
    baseline = view(tmp_path, source, [chunks[0], chunks[2]])
    inside = chunks[0]
    assert view(tmp_path, source, [chunks[0], inside, chunks[2]]) == baseline


def test_total_budget_is_reserved_for_independent_baseline_passage(tmp_path):
    source, chunks = recipe(tmp_path, padding=900)
    path = tmp_path / "policy.md"
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[29] += "y" * 2000
    path.write_text("\n".join(lines), encoding="utf-8")
    source = snapshot(tmp_path, "policy.md")
    chunks[2] = ("policy.md", 30, lines[29])
    baseline = view(tmp_path, source, [chunks[0], chunks[2]])
    assert len(baseline) == 2
    assert 2800 - sum(len(p["text"]) for p in baseline) < 900
    assert view(tmp_path, source, chunks) == baseline


def test_same_line_numbers_in_other_file_do_not_merge(tmp_path):
    source, chunks = recipe(tmp_path)
    other = tmp_path / "other.md"
    lines = ["Unrelated attribute; never an archive policy."] * 40
    lines[11] = ""
    other.write_text("\n".join(lines), encoding="utf-8")
    sources = [source, snapshot(tmp_path, "other.md")]
    preferred = [("policy.md", 9), ("other.md", 13)]
    actual = excerpts(tmp_path, sources, "Archive retention?", preferred=preferred)
    assert len(actual) == 2
    assert actual[0]["ref"] == "policy.md" and actual[1]["ref"] == "other.md"
    assert "Unrelated attribute" not in actual[0]["text"]


def test_changed_source_is_never_used_to_fill_overlap(tmp_path):
    source, chunks = recipe(tmp_path)
    (tmp_path / "policy.md").write_text("Replacement policy.", encoding="utf-8")
    assert view(tmp_path, source, chunks) == []
    with pytest.raises(SourceChanged):
        view(tmp_path, source, chunks, strict_sources=True)
