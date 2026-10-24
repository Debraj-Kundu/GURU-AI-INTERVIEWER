"""Voice interviewer worker (PLAN §3): Consent -> Interviewer -> Closing, with a silent Observer.

Run:  uv run python -m guru.interviewer dev      (connects to LiveKit Cloud as agent "interviewer")
"""

import asyncio
import json
import logging
import statistics
import time
from dataclasses import dataclass, field

from google.genai import types
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    AgentTask,
    JobContext,
    JobProcess,
    TurnHandlingOptions,
    cli,
    function_tool,
    llm,
    metrics,
    room_io,
)
from livekit.plugins import deepgram, elevenlabs, google, silero

from . import config, kb, prompts
from .models import ObserverNote, Plan
from .llm import generate_json

log = logging.getLogger("guru.interviewer")

GOODBYE = "We've reached the end of our time. Thank you for speaking with me today. Goodbye!"
DECLINED = "No problem at all. Thank you for your time, and goodbye!"
NO_ANSWER = {"status": "NO_ANSWER", "chunks": [], "source_urls": []}

_background: set[asyncio.Task] = set()


def spawn(coro) -> asyncio.Task:
    """create_task that keeps a reference, so the task isn't garbage-collected mid-flight."""
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task


@dataclass
class State:
    plan: Plan
    session_id: str
    started_at: float = 0.0
    consented: bool = False
    idx: int = 0
    answers: list[str] = field(default_factory=list)  # candidate turns on the current question
    question_started_at: float = 0.0
    time_up: bool = False  # set by the timer at cap - CLOSING_SEC
    note: str = ""  # latest observer note, shown to the interviewer
    notes: list[dict] = field(default_factory=list)  # all notes, for the scorer
    latencies: list[float] = field(default_factory=list)  # voice-to-voice, seconds
    llm_calls: int = 0
    llm_cancelled: int = 0  # preemptive generations thrown away (cost visibility, §3.5.1)
    timer: asyncio.Task | None = None

    @property
    def question(self):
        return self.plan.plan.questions[self.idx]


def interviewer_instructions(s: State) -> str:
    p, job = s.plan.plan, s.plan.job
    static = prompts.INTERVIEWER.format(
        job_title=job.title,
        company_name=job.company_name,
        allowed_tech_stack=", ".join(p.allowed_tech_stack),
        plan_json=p.model_dump_json(include={"questions"}),
    )
    q = s.question
    over_time = time.monotonic() - s.question_started_at > q.time_budget_sec
    # These instructions take effect from the reply AFTER the one already being generated
    # (it may have started preemptively), so count that reply as a follow-up already.
    # ponytail: assumes every interviewer reply on a question is a follow-up; clarifications
    # and company Q&A also count. Upgrade: have the LLM tag follow-ups via a tool if it matters.
    follow_ups = len(s.answers)
    must_advance = ""
    if s.time_up or over_time or follow_ups >= config.MAX_FOLLOW_UPS:
        must_advance = " Do not ask more follow-ups: call advance_to_next_question now."
    return static + prompts.CURRENT_QUESTION.format(
        number=s.idx + 1,
        total=len(p.questions),
        text=q.text,
        signals="; ".join(q.expected_signals),
        probes="; ".join(q.follow_up_probes),
        follow_ups_used=follow_ups,
        max_follow_ups=config.MAX_FOLLOW_UPS,
        must_advance=must_advance,
        observer_note=prompts.OBSERVER_NOTE.format(note=s.note) if s.note else "",
    )


async def lookup_tool(s: State, question: str) -> str:
    try:
        result = await kb.lookup(s.plan.job, s.plan.plan.allowed_tech_stack, question)
    except Exception:
        # Fail closed: if the scope check or search breaks, refuse rather than guess.
        log.exception("company lookup failed")
        result = NO_ANSWER
    return json.dumps(result)


class ConsentTask(AgentTask[bool]):
    def __init__(self, s: State) -> None:
        super().__init__(instructions=prompts.CONSENT.format(company_name=s.plan.job.company_name))

    async def on_enter(self) -> None:
        self.session.generate_reply()

    @function_tool
    async def consent_given(self) -> None:
        """The candidate clearly agreed to an AI-led, recorded interview."""
        self.complete(True)

    @function_tool
    async def consent_declined(self) -> None:
        """The candidate declined to continue."""
        self.complete(False)


