"""Interview sessions: recruiter links, the candidate's LiveKit token, results, and the
agent-only /internal endpoints (PLAN §4.4)."""

import asyncio
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from livekit import api
from pydantic import BaseModel, Field
from sqlalchemy import select, text
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
        "invite_expires_at": s.invite_expires_at, "expired": expired(s),
        "score_status": s.score_status, "created_at": s.created_at, "started_at": s.started_at,
        "ended_at": s.ended_at, "duration_sec": s.duration_sec,
        "overall": s.score.get("overall") if s.score else None,
    }
    if full:
        out |= {"plan": s.plan["plan"], "criteria": s.plan["job"]["criteria"], "transcript": s.transcript,
                "notes": s.notes, "usage": s.usage, "score": s.score, "score_error": s.score_error}
    return out


def expired(s: SessionRow) -> bool:
    """The invite lapsed before the interview started. Once started, rejoins don't expire."""
    return s.status == "created" and s.invite_expires_at is not None and datetime.now(timezone.utc) > s.invite_expires_at


def start_window(s: SessionRow) -> None:
    """The link is valid for INVITE_TTL_MIN from now, until the interview starts."""
    s.invite_expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.INVITE_TTL_MIN)


EVENTS = "session_events"  # Postgres NOTIFY channel; the SSE endpoint below listens on it


async def notify(db: AsyncSession, s: SessionRow) -> None:
    """Announce a session change. Call before commit: Postgres delivers a NOTIFY only when its
    transaction commits, so listeners never see a change that rolled back, nor miss one that didn't."""
    payload = {"id": s.id, "job_id": s.job_id, "status": s.status, "score_status": s.score_status}
    await db.execute(text("SELECT pg_notify(:channel, :payload)"), {"channel": EVENTS, "payload": json.dumps(payload)})


async def _get_session(db: AsyncSession, session_id: str) -> SessionRow:
    s = await db.get(SessionRow, session_id)
    if s is None or s.org_id != settings.DEFAULT_ORG:
        raise HTTPException(404, "Interview not found")
    return s


# ---------- recruiter ----------

class SessionIn(BaseModel):
    candidate_name: str = Field("", max_length=200)


@router.get("/invite-defaults")
async def invite_defaults():
    return {"ttl_min": settings.INVITE_TTL_MIN}


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
    start_window(s)
    await notify(db, s)
    await db.commit()
    return session_out(s, full=False)


@router.post("/sessions/{session_id}/renew")
async def renew_link(session_id: str, db: AsyncSession = Depends(get_db)):
    """Same link, fresh expiry window."""
    s = await _get_session(db, session_id)
    if s.status != "created":
        raise HTTPException(409, "Only links for interviews that haven't started can be renewed.")
    start_window(s)
    await db.commit()
    return session_out(s, full=False)


@router.get("/jobs/{job_id}/sessions")
async def list_sessions(job_id: str, db: AsyncSession = Depends(get_db)):
    await get_job(db, job_id)
    rows = await db.scalars(select(SessionRow).where(SessionRow.job_id == job_id).order_by(SessionRow.created_at.desc()))
    return [session_out(s, full=False) for s in rows]


@router.get("/jobs/{job_id}/sessions/events")
async def session_events(job_id: str):
    """Server-sent events: one `data:` line per change to this role's interviews (started, ended,
    scored), so the recruiter page updates without polling. The page reloads the list on each
    event and on every reconnect, so nothing is missed while it was disconnected."""
    async with SessionLocal() as db:
        await get_job(db, job_id)  # 404 before the stream starts

    async def stream():
        events: asyncio.Queue[str] = asyncio.Queue()

        def on_event(_conn, _pid, _channel, payload: str) -> None:
            if json.loads(payload).get("job_id") == job_id:
                events.put_nowait(payload)

        # ponytail: one LISTEN connection per open page. Fine for a handful of recruiters; share one
        # listener per API process if there are hundreds of pages open at once.
        conn = await asyncpg.connect(settings.DATABASE_URL.replace("+asyncpg", ""))
        try:
            await conn.add_listener(EVENTS, on_event)
            yield "retry: 3000\n\n"  # reconnect after 3 s if dropped; also sends the headers at once
            while True:
                try:
                    yield f"data: {await asyncio.wait_for(events.get(), 15)}\n\n"
                except TimeoutError:
                    yield ": ping\n\n"  # keeps proxies from closing an idle stream
        finally:  # the client went away: Starlette cancels this generator
            await conn.close()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


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
    await notify(db, s)
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
            "company_name": job["company_name"], "max_duration_sec": job["max_duration_sec"], "expired": expired(s)}


