"""Literal headings inside fenced examples must not become source sections."""

import uuid

import numpy as np
import pytest

import colony.store as storage
from colony.context import context_window, excerpts, source_chunks
from colony.models import Trail
from colony.retrieval import embed_passages, index_version
from colony.store import Store
from colony.validation import snapshot

FACT = "The plotter supports preview and render; neither operation changes stored drawings."


@pytest.mark.parametrize(
    "example",
    [
        "````markdown\n```python\n# Example heading\npreview()\n```\n````",
        "~~~markdown\n```python\n# Example heading\npreview()\n```\n~~~",
        "```text\n```example-literal\n# Example heading\npreview()\n```",
        "````text\n```\n# Example heading\npreview()\n`````",
        "   ~~~text\n```\n# Example heading\npreview()\n  ~~~~\t",
    ],
)
def test_nested_fence_examples_keep_real_section_and_contiguous_display(tmp_path, example):
    text = "# Manual\n\n## Operations\n\n" + example + "\n\n" + FACT + "\n"
    (tmp_path / "plotter.md").write_text(text, encoding="utf-8")
    source = snapshot(tmp_path, "plotter.md")
    lines = text.splitlines()
    line = lines.index(FACT) + 1
    chunks = source_chunks(tmp_path, [source], strict=True)
    selected = next(content for _, start, content in chunks if start == line)
    assert selected == "Section: Manual > Operations\n" + FACT
    start, window = context_window("plotter.md", lines, line, 1)
    assert example in window and FACT in window
    assert window == "\n".join(lines[start - 1 : start - 1 + len(window.splitlines())])
    shown = excerpts(
        tmp_path,
        [source],
        "Available plotter operations?",
        preferred=[("plotter.md", line)],
        section_context=True,
        strict_sources=True,
    )
    assert len(shown) == 1
    assert example in shown[0]["text"] and FACT in shown[0]["text"]
    assert shown[0]["line"] == 3 and not shown[0]["complete_file"]
    assert len(shown[0]["text"]) <= 2400


@pytest.mark.parametrize("prefix", ["    ```python\n    # Indented example", "\t~~~text"])
def test_indented_markers_do_not_hide_following_real_heading(tmp_path, prefix):
    text = "# Manual\n\n" + prefix + "\n\n## Operations\n\n" + FACT + "\n"
    (tmp_path / "plotter.md").write_text(text, encoding="utf-8")
    source = snapshot(tmp_path, "plotter.md")
    assert next(c for _, _, c in source_chunks(tmp_path, [source]) if FACT in c).startswith(
        "Section: Manual > Operations\n"
    )


@pytest.mark.parametrize("indent", ["", " ", "  ", "   "])
def test_supported_fence_indentation_info_and_longer_closer(tmp_path, indent):
    text = (
        "# Manual\n\n"
        + indent
        + "```python example\n# Literal heading\npreview()\n"
        + indent
        + "`````  \t\n\n## Operations\n\n"
        + FACT
        + "\n"
    )
    (tmp_path / "plotter.md").write_text(text, encoding="utf-8")
    source = snapshot(tmp_path, "plotter.md")
    chunks = source_chunks(tmp_path, [source])
    assert all("Section: Literal heading" not in c for _, _, c in chunks)
    assert next(c for _, _, c in chunks if FACT in c).startswith("Section: Manual > Operations\n")


def test_backtick_in_info_string_is_not_an_opening_fence(tmp_path):
    text = "# Manual\n\n```not `a` fence\n\n## Operations\n\n" + FACT + "\n"
    (tmp_path / "plotter.md").write_text(text, encoding="utf-8")
    source = snapshot(tmp_path, "plotter.md")
    assert next(c for _, _, c in source_chunks(tmp_path, [source]) if FACT in c).startswith(
        "Section: Manual > Operations\n"
    )


