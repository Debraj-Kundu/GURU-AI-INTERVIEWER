"""Scorer agent (PLAN §3.7): one parallel call per criterion, then a weighted aggregate.

Usage: uv run python -m guru.scorer out/<session_id>.json [--plan fixtures/plan.json]
"""

import argparse
import asyncio
import json
from pathlib import Path

from . import config, llm, prompts
from .models import Criterion, CriterionScore, Plan


def aggregate(scores: list[CriterionScore], criteria: list[Criterion]) -> float | None:
    """Weighted mean on the 1-5 scale; criteria without evidence are excluded, not zeroed."""
    weights = {c.id: c.weight for c in criteria}
    rated = [(s.score, weights[s.criterion_id]) for s in scores if not s.insufficient_evidence and s.score is not None]
    total = sum(w for _, w in rated)
    if not total:
        return None
    return round(sum(score * w for score, w in rated) / total, 2)


async def score_criterion(c: Criterion, transcript: str, notes: str) -> CriterionScore:
    prompt = prompts.SCORER.format(
        name=c.name,
        id=c.id,
        description=c.description,
        anchors="\n".join(f"{k}: {v}" for k, v in sorted(c.anchors.items())),
        notes=notes,
        transcript=transcript,
    )
    s = await llm.generate_json(config.SCORER_MODEL, prompt, CriterionScore, timeout=120)
    s.criterion_id = c.id  # never trust the model to echo the id back
    return s


async def score_session(session: dict, plan: Plan) -> dict:
    transcript = "\n".join(f"{t['idx']} {t['speaker']}: {t['text']}" for t in session["turns"])
    notes = json.dumps(session.get("notes", []))
    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(score_criterion(c, transcript, notes)) for c in plan.job.criteria]
    scores = [t.result() for t in tasks]
    return {
        "session_id": session["session_id"],
        "overall": aggregate(scores, plan.job.criteria),
        "criteria": [s.model_dump() for s in scores],
        "advisory": "AI-assisted score. Requires human review before any decision.",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("session", type=Path)
    ap.add_argument("--plan", type=Path, default=config.PLAN_PATH)
    args = ap.parse_args()

    session = json.loads(args.session.read_text(encoding="utf-8"))
    plan = Plan.model_validate_json(args.plan.read_text(encoding="utf-8"))
    result = asyncio.run(score_session(session, plan))
    out = args.session.with_suffix(".score.json")
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"overall={result['overall']} -> {out}")


if __name__ == "__main__":
    main()
