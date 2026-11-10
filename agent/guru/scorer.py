"""Scorer agent (PLAN §3.7): one parallel call per criterion, then a weighted aggregate.

Usage: uv run python -m guru.scorer out/<session_id>.json [--plan fixtures/plan.json]
"""

import argparse
import asyncio
import json
import logging
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


async def score_criterion(c: Criterion, context: str, session_id: str, provider: llm.Provider) -> CriterionScore:
    prompt = prompts.SCORER.format(
        name=c.name,
        id=c.id,
        description=c.description,
        anchors="\n".join(f"{k}: {v}" for k, v in sorted(c.anchors.items())),
    )
    # flex: nobody waits on the score (the candidate never sees it), so take the cheaper tier.
    s = await llm.generate_json(
        config.SCORER_MODEL, prompt, CriterionScore, timeout=120, context=context, session_id=session_id, flex=True,
        provider=provider,
    )
    s.criterion_id = c.id  # never trust the model to echo the id back
    return s


async def score_session(session: dict, plan: Plan) -> dict:
    transcript = "\n".join(f"{t['idx']} {t['speaker']}: {t['text']}" for t in session["turns"])
    context = prompts.SCORER_CONTEXT.format(notes=json.dumps(session.get("notes", [])), transcript=transcript)
    sid = session["session_id"]
    # One judge per session: every criterion is scored by the same model. If any OpenRouter call
    # fails, all criteria are re-scored on Gemini, rather than mixing models within one candidate.
    provider: llm.Provider = "openrouter" if config.OPENROUTER_API_KEY else "gemini"
    try:
        scores = await _score_all(plan, context, sid, provider)
    except Exception as e:
        if provider == "gemini":
            raise
        logging.getLogger(__name__).warning("scoring on openrouter failed, re-scoring all criteria on gemini: %s", e)
        provider = "gemini"
        scores = await _score_all(plan, context, sid, provider)
    return {
        "session_id": session["session_id"],
        "model": config.OPENROUTER_MODEL if provider == "openrouter" else config.SCORER_MODEL,
        "overall": aggregate(scores, plan.job.criteria),
        "criteria": [s.model_dump() for s in scores],
        "advisory": "AI-assisted score. Requires human review before any decision.",
    }


async def _score_all(plan: Plan, context: str, sid: str, provider: llm.Provider) -> list[CriterionScore]:
    first, *rest = plan.job.criteria
    # The first call writes the shared-context cache; firing all at once would make every call miss it.
    scores = [await score_criterion(first, context, sid, provider)]
    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(score_criterion(c, context, sid, provider)) for c in rest]
    return scores + [t.result() for t in tasks]


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
