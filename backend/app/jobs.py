"""Recruiter API: jobs, question-bank CSV import, KB sources, and the interview plan."""

import csv
import io
import logging
import re
from pathlib import PurePath

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from guru import kb
from guru.jsonlog import jlog, logged
from guru.models import BankQuestion, Criterion, Plan
from guru.planner import make_plan

from . import settings, storage
from .bg import spawn
from .db import JobRow, PlanRow, SessionLocal, SourceRow, get_db
from .ingest import guru_job, ingest

router = APIRouter()


# ---------- jobs ----------

class JobIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    company_name: str = Field(min_length=1, max_length=200)
    jd_text: str = Field(min_length=1, max_length=50_000)
    max_duration_sec: int = Field(600, ge=300, le=3600)
    questions: list[BankQuestion] = []
    criteria: list[Criterion] = []

    @field_validator("criteria")
    @classmethod
    def unique_ids(cls, v: list[Criterion]) -> list[Criterion]:
        if len({c.id for c in v}) != len(v):
            raise ValueError("criterion ids must be unique")
        return v


def job_out(j: JobRow) -> dict:
    return {
        "id": j.id, "title": j.title, "company_name": j.company_name, "jd_text": j.jd_text,
        "max_duration_sec": j.max_duration_sec, "questions": j.questions, "criteria": j.criteria,
        "version": j.version, "updated_at": j.updated_at,
    }


async def get_job(db: AsyncSession, job_id: str) -> JobRow:
    job = await db.get(JobRow, job_id)
    if job is None or job.org_id != settings.DEFAULT_ORG:
        raise HTTPException(404, "Job not found")
    return job


@router.get("/jobs")
async def list_jobs(db: AsyncSession = Depends(get_db)):
    rows = await db.scalars(select(JobRow).where(JobRow.org_id == settings.DEFAULT_ORG).order_by(JobRow.updated_at.desc()))
    return [
        {"id": j.id, "title": j.title, "company_name": j.company_name, "updated_at": j.updated_at,
         "questions": len(j.questions), "criteria": len(j.criteria)}
        for j in rows
    ]


@router.post("/jobs", status_code=201)
async def create_job(body: JobIn, db: AsyncSession = Depends(get_db)):
    job = JobRow(org_id=settings.DEFAULT_ORG, **body.model_dump())
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job_out(job)


@router.get("/jobs/{job_id}")
async def read_job(job_id: str, db: AsyncSession = Depends(get_db)):
    return job_out(await get_job(db, job_id))


@router.put("/jobs/{job_id}")
async def update_job(job_id: str, body: JobIn, db: AsyncSession = Depends(get_db)):
    job = await get_job(db, job_id)
    for k, v in body.model_dump().items():
        setattr(job, k, v)
    job.version += 1  # any saved plan is now stale
    await db.commit()
    await db.refresh(job)
    return job_out(job)


@router.delete("/jobs/{job_id}", status_code=204)
async def delete_job(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await get_job(db, job_id)
    for src in await db.scalars(select(SourceRow).where(SourceRow.job_id == job_id)):
        await _remove_source_data(job, src)
    await db.delete(job)  # sources, plan, and sessions cascade
    await db.commit()


@router.post("/questions/import")
async def import_questions(file: UploadFile):
    """CSV with a `text` column, plus optional category, difficulty, and expected_signals
    (separated by ;). Returns the parsed list; the UI merges it into the job and saves."""
    raw = (await file.read(1_000_001)).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(raw))
    cols = {c.strip().lower(): c for c in reader.fieldnames or []}
    if "text" not in cols:
        raise HTTPException(422, "The CSV needs a 'text' column.")
    out = []
    for row in reader:
        text = (row.get(cols["text"]) or "").strip()
        if not text:
            continue
        get = lambda k: (row.get(cols[k]) or "").strip() if k in cols else ""  # noqa: E731
        out.append(BankQuestion(
            text=text, category=get("category"), difficulty=get("difficulty"),
            expected_signals=[s.strip() for s in get("expected_signals").split(";") if s.strip()],
        ))
    return out


