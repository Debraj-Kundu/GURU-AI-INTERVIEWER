"""Celery workers for KB ingestion (app/ingest.py): one stage per queue. Tasks are published only by
the outbox relay (app/relay.py), never directly.

    uv run celery -A app.worker worker -Q parse -n parse@%h --pool=solo --loglevel=info
    uv run celery -A app.worker worker -Q embed -n embed@%h --pool=solo --loglevel=info

--pool=solo is for Windows (one task at a time); on Linux use the default prefork pool.
"""

import asyncio
import logging
from functools import cache

from celery import Celery

from . import ingest, settings

celery = Celery("guru", broker=settings.REDIS_URL)
celery.conf.update(
    task_routes={"app.worker.parse_source": {"queue": "parse"}, "app.worker.embed_source": {"queue": "embed"}},
    task_acks_late=True,  # a task a crashed worker was running goes back on the queue
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    # A task taken but not acked (worker crashed) goes back on the queue after this. Must stay above
    # the longest task plus its retry countdown (40 s), or a slow task would run twice.
    broker_transport_options={"visibility_timeout": 15 * 60},
)

# Message TTL (Celery `expires`): a worker discards a task older than this instead of running it, so
# repeats piled up while workers were down die off. A source whose task expired is re-queued by the
# relay's sweeper. The queues and kombu's binding keys get no TTL: an expiring queue would delete
# pending tasks, and kombu keeps bindings TTL-free on purpose.
TASK_EXPIRES = 30 * 60
TASKS = {"parse": "app.worker.parse_source", "embed": "app.worker.embed_source"}

RETRIES = 3  # temporary errors (network, rate limits): retried after 10, 20, 40 s


@cache
def _loop() -> asyncio.AbstractEventLoop:
    """One event loop per worker process: the DB pool and the cached Qdrant/LLM clients are bound to it."""
    return asyncio.new_event_loop()


def _run(task, stage: str, coro_fn, source_id: str):
    try:
        return _loop().run_until_complete(coro_fn(source_id))
    except Exception as e:
        if task.request.retries < RETRIES:
            raise task.retry(exc=e, countdown=10 * 2**task.request.retries) from e
        _loop().run_until_complete(ingest.fail(source_id, stage, e))
        logging.getLogger(__name__).exception("%s failed for %s", stage, source_id)


@celery.task(bind=True, max_retries=RETRIES)
def parse_source(self, source_id: str) -> None:
    _run(self, "Parsing", ingest.parse_stage, source_id)


@celery.task(bind=True, max_retries=RETRIES)
def embed_source(self, source_id: str) -> None:
    _run(self, "Embedding", ingest.embed_stage, source_id)

