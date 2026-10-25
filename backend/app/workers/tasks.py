"""Document ingestion: parse → chunk → embed → index.

`run_ingestion` is plain synchronous code so it can run in two places:
- a Celery worker (docker-compose / production: scalable, survives restarts)
- a FastAPI background thread (INGEST_MODE=inline: free hosting with no worker)
"""
import logging

from app.db.database import SyncSessionLocal
from app.db.models import Document
from app.services.chunker import chunk_blocks
from app.services.parser import parse_document
from app.services.vectorstore import index_chunks
from app.workers.celery_app import celery

log = logging.getLogger(__name__)


def run_ingestion(document_id: int) -> None:
    with SyncSessionLocal() as db:
        doc = db.get(Document, document_id)
        if not doc:
            return
        doc.status = "processing"
        db.commit()
        try:
            blocks = parse_document(doc.file_path, doc.mime_type)
            chunks = chunk_blocks(blocks)
            if not chunks:
                raise ValueError("No extractable text found (scanned PDFs need OCR, which isn't enabled)")
            doc.num_chunks = index_chunks(doc.org_id, doc.id, doc.filename, chunks)
            doc.doc_metadata = {
                "blocks": len(blocks),
                "tables": sum(1 for b in blocks if b.block_type == "table"),
            }
            doc.status = "indexed"
            doc.error = None
            db.commit()
        except Exception as exc:
            log.exception("ingestion failed for document %s", document_id)
            doc.status = "failed"
            doc.error = str(exc)[:2000]
            db.commit()
            raise


@celery.task(name="ingest_document", bind=True, max_retries=2, default_retry_delay=30)
def ingest_document(self, document_id: int):
    try:
        run_ingestion(document_id)
    except Exception as exc:
        raise self.retry(exc=exc)
