"""KB ingestion (PLAN §4.3): load bytes -> dedupe -> parse (process pool) -> embed + upsert to Qdrant."""

import asyncio
import hashlib
import logging
from concurrent.futures import ProcessPoolExecutor

from sqlalchemy import select

from guru import kb
from guru.jsonlog import jlog, logged
from guru.models import Job

from . import storage
from .db import JobRow, SessionLocal, SourceRow
from .fetch import fetch_page
from .parsers import ParseError, parse

# Parsing is CPU-bound: separate processes so it never stalls the API's event loop (PLAN §4.8).
pool: ProcessPoolExecutor | None = None
_gate = asyncio.Semaphore(2)  # sources ingested at once; each one also fans out embedding calls


def guru_job(job: JobRow) -> Job:
    """The agent package's Job model, built from a DB row."""
    return Job(
        id=job.id,
        org_id=job.org_id,
        title=job.title,
        company_name=job.company_name,
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


@logged
async def ingest(source_id: str) -> None:
    async with _gate:
        async with SessionLocal() as db:
            src = await db.get(SourceRow, source_id)
            job = await db.get(JobRow, src.job_id) if src else None
        if src is None or job is None:
            return
        try:
            await _set(source_id, status="parsing", error=None)
            data = await fetch_page(src.url or "") if src.kind == "url" else await storage.get_bytes(src.s3_key or "")
            sha = hashlib.sha256(data).hexdigest()
            async with SessionLocal() as db:
                dup = await db.scalar(
                    select(SourceRow).where(
                        SourceRow.job_id == job.id, SourceRow.sha256 == sha,
                        SourceRow.status == "ready", SourceRow.id != src.id,
                    )
                )
            if dup:
                await _set(source_id, status="duplicate", sha256=sha, error=f"Same content as {dup.name}.")
                return
            kind = "html" if src.kind == "url" else src.kind
            loop = asyncio.get_running_loop()
            chunks, excerpt = await loop.run_in_executor(pool, parse, kind, data, src.name)
            await _set(source_id, status="embedding", sha256=sha)
            gj = guru_job(job)
            await kb.delete_source(gj, src.id)  # re-ingest replaces, never duplicates
            await kb.upsert(gj, src.name, chunks, source_id=src.id)
            await _set(source_id, status="ready", chunks=len(chunks), excerpt=excerpt)
            jlog("Source ingested", function_name="ingest", output={"source_id": src.id, "kind": src.kind, "chunks": len(chunks)})
        except ParseError as e:
            await _set(source_id, status="failed", error=str(e))
        except Exception as e:
            jlog("Ingest failed", function_name="ingest", output={"source_id": source_id}, err=e, level=logging.ERROR)
            await _set(source_id, status="failed", error=f"Ingest failed: {type(e).__name__}: {str(e)[:300]}")
