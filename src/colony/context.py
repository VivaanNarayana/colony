"""Bounded, scrubbed excerpts of a trail's current, fingerprinted evidence."""

import hashlib
import re
from pathlib import Path

from colony.models import Source
from colony.retrieval import fused_score, keyword_coverage, rank
from colony.security import PATTERNS
from colony.validation import repo_file


def source_lines(root: Path, source: Source) -> list[str]:
    if source.kind != "file":
        return []
    try:
        data = repo_file(root, source.ref).read_bytes()
        if hashlib.sha256(data).hexdigest() != source.content_hash:
            return []
        text = data.decode("utf-8")
    except (OSError, ValueError, UnicodeError):
        return []
    # Scrub before chunking; multiline secrets cannot straddle excerpt boundaries.
    # Preserve original line numbers even when replacing a private key block.
    for pattern in PATTERNS:
        text = re.sub(pattern, lambda m: "[REDACTED]" + "\n" * m[0].count("\n"), text)
    return text.splitlines()


def source_chunks(root: Path, sources: list[Source]) -> list[tuple[str, int, str]]:
    candidates = []
    # Only explicitly attached, matching files supply context. Never crawl uncited files.
    for source in sources[:8]:
        lines = source_lines(root, source)[:2000]
        headings, stack, fenced = {}, [], False
        if Path(source.ref).suffix.lower() in {".md", ".markdown"}:
            for i, text in enumerate(lines):
                if text.lstrip().startswith(("```", "~~~")):
                    fenced = not fenced
                elif not fenced and (match := re.match(r"^(#{1,6}) (.+)", text)):
                    level = len(match[1])
                    stack = [(depth, title) for depth, title in stack if depth < level]
                    stack.append((level, match[2]))
                headings[i] = " > ".join(title for _, title in stack)
        start = None
        for end, text in enumerate([*lines, ""]):
            if text.strip():
                if start is None:
                    start = end
                continue
            if start is None:
                continue
            # Paragraphs keep independent topics apart. Long code/prose paragraphs
            # still use bounded overlapping windows.
            starts = [start] if end - start <= 16 else range(start, end, 8)
            for begin in starts:
                block = "\n".join(lines[begin : min(end, begin + 16)])
                # Rank paragraphs with their section meaning, including short code
                # blocks. Example question arguments must not win by echoing a query.
                # Display still rereads untouched source lines, preserving citations.
                block = re.sub(
                    r'(--question\s+)(["\'])([^\n]*?)\2',
                    r"\1<question>",
                    block,
                )
                title = headings.get(begin, "")
                indexed = f"Section: {title}\n{block}" if title else block
                candidates.append((source.ref, begin + 1, indexed[:2400]))
                if len(candidates) >= 96:
                    break
            start = None
            if len(candidates) >= 96:
                break
        if len(candidates) >= 96:
            break
    return candidates