def test_unclosed_fence_suppresses_example_heading_without_expanding_whole_block(tmp_path):
    text = (
        "# Manual\n\n## Operations\n\n````text\n```\n# Literal example heading\n\n"
        + "Example filler line.\n" * 100
        + "\n"
        + FACT
        + "\n"
    )
    (tmp_path / "plotter.md").write_text(text, encoding="utf-8")
    source = snapshot(tmp_path, "plotter.md")
    line = text.splitlines().index(FACT) + 1
    chunks = source_chunks(tmp_path, [source])
    selected = next(c for _, start, c in chunks if start == line)
    assert selected.startswith("Section: Manual > Operations\n")
    shown = excerpts(tmp_path, [source], "Plotter?", preferred=[("plotter.md", line)])
    assert FACT in shown[0]["text"]
    assert "````text" not in shown[0]["text"]
    assert sum(len(item["text"]) for item in shown) <= 2800


@pytest.mark.parametrize("fail_rebuild", [False, True])
def test_legacy_fence_index_migrates_atomically_with_original_trail_age(
    team, monkeypatch, fail_rebuild
):
    app, _, repo, _, key = team
    store = app.state.store
    text = (
        "# Manual\n\n## Operations\n\n````markdown\n```python\n# Example heading\n"
        "preview()\n```\n````\n\n" + FACT + "\n"
    )
    (repo / "plotter.md").write_text(text, encoding="utf-8")
    line = text.splitlines().index(FACT) + 1
    source = snapshot(repo, "plotter.md")
    trail = Trail(
        id=uuid.uuid4().hex,
        namespace="team",
        question="Available plotter operations?",
        content=FACT,
        sources=[source],
    )
    # Reproduce a legacy stored descriptor from the independently observed bug.
    with monkeypatch.context() as legacy:
        legacy.setattr(
            storage,
            "source_chunks",
            lambda root, sources, **options: [("plotter.md", line, "Section: Example heading\n" + FACT)],
        )
        store.push(trail, key.grant)
    legacy_version = "semantic-v7:legacy-fence-descriptor"
    with store.connect() as db:
        db.execute("UPDATE settings SET value=? WHERE key='index_version'", (legacy_version,))

    def state():
        with store.connect() as db:
            return {
                table: [tuple(row) for row in db.execute(f"SELECT * FROM {table}")]
                for table in [
                    "settings",
                    "trails",
                    "trail_sources",
                    "trail_vectors",
                    "trail_questions",
                    "source_chunks",
                    "source_vectors",
                    "source_index_state",
                ]
            }

    before = state()
    if fail_rebuild:
        def fail(texts):
            raise RuntimeError("Interrupted source vector rebuild")

        monkeypatch.setattr(storage, "embed_passages", fail)
        with pytest.raises(RuntimeError, match="Interrupted source vector rebuild"):
            Store(store.directory, repo)
        assert state() == before
        return

    migrated = Store(store.directory, repo)
    after = state()
    assert after["trails"] == before["trails"]  # Includes original payload, age and taint.
    assert after["trail_sources"] == before["trail_sources"]
    assert dict(after["settings"])["index_version"] == index_version()
    assert after["source_index_state"] == [(trail.id, index_version())]
    descriptors = source_chunks(repo, [source])
    assert next(c for _, start, c in descriptors if start == line).startswith(
        "Section: Manual > Operations\n"
    )
    expected = embed_passages([f"{ref}\n{content}" for ref, _, content in descriptors])
    with migrated.connect() as db:
        actual = db.execute(
            "SELECT c.ref,c.line,v.embedding FROM source_chunks c "
            "JOIN source_vectors v ON c.id=v.rowid ORDER BY c.id"
        ).fetchall()
    assert [(row["ref"], row["line"]) for row in actual] == [
        (ref, start) for ref, start, _ in descriptors
    ]
    for row, vector in zip(actual, expected, strict=True):
        np.testing.assert_allclose(
            np.frombuffer(row["embedding"], np.float32), np.frombuffer(vector, np.float32), atol=1e-5
        )
