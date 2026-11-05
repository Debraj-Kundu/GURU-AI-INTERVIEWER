"""Outbox relay: publishes unsent OutboxRow tasks to Celery and marks them sent. Run one:

    uv run python -m app.relay

Each row was committed together with the stage change it belongs to (app/ingest.py), so a task is
never lost between Postgres and Redis. Publish-then-mark means a crash in between sends a task twice:
delivery is at least once, and each ingest stage skips work already done.

Also, every minute, the sweeper: a source unchanged in one stage for STUCK_AFTER with no unsent row
lost its task after publishing (message expired, Redis data lost), so it's queued again. Sent rows are
kept OUTBOX_KEEP for debugging, then deleted.
"""

import asyncio
import logging
import time
from datetime import timedelta

from sqlalchemy import delete, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from guru.jsonlog import jlog

from . import settings
from .db import OutboxRow, SessionLocal, SourceRow
from .ingest import enqueue
from .worker import TASK_EXPIRES, TASKS, celery

BATCH = 100
OUTBOX_KEEP = timedelta(days=7)


async def publish_pending(db: AsyncSession) -> int:
    """Publish up to BATCH unsent rows in order; returns how many were read."""
    rows = list(await db.scalars(
        select(OutboxRow).where(OutboxRow.sent_at.is_(None)).order_by(OutboxRow.id).limit(BATCH)
        .with_for_update(skip_locked=True)  # a second relay takes other rows
    ))
    for row in rows:
        try:
            await asyncio.to_thread(  # blocking Redis call
                celery.send_task, TASKS[row.task], args=[row.source_id], expires=TASK_EXPIRES
            )
            row.sent_at = func.now()
        except Exception as e:
            row.attempts += 1
            row.last_error = f"{type(e).__name__}: {str(e)[:300]}"
            # one line, no stack: this repeats every poll while the broker is down
            jlog("Outbox publish failed", function_name="publish_pending",
                 output={"id": row.id, "attempts": row.attempts, "error": row.last_error}, level=logging.WARNING)
            break  # the broker is likely down: keep order, try again next poll
    await db.commit()
    return len(rows)


async def requeue_stuck(db: AsyncSession) -> None:
    pending = exists().where(OutboxRow.source_id == SourceRow.id, OutboxRow.sent_at.is_(None))
    stuck = list(await db.scalars(
        select(SourceRow).where(
            SourceRow.status.in_(("queued", "parsing", "parsed", "embedding")),
            SourceRow.updated_at < func.now() - settings.STUCK_AFTER,
            ~pending,
        )
    ))
    for src in stuck:
        await enqueue(db, src)  # moves updated_at, so it isn't swept again at once
    if stuck:
        jlog("Re-queued stuck sources", function_name="requeue_stuck", output={"source_ids": [s.id for s in stuck]})


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s %(message)s")
    last_sweep = last_purge = 0.0
    while True:
        read = 0
        try:
            async with SessionLocal() as db:
                read = await publish_pending(db)
                now = time.monotonic()
                if now - last_sweep >= 60:
                    last_sweep = now
                    await requeue_stuck(db)
                if now - last_purge >= 3600:
                    last_purge = now
                    await db.execute(delete(OutboxRow).where(OutboxRow.sent_at < func.now() - OUTBOX_KEEP))
                    await db.commit()
        except Exception as e:
            jlog("Relay loop failed", function_name="main", err=e, level=logging.ERROR)
        if read < BATCH:  # a full batch means more may be waiting
            await asyncio.sleep(1)  # ponytail: 1 s polling; LISTEN/NOTIFY if that delay matters


if __name__ == "__main__":
    asyncio.run(main())
