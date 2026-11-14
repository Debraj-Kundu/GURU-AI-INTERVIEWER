"""Planner agent (PLAN §1): job + question bank + KB -> interview plan that fits the time cap.
Questions and criteria are optional: with no criteria they're derived from the job description
first (derive_criteria); with no question bank the planner writes the questions from it.

Usage: uv run python -m guru.planner fixtures/job.json fixtures/kb --out fixtures/plan.json
"""

import argparse
import asyncio
import json
import re
from pathlib import Path

from . import config, llm, prompts
from .models import Criterion, DerivedCriteria, DerivedCriterion, InterviewPlan, JobRole, Plan, SkippedQuestion

MAX_GAP_QUESTIONS = 1
COMPANY_SUMMARY_CHARS = 4000
MAX_CRITERIA = 6


def fit_to_time(plan: InterviewPlan, budget_sec: int) -> InterviewPlan:
    """The LLM proposes, code enforces: drop trailing questions that don't fit the budget."""
    kept, used = [], 0
    for q in plan.questions:
        if used + q.time_budget_sec > budget_sec:
            plan.skipped_questions.append(SkippedQuestion(text=q.text, reason="did not fit the time cap"))
            continue
        kept.append(q)
        used += q.time_budget_sec
    plan.questions = kept
    return plan


def to_criteria(derived: list[DerivedCriterion]) -> list[Criterion]:
    """Turn the model's output into Criterion objects: unique ids from the names, anchors keyed
    "1".."5". A criterion without exactly 5 anchors is dropped rather than guessed at."""
    criteria: list[Criterion] = []
    for d in derived:
        if len(d.anchors) != 5:
            continue
        base = re.sub(r"[^a-z0-9]+", "_", d.name.lower()).strip("_")[:40] or "criterion"
        cid, n = base, 2
        while any(c.id == cid for c in criteria):
            cid, n = f"{base}_{n}", n + 1
        criteria.append(Criterion(id=cid, name=d.name, description=d.description, weight=d.weight,
                                  anchors={str(i): a for i, a in enumerate(d.anchors, 1)}, source=d.source))
    if not criteria:
        raise ValueError("couldn't derive any scoring criteria from the job description")
    return criteria[:MAX_CRITERIA]


async def derive_criteria(job: JobRole) -> list[Criterion]:
    out = await llm.generate_json(
        config.PLANNER_MODEL, prompts.CRITERIA.format(title=job.title, jd=job.jd_text), DerivedCriteria,
        timeout=120, session_id=f"job:{job.id}", flex=True, max_tokens=8192,
    )
    return to_criteria(out.criteria)


async def make_plan(job: JobRole, company_summary: str) -> Plan:
    """The plan, plus the job as planned: with derived criteria if the recruiter gave none. Those
    live only in the plan's copy of the job (the role itself is unchanged), so regenerating the plan
    derives them again."""
    if not job.criteria:
        job = job.model_copy(update={"criteria": await derive_criteria(job)})
    rule = prompts.PLANNER_BANK_RULE.format(n=MAX_GAP_QUESTIONS) if job.questions else prompts.PLANNER_NO_BANK_RULE
    prompt = prompts.PLANNER.format(
        questions_rule=rule,
        max_duration_sec=job.max_duration_sec,
        closing_sec=config.CLOSING_SEC,
        jd=job.jd_text,
        bank=json.dumps([q.model_dump() for q in job.questions], indent=1),
        criteria=json.dumps([c.model_dump() for c in job.criteria], indent=1),
        company=company_summary[:COMPANY_SUMMARY_CHARS],
    )
    # flex: planning runs when the recruiter saves the job, not while a candidate waits.
    plan = await llm.generate_json(
        config.PLANNER_MODEL, prompt, InterviewPlan, timeout=120, session_id=f"job:{job.id}", flex=True,
        max_tokens=16384,  # a full plan is a few thousand tokens; the default 2048 would cut it off
    )
    ids = {c.id for c in job.criteria}
    for q in plan.questions:  # a link to a criterion that doesn't exist would break scoring links
        q.linked_criteria = [c for c in q.linked_criteria if c in ids]
    plan = fit_to_time(plan, job.max_duration_sec - config.CLOSING_SEC)
    if not plan.questions:
        raise ValueError("planner produced no questions that fit the time cap")
    return Plan(job=job, plan=plan)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job", type=Path)
    ap.add_argument("kb_dir", type=Path)
    ap.add_argument("--out", type=Path, default=config.PLAN_PATH)
    args = ap.parse_args()

    job = JobRole.model_validate_json(args.job.read_text(encoding="utf-8"))
    summary = "\n\n".join(p.read_text(encoding="utf-8") for p in sorted(args.kb_dir.glob("*")))
    full = asyncio.run(make_plan(job, summary))
    args.out.write_text(full.model_dump_json(indent=2), encoding="utf-8")
    print(f"{len(full.plan.questions)} questions planned, {len(full.plan.skipped_questions)} skipped -> {args.out}")


if __name__ == "__main__":
    main()
