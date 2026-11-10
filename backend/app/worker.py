"""Celery workers, one queue each:
  parse, embed  KB ingestion (app/ingest.py), published only by the outbox relay (app/relay.py)
  webhooks      the agent's interview-completion webhook, delivered to the API (deliver_webhook)

    uv run celery -A app.worker worker -Q parse -n parse@%h --pool=solo --loglevel=info
    uv run celery -A app.worker worker -Q embed -n embed@%h --pool=solo --loglevel=info
    uv run celery -A app.worker worker -Q webhooks -n webhooks@%h --pool=solo --loglevel=info

--pool=solo is for Windows (one task at a time); on Linux use the default prefork pool.
"""

import asyncio
import logging
from functools import cache

import httpx
from celery import Celery

from . import ingest, settings

celery = Celery("guru", broker=settings.REDIS_URL)
celery.conf.update(
    task_routes={
        "app.worker.parse_source": {"queue": "parse"},
        "app.worker.embed_source": {"queue": "embed"},
        "app.worker.deliver_webhook": {"queue": "webhooks"},
    },
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


WEBHOOK_MAX_WAIT = 5 * 60  # backoff cap; must stay under visibility_timeout (15 min) or a waiting retry is handed out twice


@celery.task(bind=True, max_retries=None)  # no limit: an interview's results are never dropped
def deliver_webhook(self, session_id: str, body: dict) -> None:
    """POST the agent's completion payload to the API, retrying until it's accepted, so an API that
    was down gets the results once it's back. The agent publishes this with no expiry. The endpoint
    is idempotent, so a repeat after a lost acknowledgement is a no-op."""
    url = f"{settings.guru_config.API_URL}/internal/sessions/{session_id}/complete"
    wait = min(WEBHOOK_MAX_WAIT, 5 * 2**self.request.retries)  # 5, 10, 20 ... 300 s
    try:
        r = httpx.post(url, json=body, headers={"X-Service-Token": settings.SERVICE_TOKEN}, timeout=30)
    except httpx.TransportError as e:  # API down or unreachable
        raise self.retry(exc=e, countdown=wait) from e
    if r.status_code >= 500:
        raise self.retry(exc=RuntimeError(f"HTTP {r.status_code}"), countdown=wait)
    if r.is_error:  # 4xx: a bad token or unknown session; retrying won't fix it
        logging.getLogger(__name__).error("webhook for %s rejected: HTTP %s %s", session_id, r.status_code, r.text[:300])
        return
    logging.getLogger(__name__).info("webhook for %s delivered: %s", session_id, r.text[:200])