class InterviewerAgent(Agent):
    def __init__(self, s: State) -> None:
        self.s = s
        s.question_started_at = time.monotonic()
        super().__init__(instructions=interviewer_instructions(s))

    async def on_enter(self) -> None:
        s = self.s
        s.consented = await ConsentTask(s)
        if not s.consented:
            await self.session.say(DECLINED, allow_interruptions=False)
            self.session.shutdown()
            return
        s.started_at = s.question_started_at = time.monotonic()
        s.timer = spawn(run_timer(self.session, s))
        self.session.generate_reply(
            instructions=f"Briefly introduce yourself and the format (about {len(s.plan.plan.questions)} "
            f"questions), then ask the first question in your own words: {s.question.text}"
        )

    async def refresh(self) -> None:
        await self.update_instructions(interviewer_instructions(self.s))

    @function_tool
    async def advance_to_next_question(self):
        """Move on when the current question is covered or its follow-ups are exhausted."""
        s = self.s
        if s.time_up or s.idx + 1 >= len(s.plan.plan.questions):
            return ClosingAgent(s), "Moving to the closing part of the interview."
        s.idx += 1
        s.answers, s.note = [], ""
        s.question_started_at = time.monotonic()
        await self.refresh()
        # Keep context small as the interview grows (§3.4); observer notes keep the summary.
        await self.update_chat_ctx(self.chat_ctx.copy().truncate(max_items=config.KEEP_CHAT_ITEMS))
        return f"Ask this next question now, in your own words: {s.question.text}"

    @function_tool
    async def lookup_company_info(self, question: str) -> str:
        """Look up facts about the company to answer a candidate's question.

        Args:
            question: The candidate's question about the company, rephrased as a standalone question.
        """
        return await lookup_tool(self.s, question)


class ClosingAgent(Agent):
    def __init__(self, s: State) -> None:
        self.s = s
        job = s.plan.job
        super().__init__(
            instructions=prompts.CLOSING.format(
                job_title=job.title,
                company_name=job.company_name,
                allowed_tech_stack=", ".join(s.plan.plan.allowed_tech_stack),
            )
        )

    async def on_enter(self) -> None:
        self.session.generate_reply()

    @function_tool
    async def lookup_company_info(self, question: str) -> str:
        """Look up facts about the company to answer a candidate's question.

        Args:
            question: The candidate's question about the company, rephrased as a standalone question.
        """
        return await lookup_tool(self.s, question)

    @function_tool
    async def end_interview(self) -> None:
        """End the interview after saying goodbye."""
        self.session.shutdown(drain=True)  # drain lets the goodbye finish playing


async def run_timer(session: AgentSession, s: State) -> None:
    """Python, not the LLM, owns the clock (§3.3)."""
    cap = s.plan.job.max_duration_sec
    await asyncio.sleep(max(0, cap - config.CLOSING_SEC))
    s.time_up = True
    if isinstance(session.current_agent, InterviewerAgent):
        await session.current_agent.refresh()  # next reply moves to closing; no talking over the candidate
    await asyncio.sleep(config.CLOSING_SEC)
    log.info("hard cap reached", extra={"session_id": s.session_id})
    session.interrupt(force=True)
    await session.say(GOODBYE, allow_interruptions=False)
    session.shutdown()


async def observe(agent: InterviewerAgent, answer: str) -> None:
    """Silent Observer (§1): grades the answer off the hot path; the note shapes the NEXT reply."""
    s = agent.s
    idx = s.idx
    q = s.question
    prompt = prompts.OBSERVER.format(question=q.text, signals="; ".join(q.expected_signals), answer=answer)
    try:
        note = await generate_json(config.OBSERVER_MODEL, prompt, ObserverNote, timeout=10)
    except Exception:
        log.exception("observer failed")  # interview continues without the hint
        return
    s.notes.append({"question_idx": idx, "note": note.model_dump()})
    if s.idx == idx and agent.session.current_agent is agent:  # still on the same question
        s.note = note.model_dump_json(exclude={"coverage"})
        await agent.refresh()


