"""Voice interviewer worker (PLAN §3): Consent -> Interviewer -> Closing, with a silent Observer.

Run:  uv run python -m guru.interviewer dev      (connects to LiveKit Cloud as agent "interviewer")
"""

import asyncio
import json
import logging
import re
import statistics
import time
from dataclasses import dataclass, field

import httpx
from google.genai import types
from livekit import rtc
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
from livekit.agents.llm import ToolError
from livekit.plugins import deepgram, elevenlabs, google, openai, silero

from . import config, kb, prompts
from .jsonlog import MAX_VALUE_CHARS, add_file_sink, jlog, logged
from .models import ObserverNote, Plan
from .llm import generate_json, openrouter

GOODBYE = "We've reached the end of our time. Thank you for speaking with me today. Goodbye!"
DECLINED = "No problem at all. Thank you for your time, and goodbye!"
STILL_THERE = "Are you still there?"
SILENCE_GOODBYE = "Seems like there's a technical issue, try again later."
REJOINED = "The candidate just reconnected after losing the connection. Welcome them back in one short sentence, then repeat your last question."
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
    # repr=False on bulky fields keeps State short in the JSON logs
    plan: Plan = field(repr=False)
    session_id: str
    started_at: float = field(default=0.0, repr=False)
    consented: bool = False
    idx: int = 0
    answers: list[str] = field(default_factory=list, repr=False)  # candidate turns on the current question
    question_started_at: float = field(default=0.0, repr=False)
    time_up: bool = False  # set by the timer at cap - CLOSING_SEC
    note: str = field(default="", repr=False)  # latest observer note, shown to the interviewer
    notes: list[dict] = field(default_factory=list, repr=False)  # all notes, for the scorer
    latencies: list[float] = field(default_factory=list, repr=False)  # voice-to-voice, seconds
    llm_calls: int = field(default=0, repr=False)
    llm_cancelled: int = field(default=0, repr=False)  # preemptive generations thrown away (§3.5.1)
    silence_prompts: int = 0  # "Are you still there?" asked since the candidate last said something
    timer: asyncio.Task | None = field(default=None, repr=False)

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


@logged
async def lookup_tool(s: State, question: str) -> str:
    jlog("Calling company lookup (scope check + Qdrant search)", function_name="lookup_tool")
    try:
        result = await kb.lookup(s.plan.job, s.plan.plan.allowed_tech_stack, question, session_id=s.session_id)
    except Exception as e:
        # Fail closed: if the scope check or search breaks, refuse rather than guess.
        jlog("Company lookup failed, answering NO_ANSWER", function_name="lookup_tool", err=e, level=logging.ERROR)
        result = NO_ANSWER
    jlog(
        "Company lookup result",
        function_name="lookup_tool",
        output={"status": result["status"], "source_urls": result["source_urls"], "chunks": len(result["chunks"])},
    )
    return json.dumps(result)


class ConsentTask(AgentTask[bool]):
    @logged
    def __init__(self, s: State) -> None:
        super().__init__(instructions=prompts.CONSENT.format(company_name=s.plan.job.company_name))

    @logged
    async def on_enter(self) -> None:
        jlog("Asking for consent", class_name="ConsentTask", function_name="on_enter")
        self.session.generate_reply()

    @function_tool
    @logged
    async def consent_given(self) -> None:
        """The candidate clearly agreed to an AI-led, recorded interview."""
        jlog("Consent recorded", class_name="ConsentTask", function_name="consent_given", output={"consented": True})
        self.complete(True)

    @function_tool
    @logged
    async def consent_declined(self) -> None:
        """The candidate declined to continue."""
        jlog("Consent recorded", class_name="ConsentTask", function_name="consent_declined", output={"consented": False})
        self.complete(False)