@router.post("/interviews/{session_id}/token")
async def interview_token(session_id: str, db: AsyncSession = Depends(get_db)):
    """LiveKit token with explicit agent dispatch; the dispatch metadata is the session id, and the
    agent fetches the plan by it. https://docs.livekit.io/frontends/reference/tokens-grants"""
    s = await _get_session(db, session_id)
    if s.status in ("completed", "declined"):
        raise HTTPException(409, "This interview has already ended.")
    if expired(s):
        raise HTTPException(410, "This interview link has expired. Ask the recruiter for a new one.")
    if s.status == "created":
        # No interviewer has joined yet (e.g. none was running). A token only dispatches the agent
        # when it creates the room, so each try gets a fresh room. Once the agent starts
        # (/internal/.../start), the room is fixed and a rejoin lands back with the same agent.
        s.room_name = f"interview-{s.id}-{secrets.token_hex(3)}"
        await db.commit()
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
            agents=[api.RoomAgentDispatch(agent_name=settings.AGENT_NAME, metadata=s.id)],
            # LiveKit closes a room departure_timeout s after the candidate leaves (default 20; the
            # agent doesn't keep it open). Outlast the agent's rejoin window; the agent deletes
            # the room itself when the window ends.
            departure_timeout=int(settings.guru_config.RECONNECT_GRACE_SEC) + 30,
        ))
        .to_jwt()
    )
    # ponytail: the link itself is the credential until it expires (no single use beyond the one attempt, PLAN §4.4).
    # started_at lets a rejoining page show the real time left.
    return {"server_url": settings.LIVEKIT_URL, "participant_token": token, "started_at": s.started_at}


# ---------- agent only ----------

def require_service(x_service_token: str = Header("")) -> None:
    if not settings.SERVICE_TOKEN or x_service_token != settings.SERVICE_TOKEN:
        raise HTTPException(401, "Bad service token")


class StartIn(BaseModel):
    room: str


@internal.post("/sessions/{session_id}/start", dependencies=[Depends(require_service)])
async def agent_start(session_id: str, body: StartIn, db: AsyncSession = Depends(get_db)):
    """The agent joined and is about to speak: the one attempt starts now. Returns the plan.
    Any other agent (an older room, or a re-dispatch after the interview began) gets 409."""
    s = await _get_session(db, session_id)
    if s.status != "created" or body.room != s.room_name:
        raise HTTPException(409, "This interview already started or ended.")
    s.status, s.started_at = "active", datetime.now(timezone.utc)
    await notify(db, s)
    await db.commit()
    return s.plan


class CompleteIn(BaseModel):
    consented: bool
    turns: list[dict] = []
    notes: list[dict] = []
    usage: dict = {}
    duration_sec: float | None = None


@internal.post("/sessions/{session_id}/complete", dependencies=[Depends(require_service)])
async def agent_complete(session_id: str, body: CompleteIn, db: AsyncSession = Depends(get_db)):
    """The completion webhook (delivered from the queue by worker.deliver_webhook, which retries
    until it gets a 2xx). Idempotent: a redelivery after the first success changes nothing."""
    s = await _get_session(db, session_id)
    if s.status in ("completed", "declined"):
        return {"ok": True, "duplicate": True}
    s.ended_at = datetime.now(timezone.utc)
    if not body.consented:
        s.status = "declined"  # nothing from a declined interview is kept
    else:
        s.status, s.transcript, s.notes = "completed", body.turns, body.notes
        s.usage, s.duration_sec = body.usage, body.duration_sec
        if body.turns:
            s.score_status = "scoring"
    await notify(db, s)
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
            await notify(db, s)
            await db.commit()
