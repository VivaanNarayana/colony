"""Local ONNX semantic retrieval. No trail data is sent to a model service."""

import hashlib
import math
import os
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

import sqlite_vec
from fastembed import TextEmbedding
from fastembed.rerank.cross_encoder import TextCrossEncoder

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
EMBED_REVISION = "aa8f8b060edb00e03bfdd08813a2949946c8ba55"
RANK_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"
RANK_REVISION = "a09144355adeed5f58c8ed011d209bf8ee5a1fec"
# Passage inputs match v7 again after reverting the experimental positional-query
# mask. Reuse valid v7 vectors; experimental v8/v9 stores still rebuild on mismatch.
INDEX_VERSION = f"semantic-v7:{EMBED_MODEL}:section-aware-paragraphs-question-arguments-masked"
CANDIDATES = 12


@lru_cache(maxsize=1)
def models():
    # Embedding and ranking have separate pools. Bound their combined pressure
    # on the short, interleaved query path as well as bulk-passage inference.
    # Model inputs, batch-one execution and numerical outputs remain unchanged.
    options = {
        "cache_dir": os.environ.get("COLONY_MODEL_CACHE"),
        "threads": min(4, os.cpu_count() or 1),
    }
    return TextEmbedding(
        model_name=EMBED_MODEL,
        revision=EMBED_REVISION,
        specific_model_path=os.environ.get("COLONY_EMBED_MODEL_DIR"),
        **options,
    ), TextCrossEncoder(
        model_name=RANK_MODEL,
        revision=RANK_REVISION,
        specific_model_path=os.environ.get("COLONY_RANK_MODEL_DIR"),
        **options,
    )


@lru_cache(maxsize=1)
def index_version() -> str:
    embedding, _ = models()
    directory = Path(embedding.model._model_dir)
    digest = hashlib.sha256()
    # Include tokenization as well as weights for explicitly provisioned offline snapshots.
    for name in [
        "model_optimized.onnx",
        "config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
    ]:
        digest.update(name.encode())
        digest.update(hashlib.sha256((directory / name).read_bytes()).digest())
    return INDEX_VERSION + ":" + digest.hexdigest()


def passage(question: str, content: str) -> str:
    # Preserve the question even when a large capture is truncated by the model tokenizer.
    return f"Question: {question}\nAnswer: {content[:4000]}"


def requires_procedure(question: str) -> bool:
    """Recognize explicit how-to/imperative requests, not descriptive questions."""
    return bool(
        re.match(
            r"(?:(?:which|what)\s+(?:[\w-]+\s+){0,2}(?:command|flags|options)\b|"
            r"how\s+(?:do|can|should)\s+(?:i|we)\s+|(?:please\s+)?"
            r"(?:start|run|launch|install|deploy|configure|set\s+up|create|restore|rollback|roll\s+back)\b)",
            question.strip(),
            re.I,
        )
    )


def procedural_text(text: str) -> bool:
    """Prefer concrete commands or instructions over a capability overview."""
    return bool(
        "```" in text
        or "~~~" in text
        or re.search(r"`[^`\n]+`", text)
        or re.search(
            r"(?:^|\n)(?:run|use|execute|start|launch|install|build|create|add|set|configure|open|invoke|call|connect|deploy|retry|issue|join|activate|copy|edit|apply|export|restore|revert)\b",
            text,
            re.I,
        )
        or re.search(r"(?:^|\n)[^\n]*\s--?[a-zA-Z][\w-]*", text)
    )


def embed(text: str, *, query: bool = False) -> bytes:
    model, _ = models()
    vectors = model.query_embed(text) if query else model.passage_embed(text)
    return sqlite_vec.serialize_float32(next(vectors).tolist())


def rank(question: str, passages: list[str]) -> list[float]:
    if not passages:
        return []
    _, model = models()
    # Cross-encoder batches also pad every pair to the longest passage. Source
    # snippets vary substantially in length; avoid that work without changing
    # the passage set, ordering, model or relevance thresholds.
    return [float(score) for score in model.rerank(question, passages, batch_size=1)]


