"""Main step: ask the planned questions, with follow-ups, then hand off to ClosingAgent."""

import asyncio
import logging
import time

from livekit.agents import Agent, ModelSettings, RunContext, function_tool, llm
from livekit.agents.llm import ToolError

from .. import config, prompts
from ..aio import spawn
from ..jsonlog import jlog, logged
from ..llm import generate_json
from ..models import OffScript
from ..phrases import DECLINED, INTRO, OFF_SCRIPT, OFF_SCRIPT_BACK
from ..session.lifecycle import run_timer
from ..states import InterviewState
from .closing import ClosingAgent
from .company_lookup import lookup_tool
from .consent import ConsentTask
from .guards import last_spoken, not_yet


def interviewer_instructions(s: InterviewState) -> str:
    """The system prompt. Fixed for the whole interview, so the provider can cache it plus
    the growing chat history; per-turn state goes in current_question_block instead."""
    p, job = s.plan.plan, s.plan.job
    return prompts.INTERVIEWER.format(
        job_title=job.title,
        company_name=job.company_name,
        allowed_tech_stack=", ".join(p.allowed_tech_stack),
        plan_json=p.model_dump_json(include={"questions"}),
    )


def current_question_block(s: InterviewState) -> str:
    """Where the interview is, added before each candidate-turn reply (turn-only, never kept)."""
    p, q = s.plan.plan, s.question
    over_time = time.monotonic() - s.question_started_at > q.time_budget_sec
    # Runs in llm_node, which LiveKit starts before it commits the candidate's message: s.answers
    # does not include the answer just given yet, and this block shapes the reply being generated now.
    # s.answers joins candidate turns split by a pause (InterviewState.record_answer), so one answer is
    # one entry. Answers the Observer labelled as clarifications don't count. ponytail: the label lands a few seconds
    # after the turn, and is lost if a quick next turn supersedes that Observer call, so the turn then
    # counts as an answer; company Q&A still counts. Upgrade: have the LLM tag follow-ups via a tool.
    follow_ups = len(s.answers) - s.clarifications
    must_advance = " Do not ask more follow-ups: call advance_to_next_question now." \
                        if s.time_up or over_time or follow_ups >= config.MAX_FOLLOW_UPS else ""

    return prompts.CURRENT_QUESTION.format(
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


async def is_off_script(s: InterviewState, last: str, turn: str) -> bool:
    """Gemini Flash-Lite, no fallback (0.6-1.4 s live; the OpenRouter model took 1.2-2 s+). Fails open (False) on error or timeout: a missed
    refusal costs less than a stalled interview, and the prompt still tells the model to stay on topic."""
    try:
        async with asyncio.timeout(config.OFF_SCRIPT_TIMEOUT_SEC):
            r = await generate_json(config.OBSERVER_ROUTE, prompts.OFF_SCRIPT.format(last=last, turn=turn),
                                    OffScript, timeout=config.OFF_SCRIPT_TIMEOUT_SEC, provider="gemini", max_tokens=200)
    except Exception as e:
        jlog("Off-script check failed, letting the reply through", function_name="is_off_script", err=e,
             level=logging.WARNING)
        return False
    return r.off_script


class InterviewerAgent(Agent):
    @logged
    def __init__(self, s: InterviewState) -> None:
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
        # Fixed text, no LLM call: the format and the first question are known. (The model also
        # skipped the format when asked to say it.) The question is read as written in the plan.
        n = len(s.plan.plan.questions)
        self.session.say(INTRO.format(
            questions="one question" if n == 1 else f"{n} questions",
            minutes=max(1, round(s.plan.job.max_duration_sec / 60)),
            question=s.question.text,
        ))

    async def llm_node(self, chat_ctx: llm.ChatContext, tools: list[llm.Tool], model_settings: ModelSettings):
        # The block goes on a copy, at call time (LiveKit docs: nodes "LLM node"). Not in
        # on_user_turn_completed: that changes the turn context, so LiveKit threw away every
        # preemptive reply, and the tool reply after advance_to_next_question kept the old
        # question's block. Here each call (preemptive, normal, tool reply) gets fresh state, and
        # the stored history, the cacheable prefix, never sees it. Gemini receives a mid-conversation
        # system message as a user turn in place (livekit.agents.llm._provider_format.utils).
        s = self.s
        last_item = chat_ctx.items[-1] if chat_ctx.items else None
        turn = last_item.text_content if last_item and last_item.type == "message" and last_item.role == "user" else None
        # Session 2e29d20d: asked six times for food, the model gave in and read a biryani recipe, and
        # advanced questions on off-script turns. A guard checks the turn in parallel with the voice LLM
        # (observer pattern, livekit.com/blog/keeping-your-agent-conversation-on-track): the model's
        # first token is held until the check returns, so it costs no latency while the check is faster.
        prev = last_spoken(self) or s.question.text
        prev = prev.removeprefix(OFF_SCRIPT).strip()  # don't stack refusals when repeating
        check = spawn(is_off_script(s, prev, turn)) if turn and s.consented else None
        chat_ctx = chat_ctx.copy()
        chat_ctx.add_message(role="system", content=current_question_block(s))
        gen = Agent.default.llm_node(self, chat_ctx, tools, model_settings)
        first = spawn(anext(gen, None))  # starts the LLM request now
        try:
            if check and await check:
                jlog("Off-script turn refused", class_name="InterviewerAgent", function_name="llm_node",
                     output={"question": s.idx + 1, "turn": turn})
                s.off_script.add(turn)
                if turn in s.answers:  # already recorded (the turn was committed before the check returned)
                    s.answers.remove(turn)
                # Yielded, not session.say(): llm_node also runs preemptively before the turn is final,
                # and say() would speak then; a yielded reply is only spoken once the turn is confirmed.
                # Any tool call the model made (advance_to_next_question) is dropped with its stream.
                yield OFF_SCRIPT_BACK.format(last=prev)
                return
            chunk = await first
            if chunk is not None:
                yield chunk
                async for chunk in gen:
                    yield chunk
        finally:
            pending = [t for t in (check, first) if t and not t.done()]
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)  # gen must be idle before aclose
            await gen.aclose()

    @function_tool
    @logged
    async def advance_to_next_question(self):
        """Move on when the current question is covered or its follow-ups are exhausted."""
        s = self.s
        if not s.answers and not s.time_up:  # no candidate turn on this question yet
            raise not_yet(s)
        if s.time_up or s.idx + 1 >= len(s.plan.plan.questions):
            jlog(
                "Handing off to ClosingAgent",
                class_name="InterviewerAgent",
                function_name="advance_to_next_question",
                output={"from_question": s.idx + 1, "time_up": s.time_up},
            )
            # Bare agent, no message: a message makes LiveKit generate a reply here AND in
            # ClosingAgent.on_enter, so the candidate heard two closings.
            return ClosingAgent(s, "time_up" if s.time_up else "completed")
        s.idx += 1
        s.answers, s.note, s.clarifications = [], "", 0
        s.question_started_at = time.monotonic()
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
    async def lookup_company_info(self, context: RunContext, question: str) -> str:
        """Look up facts about the company to answer a candidate's question.

        Args:
            question: The candidate's question about the company, rephrased as a standalone question.
        """
        return await lookup_tool(self.s, question, context)

    @function_tool
    @logged
    async def candidate_wants_to_end(self):
        """The candidate asked to stop or end the interview before all questions are done."""
        if not self.s.answers:  # the request to stop is itself a candidate turn on this question
            raise not_yet(self.s)
        jlog(
            "Candidate ended early, handing off to ClosingAgent",
            class_name="InterviewerAgent",
            function_name="candidate_wants_to_end",
            output={"at_question": self.s.idx + 1, "of": len(self.s.plan.plan.questions)},
        )
        return ClosingAgent(self.s, "candidate_ended")  # bare handoff: only ClosingAgent.on_enter speaks
