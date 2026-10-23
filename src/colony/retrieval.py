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
    # Identical pairs have identical batch-one model inputs. Compute them once
    # within this invocation and scatter back, without caching text across calls.
    unique = list(dict.fromkeys(passages))
    scores = dict(
        zip(unique, [float(s) for s in model.rerank(question, unique, batch_size=1)], strict=True)
    )
    return [scores[p] for p in passages]


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
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    # Shared prefixes cover plural/inflected prose and common code identifiers.
    words = re.findall(r"[a-z][a-z0-9]+", text.lower())
    return {
        (w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w)[:6]
        for w in words
        if w not in STOP_WORDS
    }


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


def evidence_score(cosine: float, logit: float, coverage: float) -> float:
    return 0.4 * cosine + 0.2 / (1 + math.exp(-max(-30, min(30, logit)))) + 0.4 * coverage
