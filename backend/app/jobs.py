"""Recruiter API: companies, their jobs (roles), question-bank CSV import, company KB sources, and
the interview plan."""

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
from guru.models import QuestionBank, Criterion
from guru.planner import make_plan

from . import settings, storage
from .bg import spawn
from .db import CompanyRow, JobRow, PlanRow, SessionLocal, SourceRow, get_db
from .ingest import enqueue, guru_job

router = APIRouter()


# ---------- companies ----------

class CompanyIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


def company_out(c: CompanyRow, roles: int = 0, sources: int = 0) -> dict:
    return {"id": c.id, "name": c.name, "roles": roles, "sources": sources, "updated_at": c.updated_at}


async def get_company(db: AsyncSession, company_id: str) -> CompanyRow:
    company = await db.get(CompanyRow, company_id)
    if company is None or company.org_id != settings.DEFAULT_ORG:
        raise HTTPException(404, "Company not found")
    return company


def _count(model, company_col):
    return select(func.count()).select_from(model).where(company_col == CompanyRow.id).scalar_subquery()


@router.get("/companies")
async def list_companies(db: AsyncSession = Depends(get_db)):
    rows = await db.execute(
        select(CompanyRow, _count(JobRow, JobRow.company_id), _count(SourceRow, SourceRow.company_id))
        .where(CompanyRow.org_id == settings.DEFAULT_ORG)
        .order_by(CompanyRow.name)
    )
    return [company_out(c, roles, sources) for c, roles, sources in rows]


@router.post("/companies", status_code=201)
async def create_company(body: CompanyIn, db: AsyncSession = Depends(get_db)):
    company = CompanyRow(org_id=settings.DEFAULT_ORG, name=body.name.strip())
    db.add(company)
    await db.commit()
    await db.refresh(company)
    return company_out(company)


@router.get("/companies/{company_id}")
async def read_company(company_id: str, db: AsyncSession = Depends(get_db)):
    return company_out(await get_company(db, company_id))


@router.put("/companies/{company_id}")
async def rename_company(company_id: str, body: CompanyIn, db: AsyncSession = Depends(get_db)):
    company = await get_company(db, company_id)
    company.name = body.name.strip()
    # Plans carry the company name into the interview, so they are stale now.
    await db.execute(update(JobRow).where(JobRow.company_id == company_id).values(version=JobRow.version + 1))
    await db.commit()
    await db.refresh(company)
    return company_out(company)


@router.delete("/companies/{company_id}", status_code=204)
async def delete_company(company_id: str, db: AsyncSession = Depends(get_db)):
    company = await get_company(db, company_id)
    for src in await db.scalars(select(SourceRow).where(SourceRow.company_id == company_id)):
        await _remove_source_data(src)
    await db.delete(company)  # roles, sources, plans, and sessions cascade in the database
    await db.commit()


# ---------- jobs (a company's roles) ----------

class JobIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    jd_text: str = Field(min_length=1, max_length=50_000)
    max_duration_sec: int = Field(600, ge=300, le=3600)
    questions: list[QuestionBank] = []
    criteria: list[Criterion] = []

    @field_validator("criteria")
    @classmethod
    def unique_ids(cls, v: list[Criterion]) -> list[Criterion]:
        if len({c.id for c in v}) != len(v):
            raise ValueError("criterion ids must be unique")
        return v


def job_out(j: JobRow) -> dict:
    return {
        "id": j.id, "company_id": j.company_id, "company_name": j.company.name, "title": j.title,
        "jd_text": j.jd_text,
        "max_duration_sec": j.max_duration_sec, "questions": j.questions, "criteria": j.criteria,
        "version": j.version, "updated_at": j.updated_at,
    }


async def get_job(db: AsyncSession, job_id: str) -> JobRow:
    job = await db.get(JobRow, job_id)
    if job is None or job.org_id != settings.DEFAULT_ORG:
        raise HTTPException(404, "Job not found")
    return job


@router.get("/companies/{company_id}/jobs")
async def list_jobs(company_id: str, db: AsyncSession = Depends(get_db)):
    await get_company(db, company_id)
    rows = await db.scalars(select(JobRow).where(JobRow.company_id == company_id).order_by(JobRow.title))
    return [
        {"id": j.id, "title": j.title, "updated_at": j.updated_at,
         "questions": len(j.questions), "criteria": len(j.criteria)}
        for j in rows
    ]