def build_session(proc: JobProcess, keyterms: list[str]) -> AgentSession:
    llms: list[llm.LLM] = [
        google.LLM(
            model=config.INTERVIEWER_MODEL,
            temperature=0.4,
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel(config.INTERVIEWER_THINKING.upper())),
        ),
        google.LLM(model=config.FALLBACK_MODEL, temperature=0.4),
    ]
    return AgentSession(
        stt=deepgram.STTv2(
            model="flux-general-en",
            audio_chunk_duration_ms=80,  # Deepgram's recommendation for Flux
            eot_threshold=config.EOT_THRESHOLD,
            eager_eot_threshold=config.EAGER_EOT_THRESHOLD,  # enables preemptive generation
            eot_timeout_ms=config.EOT_TIMEOUT_MS,
            keyterm=keyterms,
            mip_opt_out=True,  # keep candidate audio out of Deepgram's model-improvement program
        ),
        llm=llm.FallbackAdapter(llms),
        tts=elevenlabs.TTS(
            model="eleven_flash_v2_5",  # the plugin default (turbo_v2_5) is deprecated
            voice_id=config.ELEVENLABS_VOICE_ID or elevenlabs.DEFAULT_VOICE_ID,
        ),
        vad=proc.userdata["vad"],
        turn_handling=TurnHandlingOptions(
            turn_detection="stt",
            endpointing={"min_delay": config.ENDPOINTING_MIN_DELAY},
            preemptive_generation={
                "enabled": True,
                "preemptive_tts": False,  # never synthesise or speak before the turn is confirmed
                "max_speech_duration": config.PREEMPT_MAX_SPEECH_SEC,
                "max_retries": config.PREEMPT_MAX_RETRIES,
            },
        ),
        user_away_timeout=7.0,
    )


def save_transcript(session: AgentSession, s: State) -> None:
    """Phase 1 writes JSON; Phase 2 publishes interview.completed to RabbitMQ instead."""
    if not s.consented:
        return  # declined consent: nothing is kept
    turns = [
        {"idx": i, "speaker": "candidate" if m.role == "user" else "interviewer", "text": m.text_content}
        for i, m in enumerate(
            m for m in session.history.items if m.type == "message" and m.role in ("user", "assistant")
        )
    ]
    lat = sorted(s.latencies)
    out = {
        "session_id": s.session_id,
        "job_id": s.plan.job.id,
        "duration_sec": round(time.monotonic() - s.started_at, 1),
        "turns": turns,
        "notes": s.notes,
        "latency": {
            "turns": len(lat),
            "p50": round(statistics.median(lat), 3) if lat else None,
            "p95": round(lat[int(0.95 * (len(lat) - 1))], 3) if lat else None,
        },
        "llm_calls": s.llm_calls,
        "llm_cancelled": s.llm_cancelled,
        "usage": [u.model_dump() for u in session.usage.model_usage],
    }
    config.OUT_DIR.mkdir(exist_ok=True)
    path = config.OUT_DIR / f"{s.session_id}.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    log.info("transcript saved", extra={"path": str(path), "latency": out["latency"]})


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()
    kb.bm25()  # load the BM25 model before the first candidate question


server = AgentServer(setup_fnc=prewarm)


@server.rtc_session(agent_name="interviewer")
async def entrypoint(ctx: JobContext) -> None:
    # Phase 2: metadata carries session_id and the plan comes from the internal API.
    plan = Plan.model_validate_json(config.PLAN_PATH.read_text(encoding="utf-8"))
    s = State(plan=plan, session_id=ctx.job.metadata or ctx.room.name)
    session = build_session(ctx.proc, keyterms=plan.plan.allowed_tech_stack[:50])
    agent = InterviewerAgent(s)
    user_stopped_at: float | None = None

    @session.on("user_state_changed")
    def _user_state(ev):
        nonlocal user_stopped_at
        if ev.old_state == "speaking" and ev.new_state == "listening":
            user_stopped_at = time.monotonic()

    @session.on("agent_state_changed")
    def _agent_state(ev):
        nonlocal user_stopped_at
        if ev.new_state == "speaking" and user_stopped_at is not None:
            s.latencies.append(time.monotonic() - user_stopped_at)
            user_stopped_at = None

    @session.on("metrics_collected")
    def _metrics(ev):
        metrics.log_metrics(ev.metrics)
        if isinstance(ev.metrics, metrics.LLMMetrics):
            s.llm_calls += 1
            s.llm_cancelled += ev.metrics.cancelled

    @session.on("conversation_item_added")
    def _item(ev):
        item = ev.item
        if getattr(item, "role", None) != "user" or not s.consented:
            return
        if session.current_agent is not agent:
            return  # consent/closing turns are not graded
        s.answers.append(item.text_content or "")
        spawn(agent.refresh())  # follow-up count for the next reply
        spawn(observe(agent, "\n".join(s.answers)))

    async def _on_shutdown() -> None:
        if s.timer:
            s.timer.cancel()
        save_transcript(session, s)

    ctx.add_shutdown_callback(_on_shutdown)

    await session.start(
        agent,
        room=ctx.room,
        # Closing the tab ends the session (stops STT/LLM/TTS spend); room is deleted to free minutes.
        room_options=room_io.RoomOptions(close_on_disconnect=True, delete_room_on_close=True),
        record=False,  # no LiveKit Cloud recording before consent; Phase 2 starts Egress after consent
    )


if __name__ == "__main__":
    cli.run_app(server)