class InterviewerAgent(Agent):
    @logged
    def __init__(self, s: State) -> None:
        self.s = s
        s.question_started_at = time.monotonic()
        super().__init__(instructions=interviewer_instructions(s))

    @logged
    async def on_enter(self) -> None:
        s = self.s
        try:
            s.consented = await ConsentTask(s)
        except ToolError:
            # LiveKit cancels the task when the session ends mid-consent (silence timeout, tab closed).
            jlog("Session ended before consent", class_name="InterviewerAgent", function_name="on_enter")
            return
        jlog("Consent task finished", class_name="InterviewerAgent", function_name="on_enter", output={"consented": s.consented})
        if not s.consented:
            jlog("Ending session: consent declined", class_name="InterviewerAgent", function_name="on_enter")
            await self.session.say(DECLINED, allow_interruptions=False)
            self.session.shutdown()
            return
        s.started_at = s.question_started_at = time.monotonic()
        s.timer = spawn(run_timer(self.session, s))
        jlog(
            "Starting interview",
            class_name="InterviewerAgent",
            function_name="on_enter",
            output={"questions": len(s.plan.plan.questions), "max_duration_sec": s.plan.job.max_duration_sec},
        )
        self.session.generate_reply(
            instructions=f"Briefly introduce yourself and the format (about {len(s.plan.plan.questions)} "
            f"questions), then ask the first question in your own words: {s.question.text}"
        )

    async def refresh(self) -> None:
        await self.update_instructions(interviewer_instructions(self.s))

    @function_tool
    @logged
    async def advance_to_next_question(self):
        """Move on when the current question is covered or its follow-ups are exhausted."""
        s = self.s
        if s.time_up or s.idx + 1 >= len(s.plan.plan.questions):
            jlog(
                "Handing off to ClosingAgent",
                class_name="InterviewerAgent",
                function_name="advance_to_next_question",
                output={"from_question": s.idx + 1, "time_up": s.time_up},
            )
            # Bare agent, no message: a message makes LiveKit generate a reply here AND in
            # ClosingAgent.on_enter, so the candidate heard two closings.
            return ClosingAgent(s)
        s.idx += 1
        s.answers, s.note = [], ""
        s.question_started_at = time.monotonic()
        await self.refresh()
        # Keep context small as the interview grows (§3.4); observer notes keep the summary.
        await self.update_chat_ctx(self.chat_ctx.copy().truncate(max_items=config.KEEP_CHAT_ITEMS))
        jlog(
            "Advanced to next question",
            class_name="InterviewerAgent",
            function_name="advance_to_next_question",
            output={"question": s.idx + 1, "of": len(s.plan.plan.questions), "text": s.question.text},
        )
        return f"Ask this next question now, in your own words: {s.question.text}"

    @function_tool
    @logged
    async def lookup_company_info(self, question: str) -> str:
        """Look up facts about the company to answer a candidate's question.

        Args:
            question: The candidate's question about the company, rephrased as a standalone question.
        """
        return await lookup_tool(self.s, question)

    @function_tool
    @logged
    async def candidate_wants_to_end(self):
        """The candidate asked to stop or end the interview before all questions are done."""
        jlog(
            "Candidate ended early, handing off to ClosingAgent",
            class_name="InterviewerAgent",
            function_name="candidate_wants_to_end",
            output={"at_question": self.s.idx + 1, "of": len(self.s.plan.plan.questions)},
        )
        return ClosingAgent(self.s)  # bare handoff: only ClosingAgent.on_enter speaks


class ClosingAgent(Agent):
    @logged
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

    @logged
    async def on_enter(self) -> None:
        jlog("Inviting candidate questions", class_name="ClosingAgent", function_name="on_enter")
        self.session.generate_reply()

    @function_tool
    @logged
    async def lookup_company_info(self, question: str) -> str:
        """Look up facts about the company to answer a candidate's question.

        Args:
            question: The candidate's question about the company, rephrased as a standalone question.
        """
        return await lookup_tool(self.s, question)

    @function_tool
    @logged
    async def end_interview(self) -> None:
        """End the interview after saying goodbye."""
        jlog("Shutting down session (drain)", class_name="ClosingAgent", function_name="end_interview")
        self.session.shutdown(drain=True)  # drain lets the goodbye finish playing


