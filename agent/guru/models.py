"""Shapes shared by planner, interviewer, observer, and scorer."""

from typing import Literal

from pydantic import BaseModel, Field


class BankQuestion(BaseModel):
    text: str
    category: str = ""
    difficulty: str = ""
    expected_signals: list[str] = []


class Criterion(BaseModel):
    id: str
    name: str
    description: str
    weight: float = Field(gt=0)
    anchors: dict[str, str]  # "1".."5" -> behaviour at that level


class Job(BaseModel):
    id: str
    org_id: str
    title: str
    company_name: str
    jd_text: str
    max_duration_sec: int = Field(600, ge=300, le=3600)
    questions: list[BankQuestion]
    criteria: list[Criterion]


class PlannedQuestion(BaseModel):
    text: str
    expected_signals: list[str]
    follow_up_probes: list[str]
    time_budget_sec: int
    linked_criteria: list[str]


class SkippedQuestion(BaseModel):
    text: str
    reason: str


class InterviewPlan(BaseModel):
    questions: list[PlannedQuestion]
    skipped_questions: list[SkippedQuestion] = []
    allowed_tech_stack: list[str]


class Plan(BaseModel):
    """What the interviewer loads: the job plus the planner's output."""

    job: Job
    plan: InterviewPlan


class ObserverNote(BaseModel):
    coverage: float = Field(ge=0, le=1)
    depth: Literal["shallow", "adequate", "deep"]
    signals_hit: list[str]
    signals_missing: list[str]
    red_flags: list[str]
    suggested_followup: str | None


class CriterionScore(BaseModel):
    criterion_id: str
    score: int | None = Field(None, ge=1, le=5)  # None when evidence is insufficient
    insufficient_evidence: bool
    evidence_turns: list[int]
    rationale: str


class ScopeLabel(BaseModel):
    label: Literal["company_info", "future_projects", "tech_outside_jd", "other"]
