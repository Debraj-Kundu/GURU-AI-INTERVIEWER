"""KB ingestion (PLAN §4.3) in two queued stages, run by the Celery workers in app/worker.py.

    queued -> parsing -> parsed -> embedding -> ready      (or failed / duplicate)

parse: load bytes -> dedupe -> parse; the chunks are saved on the row (status parsed).
embed: embed the saved chunks + upsert to Qdrant (status ready), then drop the saved chunks.
Tasks go out through the transactional outbox: each stage change that needs a task commits an
OutboxRow with it, and app/relay.py publishes it. Delivery is at least once, so each stage skips a
source that isn't at its stage and a repeated task does nothing. A retry starts at the first stage
that hasn't succeeded (enqueue).
"""

import hashlib

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from guru import kb
from guru.jsonlog import jlog, logged
from guru.models import JobRole

from . import storage
from .db import JobRow, OutboxRow, SessionLocal, SourceRow
from .fetch import fetch_page
from .parsers import ParseError, parse


def guru_job(job: JobRow) -> JobRole:
    """The agent package's JobRole model, built from a DB row (company loaded with it)."""
    return JobRole(
        id=job.id,
        org_id=job.org_id,
        title=job.title,
        company_name=job.company.name,
        company_id=job.company_id,
        jd_text=job.jd_text,
        max_duration_sec=job.max_duration_sec,
        questions=job.questions,
        criteria=job.criteria,
    )


async def _set(source_id: str, **fields) -> None:
    async with SessionLocal() as db:
        src = await db.get(SourceRow, source_id)
        if src is None:  # deleted while ingesting
            return
        for k, v in fields.items():
            setattr(src, k, v)
        await db.commit()


async def enqueue(db: AsyncSession, src: SourceRow) -> None:
    """Queue a source at the first stage that hasn't succeeded: embed if its chunks are saved."""
    parsed = await db.scalar(select(SourceRow.parsed_chunks.is_not(None)).where(SourceRow.id == src.id))
    src.status, src.error = ("parsed" if parsed else "queued"), None
    src.updated_at = func.now()  # always: an unchanged status wouldn't bump it, and the sweeper would resend every minute
    db.add(OutboxRow(task="embed" if parsed else "parse", source_id=src.id))
    await db.commit()  # the status and its task, atomically


@logged
async def parse_stage(source_id: str) -> None:
    async with SessionLocal() as db:
        src = await db.get(SourceRow, source_id)
    if src is None or src.status not in ("queued", "parsing"):  # deleted, or already past this stage
        return
    await _set(source_id, status="parsing", error=None)
    try:
        data = await fetch_page(src.url or "") if src.kind == "url" else await storage.get_bytes(src.s3_key or "")
        sha = hashlib.sha256(data).hexdigest()
        async with SessionLocal() as db:
            dup = await db.scalar(
                select(SourceRow).where(
                    SourceRow.company_id == src.company_id, SourceRow.sha256 == sha,
                    SourceRow.status == "ready", SourceRow.id != src.id,
                )
            )
        if dup:
            await _set(source_id, status="duplicate", sha256=sha, error=f"Same content as {dup.name}.")
            return
        chunks, excerpt = parse("html" if src.kind == "url" else src.kind, data, src.name)
    except ParseError as e:  # the content itself is the problem: retrying won't help
        await _set(source_id, status="failed", error=str(e))
        return
    async with SessionLocal() as db:
        src = await db.get(SourceRow, source_id)
        if src is None:  # deleted while parsing
            return
        src.status, src.sha256, src.parsed_chunks, src.excerpt = "parsed", sha, chunks, excerpt
        db.add(OutboxRow(task="embed", source_id=source_id))
        await db.commit()  # parsed + its embed task, atomically


@logged
async def embed_stage(source_id: str) -> None:
    async with SessionLocal() as db:
        src = await db.get(SourceRow, source_id, options=[undefer(SourceRow.parsed_chunks)])
    if src is None or src.status not in ("parsed", "embedding") or src.parsed_chunks is None:
        return
    await _set(source_id, status="embedding", error=None)
    chunks = src.parsed_chunks
    await kb.delete_source(src.org_id, src.id)  # re-embedding replaces, never duplicates
    await kb.upsert(src.org_id, src.company_id, src.name, chunks, source_id=src.id)
    await _set(source_id, status="ready", chunks=len(chunks), parsed_chunks=None)
    jlog("Source ingested", function_name="embed_stage", output={"source_id": src.id, "kind": src.kind, "chunks": len(chunks)})


async def fail(source_id: str, stage: str, e: Exception) -> None:
    """Out of retries. The saved progress stays, so Retry resumes at this stage."""
    await _set(source_id, status="failed", error=f"{stage} failed: {type(e).__name__}: {str(e)[:300]}")