@logged
async def run_timer(session: AgentSession, s: State) -> None:
    """Python, not the LLM, owns the clock (§3.3)."""
    cap = s.plan.job.max_duration_sec
    await asyncio.sleep(max(0, cap - config.CLOSING_SEC))
    s.time_up = True
    jlog("Time up: next reply moves to closing", function_name="run_timer", output={"elapsed_sec": cap - config.CLOSING_SEC})
    if isinstance(session.current_agent, InterviewerAgent):
        await session.current_agent.refresh()  # next reply moves to closing; no talking over the candidate
    await asyncio.sleep(config.CLOSING_SEC)
    jlog("Hard cap reached: goodbye and shutdown", function_name="run_timer", output={"session_id": s.session_id, "cap_sec": cap})
    session.interrupt(force=True)
    await session.say(GOODBYE, allow_interruptions=False)
    session.shutdown()


@logged
async def on_silence(session: AgentSession, s: State) -> None:
    """LiveKit marks the user "away" after SILENCE_SEC with nobody speaking. Check in up to
    SILENCE_PROMPTS times, then end the interview."""
    if s.silence_prompts >= config.SILENCE_PROMPTS:
        jlog("Still silent after check-ins: ending interview", function_name="on_silence", output={"prompts": s.silence_prompts})
        await session.say(SILENCE_GOODBYE, allow_interruptions=False)
        session.shutdown()
        return
    s.silence_prompts += 1
    jlog("Silence check-in", function_name="on_silence", output={"attempt": s.silence_prompts, "of": config.SILENCE_PROMPTS})
    # "away" fires once per silence; resetting re-arms the countdown, which restarts after we speak.
    session.reset_away_timer()
    session.say(STILL_THERE)


@logged
async def observe(session: AgentSession, agent: InterviewerAgent, answer: str) -> None:
    """Silent Observer (§1): grades the answer off the hot path; the note shapes the NEXT reply."""
    s = agent.s
    idx = s.idx
    q = s.question
    prompt = prompts.OBSERVER.format(question=q.text, signals="; ".join(q.expected_signals), answer=answer)
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
        s.note = note.model_dump_json(exclude={"coverage"})
        await agent.refresh()


def build_llm() -> llm.LLM:
    if not config.OPENROUTER_API_KEY:  # same check with_openrouter() made; avoids falling back to OPENAI_API_KEY
        raise ValueError("OPENROUTER_API_KEY is required")
    llms: list[llm.LLM] = [
        # Plain LLM, not with_openrouter(), which has no max_completion_tokens.
        openai.LLM(
            model=config.OPENROUTER_MODEL,
            api_key=config.OPENROUTER_API_KEY,
            base_url="https://openrouter.ai/api/v1",
            extra_headers={"X-Title": "Guru"},
            # Explicit cache mode with no breakpoints = no caching. The instructions change almost
            # every turn, so implicit caching paid 1.25x cache writes on ~10 turns for 2 reads.
            # Re-enable if the per-turn state moves out of the instructions (stable prefix).
            extra_body={"provider": {"data_collection": "deny"}, "prompt_cache_options": {"mode": "explicit"}},
            # Reasoning was most of the output tokens, and output tokens drive latency (OpenAI latency guide).
            reasoning_effort=config.INTERVIEWER_THINKING,  # type: ignore[arg-type]  # validated by the API
            max_completion_tokens=config.VOICE_MAX_TOKENS,
        ),
        google.LLM(
            model=config.INTERVIEWER_MODEL,
            temperature=0.4,
            thinking_config=types.ThinkingConfig(
                                    thinking_level=types.ThinkingLevel(config.INTERVIEWER_THINKING.upper())),
        ),
    ]
    # Gemini rejects request deadlines under 10 s; the adapter's 5 s default fails every call.
    return llm.FallbackAdapter(llms, attempt_timeout=10.0)


def build_session(proc: JobProcess, keyterms: list[str]) -> AgentSession:
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
        llm=proc.userdata["llm"],
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
        user_away_timeout=config.SILENCE_SEC,  # drives on_silence via the "away" user state
    )


