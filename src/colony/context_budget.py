"""Offline accounting and indivisible source-passage packing for opt-in responses."""

import base64
import copy
import hashlib
import json
import os
from functools import lru_cache
from importlib.metadata import version
from pathlib import Path


class ContextBudgetError(ValueError):
    """A fixed, public configuration error, without file paths or source text."""


def serialize_response(response: dict) -> str:
    return json.dumps(
        response, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _budget(value):
    from colony.models import ContextBudget

    try:
        return value if isinstance(value, ContextBudget) else ContextBudget.model_validate(value)
    except (TypeError, ValueError):
        raise ContextBudgetError("Invalid context budget") from None


@lru_cache(maxsize=1)
def _encoding(data: bytes):
    import tiktoken

    ranks = {}
    for line in data.splitlines():
        token, rank = line.split()
        ranks[base64.b64decode(token, validate=True)] = int(rank)
    # The official o200k_base definition, constructed without its downloading loader.
    pattern = "|".join(
        [
            r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]*[\p{Ll}\p{Lm}\p{Lo}\p{M}]+(?i:'s|'t|'re|'ve|'m|'ll|'d)?",
            r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]+[\p{Ll}\p{Lm}\p{Lo}\p{M}]*(?i:'s|'t|'re|'ve|'m|'ll|'d)?",
            r"\p{N}{1,3}",
            r" ?[^\s\p{L}\p{N}]+[\r\n/]*",
            r"\s*[\r\n]+",
            r"\s+(?!\S)",
            r"\s+",
        ]
    )
    return tiktoken.Encoding(
        name="o200k_base",
        pat_str=pattern,
        mergeable_ranks=ranks,
        special_tokens={"<|endoftext|>": 199999, "<|endofprompt|>": 200018},
    )


def _counter(budget):
    if budget.encoding == "utf8_bytes":
        return lambda text: len(text.encode("utf-8"))
    try:
        parts = version("tiktoken").split(".")
        if tuple(map(int, parts[:2])) != (0, 14):
            raise ValueError()
        configured = os.environ.get("COLONY_CONTEXT_TOKENIZER", "")
        if not configured or "://" in configured or configured.startswith(("//", "\\\\")):
            raise ValueError()
        path = Path(configured)
        if not path.is_absolute():
            raise ValueError()
        path = path.resolve(strict=True)
        if str(path).startswith(("//", "\\\\")) or not path.is_file():
            raise ValueError()
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError()
        with path.open("rb") as stream:
            data = stream.read(8 * 1024 * 1024 + 1)
        if (
            hashlib.sha256(data).hexdigest()
            != "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"
        ):
            raise ValueError()
        encoding = _encoding(data)
        return lambda text: len(encoding.encode(text, disallowed_special=()))
    except (OSError, ValueError, TypeError, ImportError, LookupError):
        raise ContextBudgetError("Local context tokenizer is unavailable or invalid") from None


def supported_encodings() -> list[str]:
    result = ["utf8_bytes"]
    try:
        _counter(_budget({"encoding": "o200k_base"}))
    except ContextBudgetError:
        pass
    else:
        result.append("o200k_base")
    return result


def count_response(response: dict, budget) -> int:
    return _counter(_budget(budget))(serialize_response(response))


def _receipt(response, budget, counter, limited):
    response["context_budget"] = {
        **budget.model_dump(),
        "used": 0,
        "measurement": "utf8_bytes_estimate"
        if budget.encoding == "utf8_bytes"
        else "encoding_tokens",
        "limited": limited,
    }
    # The number's own decimal representation is part of the measured response.
    for _ in range(20):
        used = counter(serialize_response(response))
        if used == response["context_budget"]["used"]:
            return response
        response["context_budget"]["used"] = used
    raise ContextBudgetError("Context budget accounting did not converge")


def _core(item, reads):
    text_key, line_key = ("content", "start_line") if reads else ("text", "line")
    text, start = item.get(text_key), item.get(line_key)
    first, last = item.get("selected_line"), item.get("selected_end_line")
    if not isinstance(text, str) or type(start) is not int or start < 1:
        raise ValueError()
    lines = text.split("\n")
    if first is None and last is None:
        # Without validated selection metadata the whole supplied excerpt is required.
        first, last = start, start + len(lines) - 1
    if (
        type(first) is not int
        or type(last) is not int
        or not start <= first <= last < start + len(lines)
    ):
        raise ValueError()
    result = dict(item)
    result.update({line_key: first, text_key: "\n".join(lines[first - start : last - start + 1])})
    result["complete_file"] = (
        item.get("complete_file") is True and result[text_key] == text and first == 1
    )
    return result


def with_citation_bounds(response: dict, budget) -> dict:
    """Annotate an already fitted compact MCP response without repacking evidence.

    Delivered content uses the packer's LF-separated line model: a final empty
    segment is a retained blank source line, not the original file's terminator.
    Hints are all-or-none and cannot take space from text or file expansion.
    """
    from colony.compact import PROFILE

    if response.get("response_profile") != PROFILE:
        return copy.deepcopy(response)
    budget = _budget(budget)
    counter = _counter(budget)
    base = copy.deepcopy(response)
    for read in base.get("current_repository_reads", []):
        read.pop("end_line", None)
    limited = bool(base.get("context_budget", {}).get("limited"))
    base = _receipt(base, budget, counter, limited)
    if base["context_budget"]["used"] > budget.max_tokens:
        raise ContextBudgetError("Citation bounds require a fitted response")
    if not base.get("hit") or base.get("status") != "validated":
        return base
    annotated = copy.deepcopy(base)
    for read in annotated.get("current_repository_reads", []):
        text, start = read.get("content"), read.get("start_line")
        if isinstance(text, str) and text and type(start) is int and start > 0:
            read["end_line"] = start + text.count("\n")
    annotated = _receipt(annotated, budget, counter, limited)
    return annotated if annotated["context_budget"]["used"] <= budget.max_tokens else base


def with_reuse_guidance(response: dict, budget) -> dict:
    """Spend spare bytes on a trusted cue; never repack or evict source evidence."""
    budget = _budget(budget)
    counter = _counter(budget)
    base = copy.deepcopy(response)
    # Do not propagate caller/source-supplied instructions as host guidance.
    base.pop("next_action", None)
    limited = bool(base.get("context_budget", {}).get("limited"))
    base = _receipt(base, budget, counter, limited)
    if base["context_budget"]["used"] > budget.max_tokens:
        raise ContextBudgetError("Reuse guidance requires a fitted response")
    if not base.get("hit") or base.get("status") != "validated" or not base.get("current_repository_reads"):
        return base
    for cue in (
        "Use current excerpts for covered claims; read only gaps or conflicting evidence. "
        "Saved prose may be withheld; cited-file coverage is not whole-repository coverage.",
        "Use reads; check gaps.",
    ):
        annotated = copy.deepcopy(base)
        annotated["next_action"] = cue
        annotated = _receipt(annotated, budget, counter, limited)
        if annotated["context_budget"]["used"] <= budget.max_tokens:
            return annotated
    return base


def _rebuild(response):
    evidence = response.get("evidence")
    if isinstance(evidence, list):
        response["complete_files"] = [e["ref"] for e in evidence if e.get("complete_file")]
        if response.get("match_kind") == "evidence":
            response["content"] = "\n\n".join(
                f"{e['ref']}:{e['line']} ({'complete redacted file' if e.get('complete_file') else 'excerpt'})\n{e['text']}"
                for e in evidence
            )
    if "current_repository_reads" in response:
        if response.get("match_kind") == "evidence":
            response.pop("content", None)
        response["complete_files"] = [
            e["path"] for e in response["current_repository_reads"] if e.get("complete_file")
        ]


def fit_response(response: dict, budget, *, question="") -> dict:
    """Bound the complete JSON payload; never trim an answer or selected block."""
    budget = _budget(budget)
    counter = _counter(budget)
    result = copy.deepcopy(response)
    limited = bool((result.pop("context_budget", None) or {}).get("limited"))
    from colony.compact import PROFILE, compact_response

    compact = result.get("response_profile") == PROFILE
    if compact:
        try:
            result = compact_response(result, packing=True)
        except (KeyError, TypeError, ValueError):
            result = {"hit": False, "reason": "invalid_evidence", "response_profile": PROFILE}

    def measured(value, is_limited):
        if compact:
            value = compact_response(value)
        return _receipt(value, budget, counter, is_limited)

    def fits(value):
        return value["context_budget"]["used"] <= budget.max_tokens

    def miss():
        return measured({"hit": False, "reason": "budget_limited"}, True)

    originals = {}
    try:
        for key in ("evidence", "current_repository_reads"):
            if key in result:
                if not isinstance(result[key], list):
                    return miss()
                originals[key] = result[key]
                result[key] = [
                    _core(item, key == "current_repository_reads") for item in result[key]
                ]
    except (KeyError, TypeError, ValueError):
        return miss()
    shortened = any(result[key] != items for key, items in originals.items())
    _rebuild(result)
    minimal = measured(result, limited or shortened)
    if not fits(minimal):
        # Diagnostics do not establish provenance or answerability.
        for key in ("score", "relevance", "created_at"):
            result.pop(key, None)
        minimal = measured(result, True)
    if not fits(minimal) and result.get("match_kind") == "answer":
        if not any(result.get(key) for key in originals) or result.get("status") != "validated":
            return miss()
        result["match_kind"] = "evidence"
        result["recommendation"] = "Saved answer withheld; assess current source evidence."
        result.pop("content_truncated", None)
        _rebuild(result)
        minimal = measured(result, True)
    if not fits(minimal):
        return miss()
    # Compact responses stop at the required cores even if more text would fit.
    # Explicit whole-file reads or the primary compact salvage file can nominate
    # a complete span. All required cores have already fitted before any expansion.
    for key, items in originals.items():
        for index, item in enumerate(items):
            if item.get("complete_file") is not True:
                continue
            trial = copy.deepcopy(minimal)
            trial[key][index] = item
            _rebuild(trial)
            trial = measured(trial, minimal["context_budget"]["limited"])
            if fits(trial):
                minimal = trial
    return minimal
