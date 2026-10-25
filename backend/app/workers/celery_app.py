from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery = Celery("docusense", broker=settings.redis_url, backend=settings.redis_url)
celery.conf.update(
    task_serializer="json",
    task_acks_late=True,          # re-deliver if a worker dies mid-ingest
    worker_prefetch_multiplier=1, # fair scheduling for heavy documents
    task_time_limit=60 * 20,
)
celery.autodiscover_tasks(["app.workers"])
