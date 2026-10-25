"""Semantic response cache.

Recurring questions ("what's our refund policy?") are served from Redis
without touching the LLM. Keys are embeddings; a hit requires cosine
similarity above SEMANTIC_CACHE_THRESHOLD within the same tenant.

The cache is an optimisation, never a dependency: if REDIS_URL is empty or
Redis is down, every call quietly degrades to a cache miss.
"""
import hashlib
import json
import logging
from functools import lru_cache

import numpy as np
import redis

from app.core.config import get_settings
from app.services.embeddings import embed_query

log = logging.getLogger(__name__)
TTL_SECONDS = 60 * 60 * 24
MAX_ENTRIES_SCANNED = 200


@lru_cache
def _r():
    url = get_settings().redis_url
    return redis.from_url(url, socket_timeout=2) if url else None


def _key(org_id: int, query: str) -> str:
    digest = hashlib.sha1(query.strip().lower().encode()).hexdigest()[:16]
    return f"semcache:{org_id}:{digest}"


def cache_lookup(org_id: int, query: str) -> dict | None:
    r = _r()
    if r is None:
        return None
    try:
        threshold = get_settings().semantic_cache_threshold
        qvec = np.array(embed_query(query))
        for i, key in enumerate(r.scan_iter(f"semcache:{org_id}:*", count=100)):
            if i >= MAX_ENTRIES_SCANNED:
                break
            raw = r.get(key)
            if not raw:
                continue
            entry = json.loads(raw)
            if float(np.dot(qvec, np.array(entry["vector"]))) >= threshold:
                return entry["response"]
    except Exception as exc:  # cache must never break a query
        log.warning("semantic cache lookup skipped: %s", exc)
    return None


def cache_store(org_id: int, query: str, response: dict):
    r = _r()
    if r is None:
        return
    try:
        r.setex(_key(org_id, query), TTL_SECONDS,
                json.dumps({"vector": embed_query(query), "response": response}))
    except Exception as exc:
        log.warning("semantic cache store skipped: %s", exc)
