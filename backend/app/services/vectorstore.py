"""Hybrid retrieval: dense vectors (Qdrant) fused with sparse BM25 via
Reciprocal Rank Fusion. Every operation is hard-scoped to org_id — the
tenant filter lives in the vector store itself, not just the app layer.
"""
import re
from functools import lru_cache

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, FieldCondition, Filter, MatchValue, PayloadSchemaType, PointStruct, VectorParams,
)
from rank_bm25 import BM25Okapi

from app.core.config import get_settings
from app.services.embeddings import embed_query, embed_texts, embedding_dim

RRF_K = 60


@lru_cache
def _client() -> QdrantClient:
    s = get_settings()
    if s.qdrant_url == ":memory:":  # in-process store for tests
        return QdrantClient(location=":memory:")
    return QdrantClient(url=s.qdrant_url, api_key=s.qdrant_api_key or None, timeout=30)


@lru_cache
def ensure_collection():
    settings = get_settings()
    client = _client()
    if not client.collection_exists(settings.qdrant_collection):
        client.create_collection(
            settings.qdrant_collection,
            vectors_config=VectorParams(size=embedding_dim(), distance=Distance.COSINE),
        )
    # Payload indexes make tenant / document filters fast (and Qdrant Cloud requires them for filtering)
    for field in ("org_id", "document_id"):
        client.create_payload_index(settings.qdrant_collection, field, PayloadSchemaType.INTEGER)
    client.create_payload_index(settings.qdrant_collection, "filename", PayloadSchemaType.KEYWORD)


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def index_chunks(org_id: int, document_id: int, filename: str, chunks) -> int:
    ensure_collection()
    settings = get_settings()
    vectors = embed_texts([c.text for c in chunks])
    base_id = document_id * 100_000  # deterministic, collision-free per doc
    points = [
        PointStruct(
            id=base_id + i,
            vector=vec,
            payload={
                "org_id": org_id,
                "document_id": document_id,
                "filename": filename,
                "page": chunk.page,
                "section": chunk.section,
                "chunk_type": chunk.chunk_type,
                "text": chunk.text,
                "tokens": _tokenize(chunk.text),
            },
        )
        for i, (chunk, vec) in enumerate(zip(chunks, vectors))
    ]
    _client().upsert(settings.qdrant_collection, points=points)
    return len(points)


def delete_document(org_id: int, document_id: int):
    ensure_collection()
    settings = get_settings()
    _client().delete(
        settings.qdrant_collection,
        points_selector=Filter(must=[
            FieldCondition(key="org_id", match=MatchValue(value=org_id)),
            FieldCondition(key="document_id", match=MatchValue(value=document_id)),
        ]),
    )


def hybrid_search(org_id: int, query: str, top_k: int | None = None,
                  filename: str | None = None) -> list[dict]:
    """Dense search in Qdrant + BM25 rerank over the dense candidate pool,
    fused with Reciprocal Rank Fusion. `filename` optionally scopes the
    search to one document (used by multi-document sub-questions)."""
    ensure_collection()
    settings = get_settings()
    top_k = top_k or settings.retrieval_top_k
    conditions = [FieldCondition(key="org_id", match=MatchValue(value=org_id))]
    if filename:
        conditions.append(FieldCondition(key="filename", match=MatchValue(value=filename)))
    tenant_filter = Filter(must=conditions)

    dense = _client().query_points(
        settings.qdrant_collection,
        query=embed_query(query),
        query_filter=tenant_filter,
        limit=top_k * 3,  # over-fetch: BM25 reranks this pool
        with_payload=True,
    ).points

    if not dense:
        return []

    # Sparse pass over the candidate pool
    corpus_tokens = [p.payload.get("tokens") or _tokenize(p.payload["text"]) for p in dense]
    bm25 = BM25Okapi(corpus_tokens)
    sparse_scores = bm25.get_scores(_tokenize(query))
    sparse_rank = {i: r for r, i in enumerate(
        sorted(range(len(dense)), key=lambda i: -sparse_scores[i]))}

    fused = []
    for dense_rank, point in enumerate(dense):
        rrf = 1 / (RRF_K + dense_rank) + 1 / (RRF_K + sparse_rank[dense_rank])
        fused.append({
            "document_id": point.payload["document_id"],
            "filename": point.payload["filename"],
            "page": point.payload.get("page"),
            "section": point.payload.get("section"),
            "text": point.payload["text"],
            "score": round(rrf, 5),
        })
    fused.sort(key=lambda x: -x["score"])
    return fused[:top_k]
