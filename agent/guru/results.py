"""What an interview produces: the transcript, observer notes, latency, outcome and usage."""

import json
import statistics
import time

from livekit.agents import AgentSession

from . import config
from .backend_client import is_session_id, post_results
from .jsonlog import jlog, logged
from .states import InterviewState


def build_results(session: AgentSession, s: InterviewState) -> dict:
    turns = [
        {"idx": i, "speaker": "candidate" if m.role == "user" else "interviewer", "text": m.text_content}
        for i, m in enumerate(
            m for m in session.history.items if m.type == "message" and m.role in ("user", "assistant")
        )
    ]
    lat = sorted(s.latencies)
    return {
        "session_id": s.session_id,
        "job_id": s.plan.job.id,
        "duration_sec": round(time.monotonic() - s.started_at, 1) if s.started_at else None,
        "turns": turns,
        "notes": s.notes,
        "latency": {
            "turns": len(lat),
            "p50": round(statistics.median(lat), 3) if lat else None,
            "p95": round(lat[int(0.95 * (len(lat) - 1))], 3) if lat else None,
            "first_turn_sec": s.first_turn_sec,
        },
        "outcome": {
            "ending": s.ending or "unknown",
            "questions_reached": s.idx + 1,
            "questions_planned": len(s.plan.plan.questions),
            "silence_checkins": s.silence_checkins,
            "guard_refusals": s.guard_refusals,
        },
        "llm_calls": s.llm_calls,
        "llm_cancelled": s.llm_cancelled,
        "usage": [u.model_dump() for u in session.usage.model_usage],
    }


@logged
def save_transcript(session: AgentSession, s: InterviewState) -> dict | None:
    """Writes out/<session_id>.json (kept as a backup now that results also go to the backend)."""
    if not s.consented:
        jlog("Not saving transcript: no consent", function_name="save_transcript")
        return None  # declined consent: nothing is kept
    out = build_results(session, s)
    config.OUT_DIR.mkdir(exist_ok=True)
    path = config.OUT_DIR / f"{s.session_id}.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    jlog(
        "Transcript saved",
        function_name="save_transcript",
        output={"path": str(path), "turns": len(out["turns"]), "notes": len(s.notes), "latency": out["latency"]},
    )
    return out


@logged
async def finish_interview(session: AgentSession, s: InterviewState) -> None:
    """Shutdown callback: stop the clock, keep the transcript, report to the backend."""
    if s.timer:
        s.timer.cancel()
    out = save_transcript(session, s)
    if is_session_id(s.session_id):  # console runs have no backend session to report to
        try:
            await post_results(s.session_id, out)
        except Exception:
            pass  # @logged already logged it; the local JSON (if consented) is the backup
