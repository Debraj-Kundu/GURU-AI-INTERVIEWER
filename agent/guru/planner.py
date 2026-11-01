"""Planner agent (PLAN §1): job + question bank + KB -> interview plan that fits the time cap.

Usage: uv run python -m guru.planner fixtures/job.json fixtures/kb --out fixtures/plan.json
"""

import argparse
import asyncio
import json
from pathlib import Path

from . import config, llm, prompts
from .models import InterviewPlan, Job, Plan, SkippedQuestion

MAX_GAP_QUESTIONS = 1
COMPANY_SUMMARY_CHARS = 4000


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


async def make_plan(job: Job, company_summary: str) -> InterviewPlan:
    prompt = prompts.PLANNER.format(
        max_duration_sec=job.max_duration_sec,
        closing_sec=config.CLOSING_SEC,
        n=MAX_GAP_QUESTIONS,
        jd=job.jd_text,
        bank=json.dumps([q.model_dump() for q in job.questions], indent=1),
        criteria=json.dumps([c.model_dump() for c in job.criteria], indent=1),
        company=company_summary[:COMPANY_SUMMARY_CHARS],
    )
    # flex: planning runs when the recruiter saves the job, not while a candidate waits.
    plan = await llm.generate_json(
        config.PLANNER_MODEL, prompt, InterviewPlan, timeout=120, session_id=f"job:{job.id}", flex=True
    )
    plan = fit_to_time(plan, job.max_duration_sec - config.CLOSING_SEC)
    if not plan.questions:
        raise ValueError("planner produced no questions that fit the time cap")
    return plan


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job", type=Path)
    ap.add_argument("kb_dir", type=Path)
    ap.add_argument("--out", type=Path, default=config.PLAN_PATH)
    args = ap.parse_args()

    job = Job.model_validate_json(args.job.read_text(encoding="utf-8"))
    summary = "\n\n".join(p.read_text(encoding="utf-8") for p in sorted(args.kb_dir.glob("*")))
    plan = asyncio.run(make_plan(job, summary))
    args.out.write_text(Plan(job=job, plan=plan).model_dump_json(indent=2), encoding="utf-8")
    print(f"{len(plan.questions)} questions planned, {len(plan.skipped_questions)} skipped -> {args.out}")


if __name__ == "__main__":
    main()