def context_window(ref: str, lines: list[str], line: int, span: int) -> tuple[int, str]:
    """Retrieve narrowly; display the containing section or surrounding source lines."""
    center = min(line - 1 + min(8, span // 2), len(lines) - 1)
    low, high = 0, len(lines)
    if Path(ref).suffix.lower() in {".md", ".markdown"}:
        headings, fenced = [], False
        for i, text in enumerate(lines):
            if text.lstrip().startswith(("```", "~~~")):
                fenced = not fenced
            elif not fenced and re.match(r"^#{1,6} ", text):
                headings.append(i)
        low = max((i for i in headings if i <= center), default=0)
        high = min((i for i in headings if i > center), default=len(lines))
    if len("\n".join(lines[low:high])) > 2400:
        low = max(low, line - 1 - 8)
        high = min(high, low + 32)
    return low + 1, "\n".join(lines[low:high])[:2400]


def _selected_block(lines: list[str], line: int) -> tuple[int, int]:
    """Recover the original paragraph, or the entire enclosing fenced block."""
    index = min(line - 1, len(lines) - 1)
    opening = None
    for i, text in enumerate(lines):
        # Conservatively retain fences nested in lists or blockquotes, including
        # indented continuation lines. The returned text remains untouched source.
        match = re.match(r"^([ \t]*(?:(?:>|[-+*]|[0-9]{1,9}[.)])[ \t]*)*)(`{3,}|~{3,})", text)
        if match:
            prefix, marker = match[1], match[2]
            if opening is None:
                opening = (i, marker[0], len(marker), prefix.count(">"))
            elif (
                marker[0] == opening[1]
                and len(marker) >= opening[2]
                and prefix.count(">") == opening[3]
                and re.fullmatch(r"[ \t>]*", prefix)
                and not text[match.end() :].strip()
            ):
                if opening[0] <= index <= i:
                    return opening[0] + 1, i + 1
                opening = None
        if i == index and opening is None:
            break
    if opening is not None and opening[0] <= index:
        return opening[0] + 1, len(lines)
    low, high = index, index + 1
    while low > 0 and lines[low - 1].strip():
        low -= 1
    while high < len(lines) and lines[high].strip():
        high += 1
    return low + 1, high


def _adaptive_excerpt(ref, lines, line, whole_files):
    first, last = _selected_block(lines, line)
    low, high = first, last
    complete = whole_files and len("\n".join(lines)) <= 16000
    if complete:
        low, high = 1, len(lines)
    elif Path(ref).suffix.lower() in {".md", ".markdown"}:
        # Optional context is a complete containing section, never a sliced line.
        headings, fenced = [], False
        for i, text in enumerate(lines):
            if text.lstrip().startswith(("```", "~~~")):
                fenced = not fenced
            elif not fenced and re.match(r"^#{1,6} ", text):
                headings.append(i + 1)
        begin = max((i for i in headings if i <= first), default=1)
        end = min((i - 1 for i in headings if i > last), default=len(lines))
        if len("\n".join(lines[begin - 1 : end])) <= 2400:
            low, high = begin, end
    return {
        "ref": ref,
        "line": low,
        "text": "\n".join(lines[low - 1 : high]),
        "complete_file": complete,
        "selected_line": first,
        "selected_end_line": last,
    }


def excerpts(
    root: Path,
    sources: list[Source],
    question: str,
    *,
    preferred: list[tuple[str, int]] | None = None,
    claims: str | None = None,
    whole_files: bool = False,
    adaptive: bool = False,
) -> list[dict]:
    unique = {s.ref: s for s in sources}
    if not adaptive and whole_files and 1 <= len(unique) <= 4:
        files = []
        for source in unique.values():
            lines = source_lines(root, source)
            text = "\n".join(lines)
            if not lines or len(text) > 16000:
                break
            files.append({"ref": source.ref, "line": 1, "text": text, "complete_file": True})
        else:
            # Complete small, redacted files remove the need to reread an identical
            # file through another tool. Large/many files retain focused excerpts.
            if sum(len(f["text"]) for f in files) <= 24000:
                return files
    candidates = source_chunks(root, sources)
    if preferred is not None:
        # A second reranking must not replace the globally selected passage
        # with unrelated text from the same file.
        order = {key: i for i, key in enumerate(preferred)}
        candidates = sorted(
            [c for c in candidates if (c[0], c[1]) in order],
            key=lambda c: order[(c[0], c[1])],
        )
        scores = [0.0] * len(candidates)
    else:
        scores = rank(question, [c[2] for c in candidates])
        if claims is not None:
            query_coverage = keyword_coverage(question, [c[2] for c in candidates])
            # Concrete code/commands identify the claim better than background
            # prose such as model-download notes in the same saved answer.
            code_claims = re.findall(r"`([^`\n]+)`", claims[:4000])
            claim_coverage = keyword_coverage(
                "\n".join(code_claims) if code_claims else claims[:4000], [c[2] for c in candidates]
            )
            scores = [
                0.2 * q + 0.7 * a + 0.5 * fused_score(0, s)
                for q, a, s in zip(query_coverage, claim_coverage, scores, strict=True)
            ]
    selected, occupied, cache, budget = [], [], {}, 2800
    best = max(scores, default=0)
    by_ref = {s.ref: s for s in sources}
    for index in sorted(range(len(scores)), key=scores.__getitem__, reverse=True):
        ref, line, core = candidates[index]
        if scores[index] < best - (0.15 if claims is not None else 2):
            continue
        if ref not in cache:
            cache[ref] = source_lines(root, by_ref[ref])
        if not cache[ref]:
            continue
        if len(selected) >= 2 or budget < 300:
            break
        if adaptive:
            item = _adaptive_excerpt(ref, cache[ref], line, whole_files)
            first, last = item["selected_line"], item["selected_end_line"]
            if any(r == ref and first <= b and last >= a for r, a, b in occupied):
                continue
            selected.append(item)
            occupied.append((ref, first, last))
            continue
        start, text = context_window(ref, cache[ref], line, core.count("\n") + 1)
        limit = min(2400, budget)
        offset = len("\n".join(cache[ref][start - 1 : line - 1])) + int(line > start)
        core_text = "\n".join(cache[ref][line - 1 : line + core.count("\n")])
        if start > line or offset + min(limit, len(core_text)) > limit:
            # Section expansion and the remaining response budget must never clip
            # away the selected paragraph. Anchor at its original source line.
            start = line
            text = "\n".join(cache[ref][line - 1 : line + 31])
        text = text[:limit]
        end = start + text.count("\n")
        if any(r == ref and start <= b and end >= a for r, a, b in occupied):
            continue
        selected.append({"ref": ref, "line": start, "text": text, "complete_file": False})
        occupied.append((ref, start, end))
        budget -= len(text)
    if adaptive and len(selected) == 2 and selected[0]["ref"] == selected[1]["ref"]:
        a, b = selected
        if a["line"] <= b["line"] + b["text"].count("\n") and b["line"] <= a["line"] + a[
            "text"
        ].count("\n"):
            # Optional section/file expansion must not hide two independent cores
            # behind one overlapping local read.
            for item in selected:
                first, last = item["selected_line"], item["selected_end_line"]
                item.update(
                    line=first,
                    text="\n".join(cache[item["ref"]][first - 1 : last]),
                    complete_file=False,
                )
    return selected
