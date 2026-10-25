import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import CurrentUser, get_current_user
from app.db.database import get_db
from app.db.models import Document
from app.schemas.schemas import DocumentOut
from app.services.vectorstore import delete_document
from app.workers.tasks import ingest_document, run_ingestion

router = APIRouter(prefix="/documents", tags=["documents"])

ALLOWED_SUFFIXES = {".pdf", ".csv", ".xlsx", ".txt", ".md", ".log"}
MAX_UPLOAD_BYTES = 100 * 1024 * 1024


@router.post("", response_model=DocumentOut, status_code=202)
async def upload(file: UploadFile, background: BackgroundTasks,
                 user: CurrentUser = Depends(get_current_user),
                 db: AsyncSession = Depends(get_db)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(415, f"Unsupported file type: {suffix or 'unknown'}")

    settings = get_settings()
    dest_dir = Path(settings.upload_dir) / str(user.org_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{uuid.uuid4().hex}{suffix}"

    size = 0
    with dest.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                dest.unlink(missing_ok=True)
                raise HTTPException(413, "File exceeds 100 MB limit")
            out.write(chunk)

    doc = Document(org_id=user.org_id, uploaded_by=user.user_id,
                   filename=file.filename or dest.name, file_path=str(dest),
                   mime_type=file.content_type or "application/octet-stream")
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    if settings.ingest_mode == "inline":
        background.add_task(_safe_inline_ingest, doc.id)  # runs after the response is sent
    else:
        ingest_document.delay(doc.id)  # queued for a Celery worker
    return doc


def _safe_inline_ingest(document_id: int):
    try:
        run_ingestion(document_id)
    except Exception:
        pass  # failure is already recorded on the document row


@router.get("", response_model=list[DocumentOut])
async def list_documents(user: CurrentUser = Depends(get_current_user),
                         db: AsyncSession = Depends(get_db)):
    result = await db.scalars(
        select(Document).where(Document.org_id == user.org_id).order_by(Document.created_at.desc()))
    return list(result)


@router.delete("/{document_id}", status_code=204)
async def remove(document_id: int, user: CurrentUser = Depends(get_current_user),
                 db: AsyncSession = Depends(get_db)):
    doc = await db.get(Document, document_id)
    if not doc or doc.org_id != user.org_id:  # tenant boundary check
        raise HTTPException(404, "Document not found")
    delete_document(user.org_id, doc.id)
    Path(doc.file_path).unlink(missing_ok=True)
    await db.delete(doc)
    await db.commit()
