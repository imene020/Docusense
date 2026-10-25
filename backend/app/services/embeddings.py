"""Embedding service on fastembed (ONNX Runtime).

fastembed runs the same bge-small model as sentence-transformers but without
PyTorch, which cuts memory from ~1.5 GB to a few hundred MB. That is what makes
the backend fit on free-tier hosting.
"""
from functools import lru_cache

from fastembed import TextEmbedding

from app.core.config import get_settings

QUERY_PREFIX = "Represent this sentence for searching relevant passages: "  # bge convention


@lru_cache
def _model() -> TextEmbedding:
    s = get_settings()
    return TextEmbedding(model_name=s.embedding_model, cache_dir=s.embedding_cache_dir)


def embed_texts(texts: list[str]) -> list[list[float]]:
    return [vec.tolist() for vec in _model().embed(texts, batch_size=32)]


def embed_query(text: str) -> list[float]:
    return next(iter(_model().embed([QUERY_PREFIX + text]))).tolist()


@lru_cache
def embedding_dim() -> int:
    return len(embed_query("dimension probe"))