def embed_passages(texts: list[str]) -> list[bytes]:
    model, _ = models()
    return (
        # These bounded source paragraphs vary greatly in length. The ONNX
        # encoder pads a batch to its longest input; individual inference avoids
        # repeating that padding work and preserves both vectors and input order.
        [sqlite_vec.serialize_float32(v.tolist()) for v in model.passage_embed(texts, batch_size=1)]
        if texts
        else []
    )


def fused_score(cosine: float, logit: float) -> float:
    """A ranking heuristic, never an answer-correctness probability."""
    return 0.8 * cosine + 0.2 / (1 + math.exp(-max(-30, min(30, logit))))


STOP_WORDS = set(
    [
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "do",
        "does",
        "did",
        "can",
        "could",
        "would",
        "should",
        "how",
        "why",
        "what",
        "which",
        "when",
        "during",
        "where",
        "who",
        "to",
        "of",
        "in",
        "on",
        "for",
        "from",
        "with",
        "and",
        "or",
        "as",
        "at",
        "it",
        "its",
        "this",
        "that",
        "i",
        "we",
        "you",
        "our",
        "your",
        "up",
    ]
)


def terms(text: str) -> set[str]:
    # Question framing is not evidence content. Keep its substantive subject and
    # condition, instead of weighting an absent generic verb more than both.
    text = re.sub(r"^\s*what\s+(?:happens|occurs)\s+(?:when|if|during)\s+", "", text, flags=re.I)
    # In passive questions, get is an auxiliary rather than a requested fact.
    # Preserve substantive uses such as "get credentials" and "get a token".
    text = re.sub(r"\b(?:get|gets|got|getting)\s+([a-z]{3,}ed)\b", r"\1", text, flags=re.I)
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    # Shared prefixes cover plural/inflected prose and common code identifiers.
    words = re.findall(r"[a-z][a-z0-9]+", text.lower())
    result = set()
    for word in words:
        if word in STOP_WORDS:
            continue
        word = (
            word[:-1] if len(word) > 4 and word.endswith("s") and not word.endswith("ss") else word
        )
        # Keep availability paraphrases coherent without dropping query terms or
        # lowering coverage: a server being "down" and an "outage" describe the
        # same state. The subject and other requested facts must still match.
        if word in {"down", "outage", "offline", "unavailable", "unavailability", "downtime"}:
            word = "offline"
        if word in {"queued", "queuing", "queueing"}:
            word = "queue"
        result.add(word[:6])
    return result


def keyword_coverage(question: str, passages: list[str]) -> list[float]:
    query = terms(question)
    docs = [terms(p) for p in passages]
    if not query:
        return [0.0] * len(docs)
    frequency = Counter(t for doc in docs for t in doc)
    weights = {t: 1 + math.log((len(docs) + 1) / (frequency[t] + 1)) for t in query}
    total = sum(weights.values())
    return [sum(weights[t] for t in query & doc) / total for doc in docs]


def local_term_support(query: set[str], text: str) -> bool:
    """Require most query terms within at most two neighboring sentences."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return any(
        len(query & terms(" ".join(sentences[i : i + 2]))) > len(query) / 2
        for i in range(len(sentences))
    )


def focused_passage(question: str, text: str) -> str:
    """Rank coherent prose instead of letting unrelated neighboring topics dominate.

    Keep fenced code intact. This model input view is used only for text documents:
    eligibility still uses the
    original paragraph, and displayed evidence still comes from current file bytes.
    """
    if "```" in text or "~~~" in text:
        return text
    heading, separator, body = text.partition("\n")
    if not heading.startswith("Section: "):
        heading, separator, body = "", "", text
    sentences = re.split(r"(?<=[.!?])\s+", body)
    if len(sentences) <= 2:
        return text
    query = terms(question)
    windows = [" ".join(sentences[i : i + 2]) for i in range(len(sentences) - 1)]
    chosen = max(windows, key=lambda s: (len(query & terms(s)), -len(s)))
    return heading + separator + chosen


def evidence_score(cosine: float, logit: float, coverage: float) -> float:
    return 0.4 * cosine + 0.2 / (1 + math.exp(-max(-30, min(30, logit)))) + 0.4 * coverage