# ---------- knowledge-base sources ----------

def source_out(s: SourceRow) -> dict:
    return {
        "id": s.id, "kind": s.kind, "name": s.name, "size_bytes": s.size_bytes, "status": s.status,
        "error": s.error, "chunks": s.chunks, "created_at": s.created_at,
    }


async def _source_count(db: AsyncSession, job_id: str) -> int:
    return await db.scalar(select(func.count()).select_from(SourceRow).where(SourceRow.job_id == job_id)) or 0


async def _get_source(db: AsyncSession, source_id: str) -> SourceRow:
    src = await db.get(SourceRow, source_id)
    if src is None or src.org_id != settings.DEFAULT_ORG:
        raise HTTPException(404, "Source not found")
    return src


async def _remove_source_data(job: JobRow, src: SourceRow) -> None:
    await kb.delete_source(guru_job(job), src.id)
    if src.s3_key:
        await storage.delete(src.s3_key)


@router.get("/jobs/{job_id}/sources")
async def list_sources(job_id: str, db: AsyncSession = Depends(get_db)):
    await get_job(db, job_id)
    rows = await db.scalars(select(SourceRow).where(SourceRow.job_id == job_id).order_by(SourceRow.created_at))
    return [source_out(s) for s in rows]


class UploadIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = "application/octet-stream"
    size_bytes: int = Field(gt=0)


@router.post("/jobs/{job_id}/sources/upload", status_code=201)
async def start_upload(job_id: str, body: UploadIn, db: AsyncSession = Depends(get_db)):
    """Step 1 of 2: validate, create the source row, and hand back a presigned PUT URL."""
    job = await get_job(db, job_id)
    kind = settings.FILE_KINDS.get(PurePath(body.filename).suffix.lower())
    if kind is None:
        raise HTTPException(422, f"Unsupported file type. Allowed: {', '.join(settings.FILE_KINDS)}")
    if body.size_bytes > settings.MAX_FILE_BYTES:
        raise HTTPException(413, "Files must be 20 MB or smaller.")
    if await _source_count(db, job_id) >= settings.MAX_SOURCES_PER_JOB:
        raise HTTPException(422, f"A job can have at most {settings.MAX_SOURCES_PER_JOB} sources.")
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", PurePath(body.filename).name)
    src = SourceRow(org_id=job.org_id, job_id=job_id, kind=kind, name=PurePath(body.filename).name,
                    size_bytes=body.size_bytes, status="uploading")
    db.add(src)
    await db.flush()  # assigns src.id
    src.s3_key = key = f"{job.org_id}/{job_id}/{src.id}/{safe}"
    await db.commit()
    return {"source": source_out(src), "upload_url": storage.presign_put(key, body.content_type),
            "headers": {"Content-Type": body.content_type}}


@router.post("/sources/{source_id}/uploaded")
async def finish_upload(source_id: str, db: AsyncSession = Depends(get_db)):
    """Step 2 of 2: the browser finished the PUT; check the object really arrived, then ingest."""
    src = await _get_source(db, source_id)
    size = await storage.size_of(src.s3_key or "")
    if size is None:
        raise HTTPException(409, "The upload hasn't arrived in storage yet.")
    if size > settings.MAX_FILE_BYTES:  # the presigned URL can't cap size, so check after
        await storage.delete(src.s3_key or "")
        src.status, src.error = "failed", "File is larger than 20 MB."
    else:
        src.status, src.size_bytes = "queued", size
    await db.commit()
    if src.status == "queued":
        spawn(ingest(src.id))
    return source_out(src)


class UrlIn(BaseModel):
    url: str = Field(min_length=8, max_length=2000, pattern=r"^https?://")


