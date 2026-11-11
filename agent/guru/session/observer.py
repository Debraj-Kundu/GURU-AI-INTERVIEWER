"""The silent Observer: a second LLM that grades each answer in the background and leaves a note
for the interviewer's next reply. It never speaks and never blocks the conversation."""

import logging
from typing import TYPE_CHECKING

from livekit.agents import AgentSession

from .. import config, prompts
from ..aio import spawn
from ..jsonlog import jlog, logged
from ..llm import generate_json
from ..models import ObserverNote

if TYPE_CHECKING:
    from ..agents import InterviewerAgent


@logged
async def observe(session: AgentSession, agent: "InterviewerAgent", answer: str, latest: str) -> None:
    """Silent Observer (§1): grades the answer off the hot path; the note shapes the NEXT reply.
    It also labels the latest turn, so a clarification doesn't use up a follow-up."""
    s = agent.s
    idx = s.idx
    q = s.question
    prompt = prompts.OBSERVER.format(
        question=q.text, signals="; ".join(q.expected_signals), answer=answer, latest=latest
    )
    jlog("Calling Observer LLM", function_name="observe", output={"question": idx + 1})
    try:
        note = await generate_json(config.OBSERVER_MODEL, prompt, ObserverNote, timeout=10, session_id=s.session_id)
    except Exception as e:
        # interview continues without the hint
        jlog("Observer failed, continuing without a note", function_name="observe", err=e, level=logging.ERROR)
        return
    jlog("Observer note", function_name="observe", output={"question": idx + 1, **note.model_dump()})
    s.notes.append({"question_idx": idx, "note": note.model_dump()})
    # The session we were given, not agent.session: that raises once a handoff has deactivated the agent.
    if s.idx == idx and session.current_agent is agent:  # still on the same question
        s.note = note.model_dump_json(exclude={"coverage", "latest_turn"})  # read by the next current_question_block
        if note.latest_turn == "clarification":
            s.clarifications += 1


def observe_latest(session: AgentSession, agent: "InterviewerAgent") -> None:
    """One Observer call per question at a time. A pause mid-answer splits it into two turns; the
    newer call grades the full text, so the older one is cancelled (its note would be stale)."""
    s = agent.s
    prev = s.observing.get(s.idx)
    if prev and not prev.done():
        prev.cancel()
        jlog("Observer call superseded by a newer turn", function_name="observe_latest", output={"question": s.idx + 1})
    s.observing[s.idx] = spawn(observe(session, agent, "\n".join(s.answers), s.answers[-1]))
