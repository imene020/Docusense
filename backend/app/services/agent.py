"""Agentic RAG pipeline, built as a LangGraph state machine.

                          ┌──────────────┐
                 ┌───────▶│ direct_answer│──────────────────────────────┐
                 │chitchat└──────────────┘                              │
 START ─▶ cache ─┤ miss                                                 ▼
          │      └▶ router ─┬─ single ───────────────┐                 END
          │ hit             └─ multi_doc ─▶ decompose ┤                  ▲
          └──────────────────────────────────────────▶│                  │
                                                      ▼                  │
                                     ┌──────────▶ retrieve               │
                                     │                ▼                  │
                                     │             grade                 │
                                     │                ▼                  │
                                     └── rewrite ◀─ enough context? ─yes─▶ synthesize

Three agentic behaviours, each a real graph construct rather than a
hard-coded `if`:

1. Agentic routing     — `router` classifies the question; a conditional edge
                         picks the path (chit-chat / single search / multi-doc).
2. Multi-doc reasoning — `decompose` splits a comparison-style question into
                         sub-questions, each optionally scoped to one document;
                         they are retrieved and graded independently, then
                         `synthesize` reasons across all of them.
3. Self-correction     — `grade` drops irrelevant chunks; if a sub-question is
                         left without evidence, a conditional edge loops back
                         through `rewrite` → `retrieve` (bounded iterations).

Every node records timings and token counts into the shared state, which the
API persists for the MLOps dashboard.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from functools import lru_cache
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from app.core.config import get_settings
from app.services import llm
from app.services.cache import cache_lookup, cache_store
from app.services.vectorstore import hybrid_search

log = logging.getLogger(__name__)


# ─────────────────────────────── state ────────────────────────────────

class SubQuestion(TypedDict):
    question: str          # what we search for (may be rewritten)
    original: str          # what it was before any rewrite
    filename: str | None   # scope retrieval to one document, or None for all
    candidates: list[dict]
    kept: list[dict]


class AgentState(TypedDict, total=False):
    # inputs
    org_id: int
    query: str
    doc_names: list[str]
    # control flow
    route: Literal["chitchat", "single", "multi_doc"]
    sub_questions: list[SubQuestion]
    iteration: int
    cache_hit: bool
    # outputs
    answer: str
    sources: list[dict]
    # telemetry
    trace: list[str]
    retrieval_ms: float
    llm_ms: float
    answer_tokens: int
    answer_ms: float
    chunks_retrieved: int
    rewrites: int
    last_fetched: int
    cached_response: dict


# ─────────────────────────────── prompts ──────────────────────────────

ROUTER_SYSTEM = """You route questions for a document Q&A system. Decide which path fits:
- "chitchat": greetings, thanks, or questions about the assistant itself; no documents needed.
- "single": one question answerable by searching the knowledge base once.
- "multi_doc": the question compares, contrasts or combines information from two or more
  distinct documents or topics (e.g. "compare X and Y", "differences between the 2023 and
  2024 reports", "which contract has the longer notice period").
Respond with JSON only: {"route": "chitchat" | "single" | "multi_doc"}"""

DECOMPOSE_SYSTEM = """You break a complex question into simpler sub-questions that can each be
answered by searching ONE topic or document. You are given the list of available document
filenames. When a sub-question is clearly about one listed document, set "filename" to that
exact filename; otherwise set it to null. Produce between 2 and {max_n} sub-questions.
Respond with JSON only:
{{"sub_questions": [{{"question": "...", "filename": "exact-name.pdf" | null}}]}}"""

GRADER_SYSTEM = """You are a strict relevance grader for a retrieval system. You receive a question
and numbered document chunks. Select the chunks that contain information useful for answering
the question. Ignore chunks that are merely on the same general topic.
Respond with JSON only: {"relevant": [list of chunk numbers]}"""

REWRITE_SYSTEM = """You rewrite search queries that returned no useful documents. Produce ONE
alternative phrasing that uses different keywords, synonyms, or expanded terms
(e.g. "staff attrition" -> "employee turnover rate resignations").
Respond with JSON only: {"query": "..."}"""

ANSWER_SYSTEM = """You are DocuSense, a document intelligence assistant. Answer using ONLY the
provided context. Cite every claim inline with the chunk number in brackets, like [2].
If the context does not contain the answer, say so plainly. Never invent facts, numbers,
or document names."""

MULTI_ANSWER_SYSTEM = ANSWER_SYSTEM + """
The context is grouped by sub-question. Answer each part, then reason across the parts to
answer the overall question (compare, contrast, or combine as asked). If evidence for one
part is missing, say which part could not be answered."""

CHITCHAT_SYSTEM = """You are DocuSense, an assistant that answers questions about a company's
uploaded documents (PDFs, spreadsheets, text files) with cited sources. Reply briefly and
helpfully. If asked what you can do, explain that, and suggest asking about their documents."""


# ─────────────────────────────── helpers ──────────────────────────────

async def _llm_json(prompt: str, system: str, state: AgentState) -> dict:
    """Call the LLM in JSON mode; returns {} if the model produced invalid JSON."""
    result = await llm.generate(prompt, system=system, json_mode=True)
    state["llm_ms"] = state.get("llm_ms", 0) + result["duration_ms"]
    try:
        parsed = json.loads(result["text"])
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        log.warning("LLM returned non-JSON: %.200s", result["text"])
        return {}


def _trace(state: AgentState, step: str) -> list[str]:
    return [*state.get("trace", []), step]


def _min_kept(state: AgentState) -> int:
    # A single question wants a little corroboration; each multi-doc part needs at least one hit.
    return 2 if state.get("route") == "single" else 1


# ─────────────────────────────── nodes ────────────────────────────────

async def cache_node(state: AgentState) -> dict:
    cached = await asyncio.to_thread(cache_lookup, state["org_id"], state["query"])
    if cached:
        return {"cache_hit": True, "cached_response": cached, "trace": _trace(state, "cache:hit")}
    return {"cache_hit": False, "trace": _trace(state, "cache:miss")}


async def router_node(state: AgentState) -> dict:
    docs = ", ".join(state.get("doc_names", [])[:50]) or "(none)"
    result = await _llm_json(f"Available documents: {docs}\n\nQuestion: {state['query']}",
                             ROUTER_SYSTEM, state)
    route = result.get("route")
    if route not in ("chitchat", "single", "multi_doc"):
        route = "single"  # safe default: searching never hurts correctness
    if route == "multi_doc" and len(state.get("doc_names", [])) < 2:
        route = "single"  # nothing to compare across
    return {"route": route, "llm_ms": state.get("llm_ms", 0),
            "trace": _trace(state, f"router:{route}")}


async def direct_answer_node(state: AgentState) -> dict:
    result = await llm.generate(state["query"], system=CHITCHAT_SYSTEM)
    return {"answer": result["text"], "sources": [],
            "answer_tokens": result["tokens"], "answer_ms": result["duration_ms"],
            "llm_ms": state.get("llm_ms", 0) + result["duration_ms"],
            "trace": _trace(state, "direct_answer")}


def single_node(state: AgentState) -> dict:
    sq: SubQuestion = {"question": state["query"], "original": state["query"],
                       "filename": None, "candidates": [], "kept": []}
    return {"sub_questions": [sq], "trace": _trace(state, "plan:single")}


async def decompose_node(state: AgentState) -> dict:
    settings = get_settings()
    names = state.get("doc_names", [])
    result = await _llm_json(
        f"Available documents:\n" + "\n".join(f"- {n}" for n in names[:50])
        + f"\n\nQuestion: {state['query']}",
        DECOMPOSE_SYSTEM.format(max_n=settings.max_sub_questions), state)

    subs: list[SubQuestion] = []
    for item in result.get("sub_questions", [])[: settings.max_sub_questions]:
        if not isinstance(item, dict) or not str(item.get("question", "")).strip():
            continue
        filename = item.get("filename")
        subs.append({"question": item["question"].strip(), "original": item["question"].strip(),
                     # only trust filenames that really exist, so a hallucinated name can't hide results
                     "filename": filename if filename in names else None,
                     "candidates": [], "kept": []})
    if len(subs) < 2:  # decomposition failed: fall back to answering the whole question at once
        subs = [{"question": state["query"], "original": state["query"],
                 "filename": None, "candidates": [], "kept": []}]
    return {"sub_questions": subs, "llm_ms": state.get("llm_ms", 0),
            "trace": _trace(state, f"decompose:{len(subs)}")}


async def retrieve_node(state: AgentState) -> dict:
    """Retrieve for every sub-question that still lacks evidence, in parallel."""
    need = _min_kept(state)
    t0 = time.perf_counter()

    async def run(sq: SubQuestion) -> SubQuestion:
        if len(sq["kept"]) >= need:
            return sq  # already answered on an earlier pass
        hits = await asyncio.to_thread(hybrid_search, state["org_id"], sq["question"],
                                       None, sq["filename"])
        return {**sq, "candidates": hits}

    subs = await asyncio.gather(*(run(sq) for sq in state["sub_questions"]))
    fetched = sum(len(sq["candidates"]) for sq in subs)
    return {"sub_questions": list(subs), "last_fetched": fetched,
            "chunks_retrieved": state.get("chunks_retrieved", 0) + fetched,
            "retrieval_ms": state.get("retrieval_ms", 0) + (time.perf_counter() - t0) * 1000,
            "iteration": state.get("iteration", 0) + 1,
            "trace": _trace(state, f"retrieve:{fetched}")}


async def grade_node(state: AgentState) -> dict:
    """Self-correction step 1: one batched grading call per sub-question keeps
    only the chunks that actually help (batching keeps API usage low)."""
    keep_k = get_settings().rerank_keep_k
    need = _min_kept(state)

    async def grade(sq: SubQuestion) -> SubQuestion:
        if len(sq["kept"]) >= need or not sq["candidates"]:
            return {**sq, "candidates": []}
        numbered = "\n\n".join(f"[{i + 1}] {c['text'][:900]}" for i, c in enumerate(sq["candidates"]))
        result = await _llm_json(f"Question: {sq['original']}\n\nChunks:\n{numbered}",
                                 GRADER_SYSTEM, state)
        picks = result.get("relevant")
        if not isinstance(picks, list):
            kept = sq["candidates"][:keep_k]  # fail open: noisy context beats none
        else:
            idx = [int(p) - 1 for p in picks if str(p).isdigit()]
            kept = [sq["candidates"][i] for i in idx if 0 <= i < len(sq["candidates"])]
        seen = {(c["document_id"], c["text"][:80]) for c in sq["kept"]}
        merged = sq["kept"] + [c for c in kept if (c["document_id"], c["text"][:80]) not in seen]
        return {**sq, "kept": merged[:keep_k], "candidates": []}

    subs = await asyncio.gather(*(grade(sq) for sq in state["sub_questions"]))
    kept_counts = "/".join(str(len(sq["kept"])) for sq in subs)
    return {"sub_questions": list(subs), "llm_ms": state.get("llm_ms", 0),
            "trace": _trace(state, f"grade:{kept_counts}")}


async def rewrite_node(state: AgentState) -> dict:
    """Self-correction step 2: rephrase only the sub-questions that came back empty."""
    need = _min_kept(state)

    async def rewrite(sq: SubQuestion) -> SubQuestion:
        if len(sq["kept"]) >= need:
            return sq
        result = await _llm_json(f"Question that found nothing: {sq['question']}",
                                 REWRITE_SYSTEM, state)
        new_q = str(result.get("query", "")).strip() or sq["question"]
        # A scoped search that found nothing might be scoped wrongly: widen it on retry.
        return {**sq, "question": new_q, "filename": None}

    subs = await asyncio.gather(*(rewrite(sq) for sq in state["sub_questions"]))
    weak = sum(1 for sq in state["sub_questions"] if len(sq["kept"]) < need)
    return {"sub_questions": list(subs), "llm_ms": state.get("llm_ms", 0),
            "rewrites": state.get("rewrites", 0) + weak,
            "trace": _trace(state, f"rewrite:{weak}")}


async def synthesize_node(state: AgentState) -> dict:
    subs = state["sub_questions"]
    sources: list[dict] = []
    sections: list[str] = []
    for sq in subs:
        lines = []
        for c in sq["kept"]:
            sources.append(c)
            lines.append(f"[{len(sources)}] ({c['filename']}, page {c.get('page') or '-'})\n{c['text']}")
        if len(subs) > 1:
            body = "\n\n".join(lines) or "(no relevant context found)"
            sections.append(f"### Sub-question: {sq['original']}\n{body}")
        else:
            sections.extend(lines)

    if not sources:
        return {"answer": "I couldn't find information in your documents that answers this question. "
                          "Try rephrasing, or check that the relevant file has finished indexing.",
                "sources": [], "answer_tokens": 0, "answer_ms": 0,
                "trace": _trace(state, "synthesize:empty")}

    system = MULTI_ANSWER_SYSTEM if len(subs) > 1 else ANSWER_SYSTEM
    result = await llm.generate(f"Context:\n\n" + "\n\n".join(sections)
                                + f"\n\nQuestion: {state['query']}", system=system)
    return {"answer": result["text"], "sources": sources,
            "answer_tokens": result["tokens"], "answer_ms": result["duration_ms"],
            "llm_ms": state.get("llm_ms", 0) + result["duration_ms"],
            "trace": _trace(state, "synthesize")}


# ─────────────────────────────── edges ────────────────────────────────

def after_cache(state: AgentState) -> str:
    return "hit" if state.get("cache_hit") else "miss"


def after_router(state: AgentState) -> str:
    return state["route"]


def after_grade(state: AgentState) -> str:
    need = _min_kept(state)
    all_covered = all(len(sq["kept"]) >= need for sq in state["sub_questions"])
    out_of_budget = state.get("iteration", 0) >= get_settings().max_agent_iterations
    knowledge_base_empty = state.get("last_fetched", 0) == 0 and state.get("iteration", 0) == 1
    return "synthesize" if all_covered or out_of_budget or knowledge_base_empty else "rewrite"


# ─────────────────────────────── graph ────────────────────────────────

@lru_cache
def build_graph():
    g = StateGraph(AgentState)
    g.add_node("cache", cache_node)
    g.add_node("router", router_node)
    g.add_node("direct_answer", direct_answer_node)
    g.add_node("single", single_node)
    g.add_node("decompose", decompose_node)
    g.add_node("retrieve", retrieve_node)
    g.add_node("grade", grade_node)
    g.add_node("rewrite", rewrite_node)
    g.add_node("synthesize", synthesize_node)

    g.add_edge(START, "cache")
    g.add_conditional_edges("cache", after_cache, {"hit": END, "miss": "router"})
    g.add_conditional_edges("router", after_router,
                            {"chitchat": "direct_answer", "single": "single", "multi_doc": "decompose"})
    g.add_edge("direct_answer", END)
    g.add_edge("single", "retrieve")
    g.add_edge("decompose", "retrieve")
    g.add_edge("retrieve", "grade")
    g.add_conditional_edges("grade", after_grade, {"rewrite": "rewrite", "synthesize": "synthesize"})
    g.add_edge("rewrite", "retrieve")  # the self-correcting loop
    g.add_edge("synthesize", END)
    return g.compile()


def graph_mermaid() -> str:
    """Mermaid source of the compiled graph (used for docs and the /agent/graph endpoint)."""
    return build_graph().get_graph().draw_mermaid()


# ─────────────────────────────── entrypoint ───────────────────────────

async def answer_query(org_id: int, query: str, doc_names: list[str] | None = None) -> dict:
    t0 = time.perf_counter()
    final: AgentState = await build_graph().ainvoke(
        {"org_id": org_id, "query": query, "doc_names": doc_names or [], "trace": []},
        {"recursion_limit": 25},
    )

    if final.get("cache_hit"):
        cached = final["cached_response"]
        cached["metrics"] = {**cached["metrics"], "cache_hit": True,
                             "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                             "trace": final["trace"]}
        return cached

    answer_ms = final.get("answer_ms", 0)
    tokens = final.get("answer_tokens", 0)
    subs = final.get("sub_questions", [])
    response = {
        "answer": final.get("answer", ""),
        "sources": [
            {"document_id": c["document_id"], "filename": c["filename"], "page": c.get("page"),
             "snippet": c["text"][:300], "score": c["score"]}
            for c in final.get("sources", [])
        ],
        "metrics": {
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "retrieval_ms": round(final.get("retrieval_ms", 0), 1),
            "llm_ms": round(final.get("llm_ms", 0), 1),
            "tokens_generated": tokens,
            "tokens_per_second": round(tokens / (answer_ms / 1000), 1) if answer_ms else 0,
            "agent_iterations": final.get("iteration", 0),
            "chunks_retrieved": final.get("chunks_retrieved", 0),
            "chunks_kept": len(final.get("sources", [])),
            "cache_hit": False,
            "route": final.get("route", "single"),
            "sub_questions": [sq["original"] for sq in subs] if len(subs) > 1 else [],
            "rewrites": final.get("rewrites", 0),
            "trace": final.get("trace", []),
        },
    }
    if response["sources"]:  # only cache grounded answers
        await asyncio.to_thread(cache_store, org_id, query, response)
    return response
