"""Talks to the Guru backend: claims a session and fetches its plan, and delivers the results."""

import asyncio
import logging
import re

import httpx
from celery import Celery

from . import config
from .jsonlog import jlog, logged
from .models import Plan

# Building an SSL context takes ~0.5 s of CPU; httpx builds one per client, which stalled the
# agent loop at join and at the end. Build it once at import, before any job runs.
_SSL = httpx.create_ssl_context()  # same defaults as httpx (certifi, SSL_CERT_FILE), built once


def is_session_id(metadata: str | None) -> bool:
    """Backend dispatches carry the session id (32 hex chars); console/dev runs don't."""
    return bool(metadata and re.fullmatch(r"[0-9a-f]{32}", metadata))


@logged
async def load_plan(metadata: str | None, room: str) -> Plan | None:
    """Claims the session's one attempt and returns its plan; None if it already started or ended."""
    if not is_session_id(metadata):
        jlog("No session in dispatch metadata: using the fixture plan", function_name="load_plan", output={"path": str(config.PLAN_PATH)})
        return Plan.model_validate_json(config.PLAN_PATH.read_text(encoding="utf-8"))
    async with httpx.AsyncClient(timeout=10, verify=_SSL) as client:
        r = await client.post(
            f"{config.API_URL}/internal/sessions/{metadata}/start",
            json={"room": room},
            headers={"X-Service-Token": config.SERVICE_TOKEN},
        )
        if r.status_code == 409:
            return None
        r.raise_for_status()
    return Plan.model_validate(r.json())


@logged
async def post_results(session_id: str, out: dict | None) -> None:
    """Send results to the backend, which stores them and starts scoring. Declined consent sends
    only consented=false, so nothing is kept.

    Queued, so an API outage loses nothing: the payload goes to Redis (persistent), and the
    backend's webhook worker POSTs it to the API, retrying until it's accepted (worker.deliver_webhook).
    If Redis is down too, it's posted directly; if that fails, out/<session>.json is the backup."""
    body = {"consented": False}
    if out is not None:
        usage = {k: out[k] for k in ("latency", "outcome", "llm_calls", "llm_cancelled", "usage")}
        body = {"consented": True, "turns": out["turns"], "notes": out["notes"], "usage": usage,
                "duration_sec": out["duration_sec"]}
    try:
        # No expiry on this message: results must never be dropped, however long the API is down.
        await asyncio.to_thread(  # blocking Redis publish
            Celery(broker=config.REDIS_URL).send_task,
            "app.worker.deliver_webhook", args=[session_id, body], queue="webhooks",
        )
        jlog("Results queued for the backend", function_name="post_results", output={"session_id": session_id, "consented": body["consented"]})
        return
    except Exception as e:
        jlog("Queueing results failed: posting directly", function_name="post_results", err=e, level=logging.WARNING)
    async with httpx.AsyncClient(timeout=15, verify=_SSL) as client:
        r = await client.post(
            f"{config.API_URL}/internal/sessions/{session_id}/complete",
            json=body,
            headers={"X-Service-Token": config.SERVICE_TOKEN},
        )
        r.raise_for_status()
    jlog("Results sent to backend", function_name="post_results", output={"session_id": session_id, "consented": body["consented"]})
