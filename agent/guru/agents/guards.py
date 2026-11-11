"""Checks in code that a tool may run now, instead of trusting the model to wait (production guide:
validate in code, not in the prompt)."""

from livekit.agents import Agent
from livekit.agents.llm import ToolError

from ..states import InterviewState

NOT_YET = "The candidate hasn't replied yet. Say nothing more now; wait for their reply."


def candidate_replied(agent: Agent) -> bool:
    """Whether the candidate has said anything in this agent's conversation. The model sometimes asks
    a question and calls the tool that should follow the answer in the same response (session
    cd2fde9e, reasoning off: consent, both questions and the handoff in 25 s, no candidate turn)."""
    return any(i.type == "message" and i.role == "user" for i in agent.chat_ctx.items)


def last_spoken(agent: Agent) -> str | None:
    """The agent's last line the candidate heard in full, for repeating it word for word. None if
    there's none, or if it was cut off (an interrupted line holds only the part already spoken)."""
    for item in reversed(agent.chat_ctx.items):
        if item.type == "message" and item.role == "assistant":
            return None if item.interrupted else item.text_content
    return None


def not_yet(s: InterviewState) -> ToolError:
    s.guard_refusals += 1  # counted: a rising rate means the model keeps trying to skip ahead
    return ToolError(NOT_YET)
