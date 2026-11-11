"""Adaptive follow-up eval (PLAN §3.9): the same question answered three valid but different
ways should get different observer notes and suggested follow-ups. Needs GOOGLE_API_KEY.

Usage: uv run python -m evals.followup_eval
Check by reading: each suggested follow-up should probe what THAT answer left open.
"""

import asyncio

from guru import config, prompts
from guru.llm import generate_json
from guru.models import ObserverNote

QUESTION = "How would you make a Kafka consumer process each event exactly once into PostgreSQL?"
SIGNALS = [
    "idempotent writes or deduplication keys",
    "offset commit tied to the database transaction",
    "awareness of at-least-once delivery semantics",
    "failure and replay scenarios",
]
ANSWERS = {
    "transactional outbox / offsets in DB": "I'd store the consumer offset in the same Postgres transaction as the "
    "write, so either both commit or neither does. On restart I read the offset from the table and seek to it.",
    "idempotency keys": "Kafka gives at-least-once, so I'd make the write idempotent: every event has an id, and I "
    "insert with ON CONFLICT DO NOTHING on a unique constraint. Duplicates just become no-ops.",
    "vague": "I'd use Kafka's exactly-once setting so it's handled for me, and add some retries.",
}


async def main() -> None:
    notes = await asyncio.gather(
        *(
            generate_json(
                config.OBSERVER_MODEL,
                prompts.OBSERVER.format(question=QUESTION, signals="; ".join(SIGNALS), answer=a, latest=a),
                ObserverNote,
            )
            for a in ANSWERS.values()
        )
    )
    for name, n in zip(ANSWERS, notes):
        print(f"\n== {name}\n depth={n.depth} coverage={n.coverage}\n missing={n.signals_missing}")
        print(f" follow-up: {n.suggested_followup}")
    followups = {n.suggested_followup for n in notes}
    assert len(followups) == len(ANSWERS), "follow-ups should differ per approach"
    assert notes[2].depth == "shallow", "the vague answer should be graded shallow"
    print("\nPASS")


if __name__ == "__main__":
    asyncio.run(main())
