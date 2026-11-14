"""Main step: ask the planned questions, with follow-ups, then hand off to ClosingAgent."""

import time

from livekit.agents import Agent, ModelSettings, RunContext, function_tool, llm
from livekit.agents.llm import ToolError

from .. import config, prompts
from ..aio import spawn
from ..jsonlog import jlog, logged
from ..phrases import DECLINED, INTRO
from ..session.lifecycle import run_timer
from ..states import InterviewState
from .closing import ClosingAgent
from .company_lookup import lookup_tool
from .consent import ConsentTask
from .guards import not_yet


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
        chat_ctx = chat_ctx.copy()
        chat_ctx.add_message(role="system", content=current_question_block(self.s))
        async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
            yield chunk

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
