"""Local ONNX semantic retrieval. No trail data is sent to a model service."""

import hashlib
import os
from functools import lru_cache
from pathlib import Path

import sqlite_vec
from fastembed import TextEmbedding
from fastembed.rerank.cross_encoder import TextCrossEncoder

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
EMBED_REVISION = "aa8f8b060edb00e03bfdd08813a2949946c8ba55"
RANK_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"
RANK_REVISION = "a09144355adeed5f58c8ed011d209bf8ee5a1fec"
INDEX_VERSION = f"semantic-v4:{EMBED_MODEL}:questions-answers-source-blocks32-16"
CANDIDATES = 12


@lru_cache(maxsize=1)
def models():
    options = {"cache_dir": os.environ.get("COLONY_MODEL_CACHE"), "threads": 2}
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


def embed(text: str, *, query: bool = False) -> bytes:
    model, _ = models()
    vectors = model.query_embed(text) if query else model.passage_embed(text)
    return sqlite_vec.serialize_float32(next(vectors).tolist())


def rank(question: str, passages: list[str]) -> list[float]:
    if not passages:
        return []
    _, model = models()
    return [float(score) for score in model.rerank(question, passages)]


def embed_passages(texts: list[str]) -> list[bytes]:
    model, _ = models()
    return (
        [sqlite_vec.serialize_float32(v.tolist()) for v in model.passage_embed(texts)]
        if texts
        else []
    )