@router.post("/jobs/{job_id}/sources/url", status_code=201)
async def add_url(job_id: str, body: UrlIn, db: AsyncSession = Depends(get_db)):
    job = await get_job(db, job_id)
    if await _source_count(db, job_id) >= settings.MAX_SOURCES_PER_JOB:
        raise HTTPException(422, f"A job can have at most {settings.MAX_SOURCES_PER_JOB} sources.")
    src = SourceRow(org_id=job.org_id, job_id=job_id, kind="url", name=body.url, url=body.url, status="queued")
    db.add(src)
    await db.commit()
    spawn(ingest(src.id))
    return source_out(src)


@router.post("/sources/{source_id}/retry")
async def retry_source(source_id: str, db: AsyncSession = Depends(get_db)):
    src = await _get_source(db, source_id)
    if src.status not in ("failed", "duplicate"):
        raise HTTPException(409, "Only failed sources can be retried.")
    src.status, src.error = "queued", None
    await db.commit()
    spawn(ingest(src.id))
    return source_out(src)


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(source_id: str, db: AsyncSession = Depends(get_db)):
    src = await _get_source(db, source_id)
    job = await get_job(db, src.job_id)
    await _remove_source_data(job, src)
    await db.delete(src)
    await db.commit()


# ---------- interview plan ----------

def plan_out(p: PlanRow | None, job: JobRow) -> dict:
    if p is None:
        return {"status": "none", "stale": False, "plan": None, "error": None}
    return {"status": p.status, "stale": p.job_version != job.version, "error": p.error,
            "plan": p.plan["plan"] if p.plan else None, "updated_at": p.updated_at}


@router.get("/jobs/{job_id}/plan")
async def read_plan(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await get_job(db, job_id)
    return plan_out(await db.get(PlanRow, job_id), job)


@router.post("/jobs/{job_id}/plan", status_code=202)
async def generate_plan(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await get_job(db, job_id)
    if not job.questions or not job.criteria:
        raise HTTPException(422, "Add at least one question and one scoring criterion first.")
    row = await db.get(PlanRow, job_id)
    if row and row.status == "generating":  # single-flight per job (PLAN §4.4)
        raise HTTPException(409, "A plan is already being generated.")
    if row is None:
        row = PlanRow(job_id=job_id, job_version=job.version, status="generating")
        db.add(row)
    else:
        row.job_version, row.status, row.error = job.version, "generating", None
    await db.commit()
    spawn(_plan(job_id, job.version))
    return plan_out(row, job)


@logged
async def _plan(job_id: str, version: int) -> None:
    async with SessionLocal() as db:
        job = await db.get(JobRow, job_id)
        if job is None:
            return
        excerpts = await db.scalars(
            select(SourceRow.excerpt).where(SourceRow.job_id == job_id, SourceRow.status == "ready")
        )
        summary = "\n\n".join(excerpts)
    try:
        gj = guru_job(job)
        plan = Plan(job=gj, plan=await make_plan(gj, summary))
        fields = {"status": "ready", "plan": plan.model_dump(mode="json"), "error": None}
    except Exception as e:
        jlog("Plan generation failed", function_name="_plan", output={"job_id": job_id}, err=e, level=logging.ERROR)
        fields = {"status": "failed", "error": f"{type(e).__name__}: {str(e)[:300]}"}
    async with SessionLocal() as db:
        row = await db.get(PlanRow, job_id)
        if row is None or row.job_version != version:
            return  # job deleted, or a newer generation started
        for k, v in fields.items():
            setattr(row, k, v)
        await db.commit()


async def requeue_unfinished() -> None:
    """Startup: re-run work a previous process didn't finish (see bg.py)."""
    async with SessionLocal() as db:
        stuck = list(await db.scalars(
            select(SourceRow.id).where(SourceRow.status.in_(("queued", "parsing", "embedding")))
        ))
        await db.execute(
            update(PlanRow).where(PlanRow.status == "generating")
            .values(status="failed", error="Interrupted by a server restart. Generate again.")
        )
        await db.commit()
    for source_id in stuck:
        spawn(ingest(source_id))
