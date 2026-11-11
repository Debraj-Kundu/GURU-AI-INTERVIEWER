"""Everything one interview remembers while it runs. The agents, the observer, the timers and the
session event handlers all read and update this one object, which is passed in rather than global."""

import asyncio
from dataclasses import dataclass, field

from ..models import Plan


@dataclass
class InterviewState:
    # repr=False on bulky fields keeps the state short in the JSON logs
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
    # The agent owes a reply: from join until its first turn ends, and from each candidate turn until
    # the agent's next turn ends. Silence doesn't count meanwhile (the agent is the one being slow).
    awaiting_reply: bool = True
    clarifications: int = 0  # candidate turns on the current question the Observer labelled as asking about it
    # Outcome metrics for the results (production guide: count outcomes, not just latency).
    joined_at: float = field(default=0.0, repr=False)
    first_turn_sec: float | None = None  # job received to the agent's first audio; most hang-ups happen here
    ending: str = ""  # completed / time_up / candidate_ended / silence / hard_cap / disconnected
    silence_checkins: int = 0  # "Are you still there?" over the whole interview
    guard_refusals: int = 0  # tool calls refused because the candidate hadn't replied (model skipping ahead)
    timer: asyncio.Task | None = field(default=None, repr=False)
    observing: dict[int, asyncio.Task] = field(default_factory=dict, repr=False)  # running Observer call per question

    @property
    def question(self):
        return self.plan.plan.questions[self.idx]
