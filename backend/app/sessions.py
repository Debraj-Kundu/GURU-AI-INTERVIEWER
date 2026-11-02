"""Interview sessions: recruiter links, the candidate's LiveKit token, results, and the
agent-only /internal endpoints (PLAN §4.4)."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from livekit import api
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from guru import scorer
from guru.jsonlog import jlog, logged
from guru.models import Plan

from . import settings
from .bg import spawn
from .db import PlanRow, SessionLocal, SessionRow, get_db
from .jobs import get_job

router = APIRouter()
internal = APIRouter(prefix="/internal")


def session_out(s: SessionRow, full: bool = True) -> dict:
    out: dict[str, Any] = {
        "id": s.id, "job_id": s.job_id, "candidate_name": s.candidate_name, "status": s.status,
        "score_status": s.score_status, "created_at": s.created_at, "started_at": s.started_at,
        "ended_at": s.ended_at, "duration_sec": s.duration_sec,
        "overall": s.score.get("overall") if s.score else None,
    }
    if full:
        out |= {"plan": s.plan["plan"], "criteria": s.plan["job"]["criteria"], "transcript": s.transcript,
                "notes": s.notes, "usage": s.usage, "score": s.score, "score_error": s.score_error}
    return out


async def _get_session(db: AsyncSession, session_id: str) -> SessionRow:
    s = await db.get(SessionRow, session_id)
    if s is None or s.org_id != settings.DEFAULT_ORG:
        raise HTTPException(404, "Interview not found")
    return s


# ---------- recruiter ----------

class SessionIn(BaseModel):
    candidate_name: str = Field("", max_length=200)


@router.post("/jobs/{job_id}/sessions", status_code=201)
async def create_session(job_id: str, body: SessionIn, db: AsyncSession = Depends(get_db)):
    job = await get_job(db, job_id)
    plan = await db.get(PlanRow, job_id)
    if plan is None or plan.status != "ready" or plan.plan is None:
        raise HTTPException(409, "Generate the interview plan first.")
    if plan.job_version != job.version:
        raise HTTPException(409, "The job changed since the plan was made. Regenerate the plan first.")
    s = SessionRow(org_id=job.org_id, job_id=job_id, candidate_name=body.candidate_name, room_name="", plan=plan.plan)
    db.add(s)
    await db.flush()
    s.room_name = f"interview-{s.id}"  # unique per session: dispatch only happens when the room is created
    await db.commit()
    return session_out(s, full=False)


@router.get("/jobs/{job_id}/sessions")
async def list_sessions(job_id: str, db: AsyncSession = Depends(get_db)):
    await get_job(db, job_id)
    rows = await db.scalars(select(SessionRow).where(SessionRow.job_id == job_id).order_by(SessionRow.created_at.desc()))
    return [session_out(s, full=False) for s in rows]


@router.get("/sessions/{session_id}")
async def read_session(session_id: str, db: AsyncSession = Depends(get_db)):
    return session_out(await _get_session(db, session_id))


@router.post("/sessions/{session_id}/score", status_code=202)
async def rescore(session_id: str, db: AsyncSession = Depends(get_db)):
    s = await _get_session(db, session_id)
    if s.status != "completed" or not s.transcript:
        raise HTTPException(409, "Only completed interviews with a transcript can be scored.")
    if s.score_status == "scoring":
        raise HTTPException(409, "Scoring is already running.")
    s.score_status, s.score_error = "scoring", None
    await db.commit()
    spawn(score(session_id))
    return session_out(s, full=False)


# ---------- candidate ----------

@router.get("/interviews/{session_id}")
async def interview_info(session_id: str, db: AsyncSession = Depends(get_db)):
    """What the candidate page may see: no plan, notes, or scores (PLAN Q8)."""
    s = await _get_session(db, session_id)
    job = s.plan["job"]
    return {"id": s.id, "candidate_name": s.candidate_name, "status": s.status, "job_title": job["title"],
            "company_name": job["company_name"], "max_duration_sec": job["max_duration_sec"]}


@router.post("/interviews/{session_id}/token")
async def interview_token(session_id: str, db: AsyncSession = Depends(get_db)):
    """LiveKit token with explicit agent dispatch; the dispatch metadata is the session id, and the
    agent fetches the plan by it. https://docs.livekit.io/frontends/reference/tokens-grants"""
    s = await _get_session(db, session_id)
    if s.status in ("completed", "declined"):
        raise HTTPException(409, "This interview has already ended.")
    token = (
        api.AccessToken(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
        .with_identity(f"candidate-{s.id[:8]}")
        .with_name(s.candidate_name or "Candidate")
        .with_ttl(timedelta(minutes=15))  # only needs to outlive the join; the connection then stays up
        .with_grants(api.VideoGrants(
            room_join=True, room=s.room_name, can_subscribe=True, can_publish=True,
            can_publish_data=True, can_publish_sources=["microphone"],  # audio only
        ))
        .with_room_config(api.RoomConfiguration(
            agents=[api.RoomAgentDispatch(agent_name=settings.AGENT_NAME, metadata=s.id)]
        ))
        .to_jwt()
    )
    if s.status == "created":
        s.status, s.started_at = "active", datetime.now(timezone.utc)
        await db.commit()
    # ponytail: the link itself is the credential (no invite expiry or single use yet, PLAN §4.4).
    return {"server_url": settings.LIVEKIT_URL, "participant_token": token}


# ---------- agent only ----------

def require_service(x_service_token: str = Header("")) -> None:
    if not settings.SERVICE_TOKEN or x_service_token != settings.SERVICE_TOKEN:
        raise HTTPException(401, "Bad service token")


@internal.get("/sessions/{session_id}/plan", dependencies=[Depends(require_service)])
async def agent_plan(session_id: str, db: AsyncSession = Depends(get_db)):
    return (await _get_session(db, session_id)).plan


class CompleteIn(BaseModel):
    consented: bool
    turns: list[dict] = []
    notes: list[dict] = []
    usage: dict = {}
    duration_sec: float | None = None


@internal.post("/sessions/{session_id}/complete", dependencies=[Depends(require_service)])
async def agent_complete(session_id: str, body: CompleteIn, db: AsyncSession = Depends(get_db)):
    s = await _get_session(db, session_id)
    s.ended_at = datetime.now(timezone.utc)
    if not body.consented:
        s.status = "declined"  # nothing from a declined interview is kept
    else:
        s.status, s.transcript, s.notes = "completed", body.turns, body.notes
        s.usage, s.duration_sec = body.usage, body.duration_sec
        if body.turns:
            s.score_status = "scoring"
    await db.commit()
    if s.score_status == "scoring":
        spawn(score(session_id))
    return {"ok": True}


@logged
async def score(session_id: str) -> None:
    async with SessionLocal() as db:
        s = await db.get(SessionRow, session_id)
        if s is None:
            return
        payload = {"session_id": s.id, "turns": s.transcript, "notes": s.notes}
        plan = Plan.model_validate(s.plan)
    try:
        fields = {"score": await scorer.score_session(payload, plan), "score_status": "scored", "score_error": None}
    except Exception as e:
        jlog("Scoring failed", function_name="score", output={"session_id": session_id}, err=e, level=logging.ERROR)
        fields = {"score_status": "failed", "score_error": f"{type(e).__name__}: {str(e)[:300]}"}
    async with SessionLocal() as db:
        s = await db.get(SessionRow, session_id)
        if s is not None:
            for k, v in fields.items():
                setattr(s, k, v)
            await db.commit()
