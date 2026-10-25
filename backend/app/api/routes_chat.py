from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser, get_current_user
from app.db.database import get_db
from app.db.models import ChatMessage, ChatSession, Document, QueryMetric
from app.schemas.schemas import ChatRequest, ChatResponse, FeedbackRequest
from app.services.agent import answer_query

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def chat(body: ChatRequest, user: CurrentUser = Depends(get_current_user),
               db: AsyncSession = Depends(get_db)):
    if body.session_id:
        session = await db.get(ChatSession, body.session_id)
        if not session or session.org_id != user.org_id:
            raise HTTPException(404, "Session not found")
    else:
        session = ChatSession(org_id=user.org_id, user_id=user.user_id,
                              title=body.query[:80])
        db.add(session)
        await db.flush()

    db.add(ChatMessage(session_id=session.id, role="user", content=body.query))

    doc_names = list(await db.scalars(
        select(Document.filename).where(Document.org_id == user.org_id, Document.status == "indexed")))
    result = await answer_query(user.org_id, body.query, doc_names)
    m = result["metrics"]

    db.add(ChatMessage(session_id=session.id, role="assistant",
                       content=result["answer"], sources=result["sources"]))
    metric = QueryMetric(
        org_id=user.org_id, user_id=user.user_id, query_text=body.query,
        latency_ms=m["latency_ms"], retrieval_ms=m.get("retrieval_ms", 0),
        llm_ms=m.get("llm_ms", 0), tokens_generated=m.get("tokens_generated", 0),
        tokens_per_second=m.get("tokens_per_second", 0),
        agent_iterations=m.get("agent_iterations", 1),
        chunks_retrieved=m.get("chunks_retrieved", 0),
        chunks_kept=m.get("chunks_kept", 0), cache_hit=m.get("cache_hit", False),
        route=m.get("route", "single"), sub_questions=max(1, len(m.get("sub_questions") or [])),
    )
    db.add(metric)
    await db.commit()

    m["metric_id"] = metric.id  # lets the UI attach thumbs up/down feedback
    return ChatResponse(session_id=session.id, answer=result["answer"],
                        sources=result["sources"], metrics=m)


@router.post("/feedback", status_code=204)
async def feedback(body: FeedbackRequest, user: CurrentUser = Depends(get_current_user),
                   db: AsyncSession = Depends(get_db)):
    metric = await db.get(QueryMetric, body.metric_id)
    if not metric or metric.org_id != user.org_id:
        raise HTTPException(404, "Metric not found")
    metric.feedback = 1 if body.feedback > 0 else -1
    await db.commit()


@router.get("/sessions")
async def sessions(user: CurrentUser = Depends(get_current_user),
                   db: AsyncSession = Depends(get_db)):
    rows = await db.scalars(select(ChatSession)
                            .where(ChatSession.org_id == user.org_id,
                                   ChatSession.user_id == user.user_id)
                            .order_by(ChatSession.created_at.desc()))
    return [{"id": s.id, "title": s.title, "created_at": s.created_at} for s in rows]


@router.get("/sessions/{session_id}/messages")
async def messages(session_id: int, user: CurrentUser = Depends(get_current_user),
                   db: AsyncSession = Depends(get_db)):
    session = await db.get(ChatSession, session_id)
    if not session or session.org_id != user.org_id:
        raise HTTPException(404, "Session not found")
    rows = await db.scalars(select(ChatMessage)
                            .where(ChatMessage.session_id == session_id)
                            .order_by(ChatMessage.created_at))
    return [{"role": msg.role, "content": msg.content, "sources": msg.sources} for msg in rows]