def is_session_id(metadata: str | None) -> bool:
    """Backend dispatches carry the session id (32 hex chars); console/dev runs don't."""
    return bool(metadata and re.fullmatch(r"[0-9a-f]{32}", metadata))


@logged
async def load_plan(metadata: str | None, room: str) -> Plan | None:
    """Claims the session's one attempt and returns its plan; None if it already started or ended."""
    if not is_session_id(metadata):
        jlog("No session in dispatch metadata: using the fixture plan", function_name="load_plan", output={"path": str(config.PLAN_PATH)})
        return Plan.model_validate_json(config.PLAN_PATH.read_text(encoding="utf-8"))
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            f"{config.API_URL}/internal/sessions/{metadata}/start",
            json={"room": room},
            headers={"X-Service-Token": config.SERVICE_TOKEN},
        )
        if r.status_code == 409:
            return None
        r.raise_for_status()
    return Plan.model_validate(r.json())


def build_results(session: AgentSession, s: State) -> dict:
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
        },
        "llm_calls": s.llm_calls,
        "llm_cancelled": s.llm_cancelled,
        "usage": [u.model_dump() for u in session.usage.model_usage],
    }


@logged
async def post_results(s: State, out: dict | None) -> None:
    """Send results to the backend, which stores them and starts scoring. Declined consent sends
    only consented=false, so nothing is kept. The local JSON from save_transcript is the backup."""
    # ponytail: direct HTTP call with one try; PLAN §4.4 routes this through RabbitMQ so a backend
    # outage never loses an interview. Until then the out/<session>.json file is the fallback.
    body = {"consented": False}
    if out is not None:
        usage = {k: out[k] for k in ("latency", "llm_calls", "llm_cancelled", "usage")}
        body = {"consented": True, "turns": out["turns"], "notes": out["notes"], "usage": usage,
                "duration_sec": out["duration_sec"]}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{config.API_URL}/internal/sessions/{s.session_id}/complete",
            json=body,
            headers={"X-Service-Token": config.SERVICE_TOKEN},
        )
        r.raise_for_status()
    jlog("Results sent to backend", function_name="post_results", output={"session_id": s.session_id, "consented": body["consented"]})


@logged
def save_transcript(session: AgentSession, s: State) -> dict | None:
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
def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()
    proc.userdata["llm"] = build_llm()  # Gemini client setup blocks ~3 s; keep it off the join path
    kb.bm25()  # load the BM25 model before the first candidate question
    openrouter()  # building its SSL context blocked the loop ~200 ms on the first Observer call


server = AgentServer(setup_fnc=prewarm)


