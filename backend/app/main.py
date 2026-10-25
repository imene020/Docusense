from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import routes_auth, routes_chat, routes_documents, routes_metrics
from app.core.config import get_settings
from app.db.database import Base, engine
from app.services.agent import graph_mermaid


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(get_settings().upload_dir).mkdir(parents=True, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)  # use Alembic in real prod
    yield


app = FastAPI(title="DocuSense API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_auth.router)
app.include_router(routes_documents.router)
app.include_router(routes_chat.router)
app.include_router(routes_metrics.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/agent/graph")
async def agent_graph():
    """Mermaid source of the LangGraph agent — paste into mermaid.live to view."""
    return {"mermaid": graph_mermaid()}
