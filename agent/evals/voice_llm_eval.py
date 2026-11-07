"""Voice LLM benchmark: time to first token AND the interviewer's turn decisions, per provider.

Each scenario is a real interviewer prompt (fixture plan) at a decision point; every voice LLM in
interviewer.voice_llms() gets the same requests, alternated so network drift hits both alike.
A faster model is only a win if it still makes the right call (follow up vs advance, look up,
end). Needs OPENROUTER_API_KEY and GOOGLE_API_KEY.

Usage: uv run python -m evals.voice_llm_eval [runs_per_scenario, default 5]
"""

import asyncio
import statistics
import sys
import time

from livekit.agents import llm

from guru import config
from guru.interviewer import InterviewerAgent, State, voice_llms
from guru.models import Plan

ANSWER = (
    "I'd put the events on a Kafka topic, have a consumer write them to Postgres, and use retries "
    "so nothing gets lost."
)
# (name, follow-ups already used, candidate's last words, expected: "speak" or the tool to call)
SCENARIOS = [
    ("follow up on a thin answer", 0, ANSWER, "speak"),
    ("follow-ups used up", config.MAX_FOLLOW_UPS, ANSWER, "advance_to_next_question"),
    ("company question", 0, "Before I answer, what's the team's on-call rotation like?", "lookup_company_info"),
    ("candidate stops", 0, "Sorry, I need to stop the interview here, something came up.", "candidate_wants_to_end"),
]


def request(answers_used: int, said: str) -> tuple[llm.ChatContext, list]:
    s = State(plan=Plan.model_validate_json(config.PLAN_PATH.read_text(encoding="utf-8")), session_id="eval")
    s.answers = ["(earlier answer)"] * answers_used
    agent = InterviewerAgent(s)  # builds the live instructions for this state
    ctx = llm.ChatContext()
    ctx.add_message(role="system", content=agent.instructions)
    ctx.add_message(role="assistant", content=s.question.text)
    ctx.add_message(role="user", content=said)
    return ctx, agent.tools


async def once(model: llm.LLM, ctx: llm.ChatContext, tools: list) -> tuple[float, float, str]:
    """(time to first token, total time, decision)."""
    t0 = time.perf_counter()
    first, decision = None, "speak"
    async with model.chat(chat_ctx=ctx, tools=tools) as stream:
        async for chunk in stream:
            d = chunk.delta
            if d and (d.content or d.tool_calls):
                first = first or time.perf_counter() - t0
                if d.tool_calls:
                    decision = d.tool_calls[0].name
    return first or float("nan"), time.perf_counter() - t0, decision


async def main(runs: int) -> None:
    models = voice_llms()
    results: dict[str, list[tuple[float, float, bool]]] = {m.model: [] for m in models}
    for name, used, said, expected in SCENARIOS:
        ctx, tools = request(used, said)
        for _ in range(runs):
            for m in models:  # alternate providers within each run
                try:
                    ttft, total, decision = await once(m, ctx, tools)
                except Exception as e:
                    print(f"  {m.model:28} {name:28} ERROR {type(e).__name__}: {e}")
                    continue
                ok = decision == expected
                results[m.model].append((ttft, total, ok))
                print(f"  {m.model:28} {name:28} ttft {ttft:5.2f}s  total {total:5.2f}s  {decision:26} {'ok' if ok else 'WRONG'}")
                # Stay under a 15 requests/minute quota (Gemini free tier): a 429 retry would be
                # timed as model latency.
                await asyncio.sleep(4)

    print("\nmodel                        n   ttft p50  ttft p95  ttft max  total p50  right")
    for model, rows in results.items():
        if not rows:
            continue
        ttfts = sorted(r[0] for r in rows)
        p95 = ttfts[min(len(ttfts) - 1, round(0.95 * (len(ttfts) - 1)))]
        print(f"{model:28} {len(rows):3}  {statistics.median(ttfts):7.2f}s  {p95:7.2f}s  {ttfts[-1]:7.2f}s"
              f"  {statistics.median(r[1] for r in rows):8.2f}s  {sum(r[2] for r in rows)}/{len(rows)}")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 5))
