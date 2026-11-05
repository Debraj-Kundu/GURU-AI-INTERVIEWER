"""Guru API. Run: uv run fastapi dev app/main.py --port 8080   (http://localhost:8080/docs)"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from guru import kb

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


app = FastAPI(title="Guru API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=[settings.WEB_ORIGIN], allow_methods=["*"], allow_headers=["*"]
)
app.include_router(jobs.router)
app.include_router(sessions.router)
app.include_router(sessions.internal)


@app.get("/healthz")
async def healthz():
    return {"ok": True}