@router.post("/companies/{company_id}/jobs", status_code=201)
async def create_job(company_id: str, body: JobIn, db: AsyncSession = Depends(get_db)):
    company = await get_company(db, company_id)
    job = JobRow(org_id=company.org_id, company=company, **body.model_dump())
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
    await db.delete(job)  # plan and sessions cascade; the company's knowledge base stays
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
        out.append(QuestionBank(
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


async def _source_count(db: AsyncSession, company_id: str) -> int:
    return await db.scalar(
        select(func.count()).select_from(SourceRow).where(SourceRow.company_id == company_id)
    ) or 0


async def _get_source(db: AsyncSession, source_id: str) -> SourceRow:
    src = await db.get(SourceRow, source_id)
    if src is None or src.org_id != settings.DEFAULT_ORG:
        raise HTTPException(404, "Source not found")
    return src


async def _remove_source_data(src: SourceRow) -> None:
    await kb.delete_source(src.org_id, src.id)
    if src.s3_key:
        await storage.delete(src.s3_key)


@router.get("/companies/{company_id}/sources")
async def list_sources(company_id: str, db: AsyncSession = Depends(get_db)):
    await get_company(db, company_id)
    rows = await db.scalars(
        select(SourceRow).where(SourceRow.company_id == company_id).order_by(SourceRow.created_at)
    )
    return [source_out(s) for s in rows]


class UploadIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = "application/octet-stream"
    size_bytes: int = Field(gt=0)


@router.post("/companies/{company_id}/sources/upload", status_code=201)
async def start_upload(company_id: str, body: UploadIn, db: AsyncSession = Depends(get_db)):
    """Step 1 of 2: validate, create the source row, and hand back a presigned PUT URL."""
    company = await get_company(db, company_id)
    kind = settings.FILE_KINDS.get(PurePath(body.filename).suffix.lower())
    if kind is None:
        raise HTTPException(422, f"Unsupported file type. Allowed: {', '.join(settings.FILE_KINDS)}")
    if body.size_bytes > settings.MAX_FILE_BYTES:
        raise HTTPException(413, "Files must be 20 MB or smaller.")
    if await _source_count(db, company_id) >= settings.MAX_SOURCES_PER_COMPANY:
        raise HTTPException(422, f"A company can have at most {settings.MAX_SOURCES_PER_COMPANY} sources.")
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", PurePath(body.filename).name)
    src = SourceRow(org_id=company.org_id, company_id=company_id, kind=kind, name=PurePath(body.filename).name,
                    size_bytes=body.size_bytes, status="uploading")
    db.add(src)
    await db.flush()  # assigns src.id
    src.s3_key = key = f"{company.org_id}/{company_id}/{src.id}/{safe}"
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
        src.size_bytes = size
        await enqueue(db, src)
    await db.commit()
    return source_out(src)


class UrlIn(BaseModel):
    url: str = Field(min_length=8, max_length=2000, pattern=r"^https?://")


@router.post("/companies/{company_id}/sources/url", status_code=201)
async def add_url(company_id: str, body: UrlIn, db: AsyncSession = Depends(get_db)):
    company = await get_company(db, company_id)
    if await _source_count(db, company_id) >= settings.MAX_SOURCES_PER_COMPANY:
        raise HTTPException(422, f"A company can have at most {settings.MAX_SOURCES_PER_COMPANY} sources.")
    src = SourceRow(org_id=company.org_id, company_id=company_id, kind="url", name=body.url, url=body.url,
                    status="queued")
    db.add(src)
    await db.flush()
    await enqueue(db, src)
    return source_out(src)


@router.post("/sources/{source_id}/retry")
async def retry_source(source_id: str, db: AsyncSession = Depends(get_db)):
    src = await _get_source(db, source_id)
    if src.status not in ("failed", "duplicate"):
        raise HTTPException(409, "Only failed sources can be retried.")
    await enqueue(db, src)  # resumes at the stage that failed
    return source_out(src)


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(source_id: str, db: AsyncSession = Depends(get_db)):
    src = await _get_source(db, source_id)
    await _remove_source_data(src)
    await db.delete(src)
    await db.commit()


# ---------- interview plan ----------

def plan_out(p: PlanRow | None, job: JobRow) -> dict:
    if p is None:
        return {"status": "none", "stale": False, "plan": None, "error": None}
    return {"status": p.status, "stale": p.job_version != job.version, "error": p.error,
            "plan": p.plan["plan"] if p.plan else None, "updated_at": p.updated_at,
            # the criteria this plan scores against: the role's own, or derived from the job description
            "criteria": p.plan["job"]["criteria"] if p.plan else None}


@router.get("/jobs/{job_id}/plan")
async def read_plan(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await get_job(db, job_id)
    return plan_out(await db.get(PlanRow, job_id), job)


@router.post("/jobs/{job_id}/plan", status_code=202)
async def generate_plan(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await get_job(db, job_id)
    # Questions and criteria are optional: the planner derives them from the job description.
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
            select(SourceRow.excerpt).where(SourceRow.company_id == job.company_id, SourceRow.status == "ready")
        )
        summary = "\n\n".join(excerpts)
    try:
        plan = await make_plan(guru_job(job), summary)  # the job as planned: derived criteria if none were given
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
    """Startup: plans a previous process didn't finish (see bg.py). Ingest recovery is the relay's sweeper."""
    async with SessionLocal() as db:
        await db.execute(
            update(PlanRow).where(PlanRow.status == "generating")
            .values(status="failed", error="Interrupted by a server restart. Generate again.")
        )
        await db.commit()

