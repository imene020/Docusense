from datetime import datetime
from pydantic import BaseModel, EmailStr


class RegisterRequest(BaseModel):
    org_name: str
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class DocumentOut(BaseModel):
    id: int
    filename: str
    status: str
    num_chunks: int
    created_at: datetime
    error: str | None = None

    class Config:
        from_attributes = True


class ChatRequest(BaseModel):
    session_id: int | None = None
    query: str


class SourceChunk(BaseModel):
    document_id: int
    filename: str
    page: int | None = None
    snippet: str
    score: float


class ChatResponse(BaseModel):
    session_id: int
    answer: str
    sources: list[SourceChunk]
    metrics: dict


class FeedbackRequest(BaseModel):
    metric_id: int
    feedback: int  # 1 or -1
