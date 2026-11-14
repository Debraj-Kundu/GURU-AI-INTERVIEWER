"""LiveKit session and room event handlers for one interview: logging, metrics, silence, the
Observer trigger, and reconnects. Each handler only updates InterviewState or starts a background
task, so nothing here blocks the conversation."""

import asyncio
import logging
import time

from livekit import rtc
from livekit.agents import AgentSession, JobContext, metrics

from .. import config
from ..agents import ClosingAgent, InterviewerAgent
from ..aio import spawn
from ..jsonlog import MAX_VALUE_CHARS, jlog
from ..agents.guards import last_spoken
from ..phrases import REJOINED, REJOINED_CLOSING
from ..states import InterviewState
from .lifecycle import on_silence
from .observer import observe_latest


class SessionEvents:
    def __init__(self, ctx: JobContext, session: AgentSession, s: InterviewState, agent: InterviewerAgent) -> None:
        self.ctx, self.session, self.s, self.agent = ctx, session, s, agent
        self._user_stopped_at: float | None = None  # for voice-to-voice latency
        # A candidate who drops (network, closed tab, reload) can rejoin the same room for
        # RECONNECT_GRACE_SEC; after that the interview ends and can't be retaken.
        self._grace: asyncio.Task | None = None

    def attach(self) -> None:
        on = self.session.on
        on("user_state_changed", self._user_state)
        on("agent_state_changed", self._agent_state)
        on("metrics_collected", self._metrics)
        on("function_tools_executed", self._tools)
        on("conversation_item_added", self._item)
        on("close", self._closed)
        self.ctx.room.on("participant_disconnected", self._left)
        self.ctx.room.on("participant_connected", self._back)

    # ---- session events ----

    def _user_state(self, ev) -> None:
        if ev.old_state == "speaking" and ev.new_state == "listening":
            self._user_stopped_at = time.monotonic()
        if ev.new_state == "away":
            spawn(on_silence(self.session, self.s))

    def _agent_state(self, ev) -> None:
        s = self.s
        if ev.new_state == "listening" and ev.old_state in ("thinking", "speaking"):
            s.awaiting_reply = False  # the agent's turn ended, spoken or silent (not the start-up "listening")
        if ev.new_state == "speaking" and s.first_turn_sec is None:
            s.first_turn_sec = round(time.monotonic() - s.joined_at, 2)
            jlog("First agent audio", function_name="entrypoint", output={"first_turn_sec": s.first_turn_sec})
        if ev.new_state == "speaking" and self._user_stopped_at is not None:
            s.latencies.append(time.monotonic() - self._user_stopped_at)
            self._user_stopped_at = None

    def _metrics(self, ev) -> None:
        metrics.log_metrics(ev.metrics)
        if isinstance(ev.metrics, metrics.LLMMetrics):
            self.s.llm_calls += 1
            self.s.llm_cancelled += ev.metrics.cancelled

    def _tools(self, ev) -> None:
        # Tool calls aren't conversation messages, so they get their own event; calls and outputs are parallel lists.
        for call, result in zip(ev.function_calls, ev.function_call_outputs):
            jlog(
                "Tool called",
                class_name=type(self.session.current_agent).__name__,
                function_name="function_tools_executed",
                output={
                    "role": "tool",
                    "question": self.s.idx + 1,
                    "tool": call.name,
                    "arguments": call.arguments,
                    "result": result.output[:MAX_VALUE_CHARS],  # lookup results carry KB chunks; keep the line short
                    "is_error": result.is_error,
                },
                level=logging.ERROR if result.is_error else logging.INFO,
            )

    def _item(self, ev) -> None:
        s, session = self.s, self.session
        item = ev.item
        role = getattr(item, "role", None)
        if role in ("user", "assistant"):  # one line per turn; the bot's speech never passes through our functions
            jlog(
                "Agent said" if role == "assistant" else "Candidate said",
                class_name=type(session.current_agent).__name__,
                function_name="conversation_item_added",
                output={"role": role, "question": s.idx + 1, "text": item.text_content},
            )
        if role == "assistant":
            s.interviewer_spoke = True
        if role == "user":
            s.silence_prompts = 0  # any real input (voice or text) restarts the check-in count
            s.awaiting_reply = True
        if role != "user" or not s.consented:
            return
        if session.current_agent is not self.agent:
            return  # consent/closing turns are not graded
        s.record_answer(item.text_content or "")
        observe_latest(session, self.agent)

    def _closed(self, ev) -> None:
        jlog(
            "Session closed",
            function_name="entrypoint",
            output={"reason": str(ev.reason), "error": str(ev.error) if ev.error else None},
        )

    # ---- room events: drop and rejoin ----

    def _left(self, p: rtc.RemoteParticipant) -> None:
        room, session = self.ctx.room, self.session
        # ROOM_DELETED: we (or LiveKit) deleted the room, so there's nothing to rejoin.
        if room.remote_participants or self._grace or p.disconnect_reason == rtc.DisconnectReason.ROOM_DELETED:
            return
        if isinstance(session.current_agent, ClosingAgent):
            # The questions are done; leaving during the wrap-up means they're finished, not dropped.
            jlog("Candidate left during closing: ending the interview", function_name="entrypoint")
            session.shutdown(drain=False)
            return
        jlog(
            "Candidate disconnected: waiting for a rejoin",
            function_name="entrypoint",
            output={"reason": rtc.DisconnectReason.Name(p.disconnect_reason or rtc.DisconnectReason.UNKNOWN_REASON), "grace_sec": config.RECONNECT_GRACE_SEC},
        )
        self._grace = spawn(self._end_if_not_back())

    def _back(self, p: rtc.RemoteParticipant) -> None:
        if not self._grace:
            return
        self._grace.cancel()
        self._grace = None
        jlog("Candidate reconnected", function_name="entrypoint")
        # Fixed text, no LLM call: repeat the agent's last full line word for word (it may be a
        # follow-up, not the planned question); if that line was cut off, fall back to the question.
        current = self.session.current_agent
        if isinstance(current, ClosingAgent):
            self.session.say(REJOINED_CLOSING)
        else:
            self.session.say(REJOINED.format(last=last_spoken(current) or self.s.question.text))

    async def _end_if_not_back(self) -> None:
        await asyncio.sleep(config.RECONNECT_GRACE_SEC)
        jlog("Candidate did not reconnect: ending the interview", function_name="entrypoint")
        self.s.ending = "disconnected"
        self.session.shutdown()
