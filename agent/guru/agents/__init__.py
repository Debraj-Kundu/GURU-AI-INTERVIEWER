"""The interview's conversation steps, one LiveKit agent per file, in the order a candidate meets them:

    ConsentTask (consent.py) -> InterviewerAgent (interviewer.py) -> ClosingAgent (closing.py)

Each agent is one state of the conversation, with its own prompt and tools; a tool returning the next
agent is the transition (LiveKit handoff). All of them share one InterviewState, passed in.
guards.py and company_lookup.py hold what more than one agent uses.
"""

from .closing import ClosingAgent
from .consent import ConsentTask
from .interviewer import InterviewerAgent

__all__ = ["ClosingAgent", "ConsentTask", "InterviewerAgent"]
