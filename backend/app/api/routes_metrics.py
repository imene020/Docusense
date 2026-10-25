"""MLOps dashboard endpoints (admin-only)."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser, require_admin
from app.db.database import get_db
from app.db.models import Document, QueryMetric

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/overview")
async def overview(user: CurrentUser = Depends(require_admin),
                   db: AsyncSession = Depends(get_db)):
    since = datetime.now(timezone.utc) - timedelta(days=7)
    q = select(
        func.count(QueryMetric.id),
        func.avg(QueryMetric.latency_ms),
        func.percentile_cont(0.95).within_group(QueryMetric.latency_ms),
        func.avg(QueryMetric.tokens_per_second),
        func.avg(case((QueryMetric.cache_hit, 1.0), else_=0.0)),
        func.avg(case((QueryMetric.feedback == 1, 1.0),
                      (QueryMetric.feedback == -1, 0.0))),
        func.avg(QueryMetric.agent_iterations),
    ).where(QueryMetric.org_id == user.org_id, QueryMetric.created_at >= since)
    total, avg_lat, p95_lat, tps, cache_rate, satisfaction, avg_iter = (await db.execute(q)).one()

    docs = await db.execute(
        select(Document.status, func.count()).where(Document.org_id == user.org_id)
        .group_by(Document.status))

    routes = await db.execute(
        select(QueryMetric.route, func.count()).where(
            QueryMetric.org_id == user.org_id, QueryMetric.created_at >= since)
        .group_by(QueryMetric.route))

    return {
        "routes_7d": dict(routes.all()),
        "queries_7d": total or 0,
        "avg_latency_ms": round(avg_lat or 0, 1),
        "p95_latency_ms": round(p95_lat or 0, 1),
        "avg_tokens_per_second": round(tps or 0, 1),
        "cache_hit_rate": round(cache_rate or 0, 3),
        "satisfaction_rate": round(satisfaction, 3) if satisfaction is not None else None,
        "avg_agent_iterations": round(avg_iter or 0, 2),
        "documents_by_status": dict(docs.all()),
    }


@router.get("/timeseries")
async def timeseries(user: CurrentUser = Depends(require_admin),
                     db: AsyncSession = Depends(get_db)):
    since = datetime.now(timezone.utc) - timedelta(days=7)
    bucket = func.date_trunc("hour", QueryMetric.created_at).label("bucket")
    rows = await db.execute(
        select(bucket, func.count(), func.avg(QueryMetric.latency_ms),
               func.avg(QueryMetric.tokens_per_second))
        .where(QueryMetric.org_id == user.org_id, QueryMetric.created_at >= since)
        .group_by(bucket).order_by(bucket))
    return [{"time": b.isoformat(), "queries": c,
             "avg_latency_ms": round(l or 0, 1), "tokens_per_second": round(t or 0, 1)}
            for b, c, l, t in rows.all()]


@router.get("/recent")
async def recent(user: CurrentUser = Depends(require_admin),
                 db: AsyncSession = Depends(get_db)):
    rows = await db.scalars(select(QueryMetric)
                            .where(QueryMetric.org_id == user.org_id)
                            .order_by(QueryMetric.created_at.desc()).limit(25))
    return [{"id": m.id, "query": m.query_text[:120], "latency_ms": m.latency_ms,
             "iterations": m.agent_iterations, "chunks_kept": m.chunks_kept,
             "route": m.route, "sub_questions": m.sub_questions,
             "cache_hit": m.cache_hit, "feedback": m.feedback,
             "created_at": m.created_at} for m in rows]
