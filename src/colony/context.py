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
    """Keep the selected passage and nearby context, without expanding whole sections."""
    center = min(line - 1 + min(8, span // 2), len(lines) - 1)
    low, high = 0, len(lines)
    fences = []
    if Path(ref).suffix.lower() in {".md", ".markdown"}:
        headings, fenced, opening = [], False, None
        for i, text in enumerate(lines):
            if text.lstrip().startswith(("```", "~~~")):
                fenced = not fenced
                if fenced:
                    opening = i
                else:
                    fences.append((opening, i + 1))
            elif not fenced and re.match(r"^#{1,6} ", text):
                headings.append(i)
        low = max((i for i in headings if i <= center), default=0)
        high = min((i for i in headings if i > center), default=len(lines))
    section_low, section_high = low, high
    # Short sections and heading-only selections need their section context.
    # For longer sections, retain the actual selected span with a small margin;
    # unrelated setup examples should not be repeated on every memory lookup.
    if line - 1 > low and high - low > span + 6:
        low = max(low, line - 3)
        high = min(high, line - 1 + span + 2)
        for begin, end in fences:
            # Do not return a closing fence and omit the commands it encloses.
            # Also retain a code block directly described by the selected prose.
            if (begin < high and end > low) or 0 <= line - 1 - end <= 2:
                expanded_low, expanded_high = min(low, begin), max(high, end)
                if len("\n".join(lines[expanded_low:expanded_high])) <= 2400:
                    low, high = expanded_low, expanded_high
        # Adjacent fenced blocks separated by brief instructions form a recipe:
        # an invocation example may require the setup block immediately before it.
        # Never cross a section heading or the existing excerpt budget.
        if any(begin < high and end > low for begin, end in fences):
            for begin, end in reversed(fences):
                if (
                    section_low <= begin < end <= section_high
                    and 0 <= low - end <= 4
                    and len("\n".join(lines[begin:high])) <= 2400
                ):
                    low = begin
    if len("\n".join(lines[low:high])) > 2400:
        low = max(low, line - 1 - 8)
        high = min(high, low + 32)
    return low + 1, "\n".join(lines[low:high])[:2400]


def excerpts(
    root: Path,
    sources: list[Source],
    question: str,
    *,
    preferred: list[tuple[str, int]] | None = None,
    candidate_chunks: list[tuple[str, int, str]] | None = None,
    claims: str | None = None,
    whole_files: bool = False,
    section_context: bool = False,
) -> list[dict]:
    unique = {s.ref: s for s in sources}
    if whole_files and 1 <= len(unique) <= 4:
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
    # A single-file evidence lookup can forward its already computed paragraph
    # descriptors. The final display below still rereads and fingerprints source
    # bytes. Other callers, including all multi-source lookups, build normally.
    candidates = (
        candidate_chunks
        if preferred is not None
        and candidate_chunks is not None
        and len(sources) == 1
        and sources[0].kind == "file"
        and len(candidate_chunks) <= 96
        and all(chunk[0] == sources[0].ref for chunk in candidate_chunks)
        else source_chunks(root, sources)
    )
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
        if claims is None:
            # With one eligible excerpt there is no ordering or selection decision
            # left: its score is always the best, regardless of the model logit.
            scores = (
                rank(question, [c[2] for c in candidates])
                if len(candidates) > 1
                else [0.0] * len(candidates)
            )
        else:
            query_coverage = keyword_coverage(question, [c[2] for c in candidates])
            # Concrete code/commands identify the claim better than background
            # prose such as model-download notes in the same saved answer.
            code_claims = re.findall(r"`([^`\n]+)`", claims[:4000])
            claim_coverage = keyword_coverage(
                "\n".join(code_claims) if code_claims else claims[:4000], [c[2] for c in candidates]
            )
            lexical = [
                0.2 * q + 0.7 * a for q, a in zip(query_coverage, claim_coverage, strict=True)
            ]
            # The existing reranker contribution lies between 0 and 0.1.
            # A paragraph whose upper bound misses even the best lower bound's
            # 0.15 selection window cannot appear in the response. Compute IDF
            # over the full set first, and preserve original order for ties.
            floor = max(lexical, default=0) - 0.15
            eligible = [i for i, score in enumerate(lexical) if score + 0.1 >= floor - 1e-12]
            candidates = [candidates[i] for i in eligible]
            # The sole survivor is selected at every possible model score.
            scores = (
                rank(question, [c[2] for c in candidates])
                if len(candidates) > 1
                else [0.0] * len(candidates)
            )
            scores = [
                lexical[i] + 0.5 * fused_score(0, s) for i, s in zip(eligible, scores, strict=True)
            ]
    selected, occupied, cache, budget = [], [], {}, 2800
    best = max(scores, default=0)
    by_ref = {s.ref: s for s in sources}
    order = sorted(range(len(scores)), key=scores.__getitem__, reverse=True)
    for index in order:
        ref, line, core = candidates[index]
        if scores[index] < best - (0.15 if claims is not None else 2):
            continue
        if ref not in cache:
            cache[ref] = source_lines(root, by_ref[ref])
        if not cache[ref]:
            continue
        if len(selected) >= 2 or budget < 300:
            break
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
    if section_context and len(selected) == 1 and budget >= 300:
        # An isolated detail paragraph can omit definitions or qualifications
        # introduced at the start of its section. Use an otherwise empty second
        # excerpt slot for that context, without evicting another ranked passage
        # or increasing the existing text/excerpt budgets. No new file is read.
        item = selected[0]
        ref, line = item["ref"], item["line"]
        if Path(ref).suffix.lower() in {".md", ".markdown"}:
            heading, fenced = None, False
            for i, text in enumerate(cache[ref][: line - 1]):
                if text.lstrip().startswith(("```", "~~~")):
                    fenced = not fenced
                elif not fenced and re.match(r"^#{1,6} ", text):
                    heading = i + 1
            if heading is not None:
                start, text = context_window(ref, cache[ref], heading, 6)
                if text and len(text) <= budget and start + text.count("\n") < line:
                    selected.insert(
                        0, {"ref": ref, "line": start, "text": text, "complete_file": False}
                    )
    return selected
