"""Session-wide rules that Python, not the LLM, enforces: the interview clock and silence check-ins."""

import asyncio

from livekit.agents import AgentSession

from .. import config
from ..jsonlog import jlog, logged
from ..phrases import GOODBYE, SILENCE_GOODBYE, STILL_THERE
from ..states import InterviewState


@logged
async def run_timer(session: AgentSession, s: InterviewState) -> None:
    """Python, not the LLM, owns the clock (§3.3)."""
    cap = s.plan.job.max_duration_sec
    await asyncio.sleep(max(0, cap - config.CLOSING_SEC))
    s.time_up = True
    # The next candidate-turn reply sees time_up in current_question_block; no talking over the candidate.
    jlog("Time up: next reply moves to closing", function_name="run_timer", output={"elapsed_sec": cap - config.CLOSING_SEC})
    await asyncio.sleep(config.CLOSING_SEC)
    jlog("Hard cap reached: goodbye and shutdown", function_name="run_timer", output={"session_id": s.session_id, "cap_sec": cap})
    s.ending = "hard_cap"
    session.interrupt(force=True)
    await session.say(GOODBYE, allow_interruptions=False)
    session.shutdown()


@logged
async def on_silence(session: AgentSession, s: InterviewState) -> None:
    """LiveKit marks the user "away" after SILENCE_SEC with nobody speaking. Check in up to
    SILENCE_PROMPTS times, then end the interview."""
    # LiveKit only marks the agent "thinking" once a reply is scheduled, so "away" also fired while
    # the first reply was still generating (session e3cd9607, 7 s after join). Ignore "away" while the
    # agent owes a reply; clearing it lets LiveKit re-arm the timer once the agent's turn ends. Only a
    # committed candidate turn sets awaiting_reply: noise that briefly counted as speech (an interruption
    # with no words) once held it for good and no check-in ever came (session 7c2b5e92).
    if s.awaiting_reply:
        session.reset_away_timer()
        return
    if s.silence_prompts >= config.SILENCE_PROMPTS:
        jlog("Still silent after check-ins: ending interview", function_name="on_silence", output={"prompts": s.silence_prompts})
        s.ending = "silence"
        await session.say(SILENCE_GOODBYE, allow_interruptions=False)
        session.shutdown()
        return
    s.silence_prompts += 1
    s.silence_checkins += 1
    jlog("Silence check-in", function_name="on_silence", output={"attempt": s.silence_prompts, "of": config.SILENCE_PROMPTS})
    # "away" fires once per silence; resetting re-arms the countdown, which restarts after we speak.
    session.reset_away_timer()
    session.say(STILL_THERE)
