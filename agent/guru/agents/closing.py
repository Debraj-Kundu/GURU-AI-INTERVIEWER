"""Last step: thank the candidate, answer their company questions, say goodbye."""

from livekit.agents import Agent, AgentSession, RunContext, function_tool

from .. import prompts
from ..aio import spawn
from ..jsonlog import jlog, logged
from ..phrases import CLOSING_GOODBYE, CLOSING_OPENERS
from ..states import InterviewState
from .company_lookup import lookup_tool
from .guards import candidate_replied, not_yet

# The closing agent starts with an empty chat history, so it's told how the interview ended;
# otherwise a candidate who quit at question 2 is thanked as if they'd finished.
ENDINGS = {
    "completed": "the candidate answered every planned question.",
    "time_up": "the time limit was reached before every planned question was asked. Don't apologise at length.",
    "candidate_ended": "the candidate asked to stop early. Respect that: don't ask why or try to continue the interview.",
}


class ClosingAgent(Agent):
    @logged
    def __init__(self, s: InterviewState, ending: str) -> None:
        self.s = s
        s.ending = ending
        job = s.plan.job
        super().__init__(
            instructions=prompts.CLOSING.format(
                job_title=job.title,
                company_name=job.company_name,
                ending=ENDINGS[ending],
                allowed_tech_stack=", ".join(s.plan.plan.allowed_tech_stack),
            )
        )

    @logged
    async def on_enter(self) -> None:
        jlog("Inviting candidate questions", class_name="ClosingAgent", function_name="on_enter")
        self.session.say(CLOSING_OPENERS[self.s.ending])  # fixed per ending, no LLM call

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
    async def end_interview(self) -> None:
        """Say goodbye and end the interview. Call it once the candidate has no more questions."""
        if not candidate_replied(self):  # ClosingAgent starts with an empty history
            raise not_yet(self.s)
        jlog("Saying goodbye, then shutting down", class_name="ClosingAgent", function_name="end_interview")
        # The goodbye is spoken here, not by the model: a model that said goodbye but skipped this
        # call left the session open, so the candidate who left was offered a rejoin. Not awaited:
        # the speech is queued behind this tool call, so awaiting it in here would wait on itself.
        spawn(say_goodbye_and_close(self.session))


async def say_goodbye_and_close(session: AgentSession) -> None:
    await session.say(CLOSING_GOODBYE, allow_interruptions=False)
    session.shutdown()
