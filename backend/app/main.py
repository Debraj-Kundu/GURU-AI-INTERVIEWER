"""Guru API. Run: uv run fastapi dev app/main.py --port 8080   (http://localhost:8080/docs)"""

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from guru import kb
from guru.jsonlog import jlog

from . import jobs, sessions, settings, storage
from .bg import spawn
from .db import SessionLocal, SessionRow, engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s %(message)s")
    await storage.ensure_bucket()
    await kb.ensure_collection()
    await jobs.requeue_unfinished()
    async with SessionLocal() as db:
        for sid in await db.scalars(select(SessionRow.id).where(SessionRow.score_status == "scoring")):
            spawn(sessions.score(sid))
    yield
    await engine.dispose()


class RequestId:
    """Every response carries X-Request-ID (the caller's, else a new one) and gets one log line with
    it: method, path, status, ms to the response headers. The agent and the webhook worker send the
    interview's session id, so one id follows an interview across services. Plain ASGI rather than
    BaseHTTPMiddleware, which can get in the way of the SSE stream's cancellation on disconnect."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        given = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")[:64]
        rid = given if given.isprintable() and given else uuid.uuid4().hex[:16]
        t0 = time.monotonic()

        async def send_with_id(message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), (b"x-request-id", rid.encode())]
                status = message["status"]
                if scope["path"] != "/healthz":  # health checks every 5 s would bury everything
                    jlog("API request", function_name="request_id",
                         output={"request_id": rid, "method": scope["method"], "path": scope["path"],
                                 "status": status, "ms": round((time.monotonic() - t0) * 1000)},
                         level=logging.WARNING if status >= 500 else logging.INFO)
            await send(message)

        await self.app(scope, receive, send_with_id)


app = FastAPI(title="Guru API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=[settings.WEB_ORIGIN], allow_methods=["*"], allow_headers=["*"],
    expose_headers=["X-Request-ID"],  # readable by the web app, to quote in bug reports
)
app.add_middleware(RequestId)  # added last = outermost: it sees CORS preflights too
app.include_router(jobs.router)
app.include_router(sessions.router)
app.include_router(sessions.internal)


@app.get("/healthz")
async def healthz():
    return {"ok": True}
