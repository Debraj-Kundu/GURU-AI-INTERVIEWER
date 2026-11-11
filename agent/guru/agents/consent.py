"""First step: the candidate agrees (or not) to an AI-led, recorded interview."""

from livekit.agents import AgentTask, function_tool

from .. import prompts
from ..jsonlog import jlog, logged
from ..phrases import CONSENT_ASK
from ..states import InterviewState
from .guards import candidate_replied, not_yet


class ConsentTask(AgentTask[bool]):
    """Runs inside InterviewerAgent.on_enter and returns True/False. Its conversation (not its tool
    calls) is merged into the interviewer's history when it completes."""

    @logged
    def __init__(self, s: InterviewState) -> None:
        self.s = s
        super().__init__(instructions=prompts.CONSENT.format(company_name=s.plan.job.company_name))

    @logged
    async def on_enter(self) -> None:
        jlog("Asking for consent", class_name="ConsentTask", function_name="on_enter")
        # Fixed text, no LLM call. Not interruptible: an early "yes" mustn't cut off the disclosure.
        # say() adds it to this task's history, so the model sees what was asked when it judges the reply.
        self.session.say(CONSENT_ASK.format(company_name=self.s.plan.job.company_name), allow_interruptions=False)

    @function_tool
    @logged
    async def consent_given(self) -> None:
        """The candidate clearly agreed to an AI-led, recorded interview."""
        if not candidate_replied(self):
            raise not_yet(self.s)
        jlog("Consent recorded", class_name="ConsentTask", function_name="consent_given", output={"consented": True})
        self.complete(True)

    @function_tool
    @logged
    async def consent_declined(self) -> None:
        """The candidate declined to continue."""
        if not candidate_replied(self):
            raise not_yet(self.s)
        jlog("Consent recorded", class_name="ConsentTask", function_name="consent_declined", output={"consented": False})
        self.complete(False)