@server.rtc_session(agent_name="interviewer")
@logged
async def entrypoint(ctx: JobContext) -> None:
    # The backend's token dispatches us with the session id as metadata; we fetch that session's plan.
    plan = await load_plan(ctx.job.metadata, ctx.room.name)
    if plan is None:
        # One attempt per interview: it already started (or ended) in another room. Report nothing,
        # so the real attempt's result stands; deleting the room tells the page it's over.
        jlog("Interview already started or ended: leaving", function_name="entrypoint", output={"room": ctx.room.name})
        await ctx.delete_room()
        ctx.shutdown("interview already started")
        return
    s = State(plan=plan, session_id=ctx.job.metadata or ctx.room.name)
    session = build_session(ctx.proc, keyterms=plan.plan.allowed_tech_stack[:50])
    agent = InterviewerAgent(s)
    user_stopped_at: float | None = None

    @session.on("user_state_changed")
    def _user_state(ev):
        nonlocal user_stopped_at
        if ev.old_state == "speaking" and ev.new_state == "listening":
            user_stopped_at = time.monotonic()
        if ev.new_state == "away":
            spawn(on_silence(session, s))

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

    @session.on("function_tools_executed")
    def _tools(ev):
        # Tool calls aren't conversation messages, so they get their own event; calls and outputs are parallel lists.
        for call, result in zip(ev.function_calls, ev.function_call_outputs):
            jlog(
                "Tool called",
                class_name=type(session.current_agent).__name__,
                function_name="function_tools_executed",
                output={
                    "role": "tool",
                    "question": s.idx + 1,
                    "tool": call.name,
                    "arguments": call.arguments,
                    "result": result.output[:MAX_VALUE_CHARS],  # lookup results carry KB chunks; keep the line short
                    "is_error": result.is_error,
                },
                level=logging.ERROR if result.is_error else logging.INFO,
            )

    @session.on("conversation_item_added")
    def _item(ev):
        item = ev.item
        role = getattr(item, "role", None)
        if role in ("user", "assistant"):  # one line per turn; the bot's speech never passes through our functions
            jlog(
                "Agent said" if role == "assistant" else "Candidate said",
                class_name=type(session.current_agent).__name__,
                function_name="conversation_item_added",
                output={"role": role, "question": s.idx + 1, "text": item.text_content},
            )
        if role == "user":
            s.silence_prompts = 0  # any real input (voice or text) restarts the check-in count
        if role != "user" or not s.consented:
            return
        if session.current_agent is not agent:
            return  # consent/closing turns are not graded
        s.answers.append(item.text_content or "")
        spawn(agent.refresh())  # follow-up count for the next reply
        spawn(observe(session, agent, "\n".join(s.answers)))

    @session.on("close")
    def _closed(ev):
        jlog(
            "Session closed",
            function_name="entrypoint",
            output={"reason": str(ev.reason), "error": str(ev.error) if ev.error else None},
        )

    @logged
    async def _on_shutdown() -> None:
        if s.timer:
            s.timer.cancel()
        out = save_transcript(session, s)
        if is_session_id(s.session_id):  # console runs have no backend session to report to
            try:
                await post_results(s, out)
            except Exception:
                pass  # @logged already logged it; the local JSON (if consented) is the backup

    ctx.add_shutdown_callback(_on_shutdown)

    # A candidate who drops (network, closed tab, reload) can rejoin the same room for
    # RECONNECT_GRACE_SEC; after that the interview ends and can't be retaken.
    grace: asyncio.Task | None = None

    async def _end_if_not_back() -> None:
        await asyncio.sleep(config.RECONNECT_GRACE_SEC)
        jlog("Candidate did not reconnect: ending the interview", function_name="entrypoint")
        session.shutdown()

    @ctx.room.on("participant_disconnected")
    def _left(p: rtc.RemoteParticipant):
        nonlocal grace
        if ctx.room.remote_participants or grace:
            return
        jlog(
            "Candidate disconnected: waiting for a rejoin",
            function_name="entrypoint",
            output={"reason": rtc.DisconnectReason.Name(p.disconnect_reason or rtc.DisconnectReason.UNKNOWN_REASON), "grace_sec": config.RECONNECT_GRACE_SEC},
        )
        grace = spawn(_end_if_not_back())

    @ctx.room.on("participant_connected")
    def _back(p: rtc.RemoteParticipant):
        nonlocal grace
        if not grace:
            return
        grace.cancel()
        grace = None
        jlog("Candidate reconnected", function_name="entrypoint")
        session.generate_reply(instructions=REJOINED)

    await session.start(
        agent,
        room=ctx.room,
        # We end the session ourselves when the candidate doesn't come back (above); the room is
        # deleted on close to free minutes and to tell the page the interview is over.
        room_options=room_io.RoomOptions(close_on_disconnect=False, delete_room_on_close=True),
        record=False,  # no LiveKit Cloud recording before consent; Phase 2 starts Egress after consent
    )
    jlog(
        "Session started",
        function_name="entrypoint",
        output={"session_id": s.session_id, "job_id": plan.job.id, "questions": len(plan.plan.questions)},
    )


if __name__ == "__main__":
    # Sinks: LiveKit's CLI sets up the console; we add the file. Main process only (see add_file_sink).
    add_file_sink(config.LOG_FILE)
    cli.run_app(server)
