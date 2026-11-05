"""Ingest queueing through the transactional outbox: a retry resumes at the first stage that hasn't
succeeded; the relay publishes in order and stops at a broker error; temporary errors in a stage are
retried, then the source is marked failed with its saved progress kept."""

import asyncio

import pytest

from app import ingest, relay, worker
from app.db import OutboxRow, SourceRow


class FakeDb:
    def __init__(self, parsed: bool = False, rows: list | None = None):
        self.parsed, self.rows, self.added, self.commits = parsed, rows or [], [], 0

    async def scalar(self, _query):
        return self.parsed

    async def scalars(self, _query):
        return self.rows

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


def test_enqueue_writes_status_and_task_together():
    async def run():
        # Failed while parsing: nothing saved, so parse again.
        db = FakeDb(parsed=False)
        src = SourceRow(id="src_a", status="failed", error="Parsing failed: boom")
        await ingest.enqueue(db, src)  # type: ignore[arg-type]
        assert (src.status, src.error) == ("queued", None)
        assert [(o.task, o.source_id) for o in db.added] == [("parse", "src_a")] and db.commits == 1

        # Failed while embedding: chunks are saved, so skip straight to embed.
        db = FakeDb(parsed=True)
        src = SourceRow(id="src_b", status="failed", error="Embedding failed: boom")
        await ingest.enqueue(db, src)  # type: ignore[arg-type]
        assert src.status == "parsed"
        assert [(o.task, o.source_id) for o in db.added] == [("embed", "src_b")] and db.commits == 1

    asyncio.run(run())


def test_relay_publishes_in_order_and_stops_at_broker_error(monkeypatch):
    rows = [OutboxRow(id=i, task=t, source_id=f"src_{i}", attempts=0) for i, t in enumerate(("parse", "embed", "parse"))]
    sent: list[tuple[str, list, int]] = []

    def send_task(name, args, expires):
        if args == ["src_1"]:
            raise ConnectionError("Redis down")
        sent.append((name, args, expires))

    monkeypatch.setattr(relay.celery, "send_task", send_task)
    db = FakeDb(rows=rows)
    assert asyncio.run(relay.publish_pending(db)) == 3  # type: ignore[arg-type]

    assert sent == [("app.worker.parse_source", ["src_0"], worker.TASK_EXPIRES)]
    assert rows[0].sent_at is not None
    assert rows[1].sent_at is None and rows[1].attempts == 1 and "Redis down" in rows[1].last_error
    assert rows[2].sent_at is None and rows[2].attempts == 0  # left for the next poll, order kept
    assert db.commits == 1


def test_retries_then_fails(monkeypatch):
    failed: list[str] = []

    async def stage(_sid):
        raise ConnectionError("embeddings API down")

    async def fail(sid, stage_name, _e):
        failed.append(f"{stage_name}:{sid}")

    monkeypatch.setattr(ingest, "fail", fail)

    class Task:
        class request:
            retries = 0

        def retry(self, exc, countdown):
            return RuntimeError(f"retry in {countdown}")

    task = Task()
    for n, wait in enumerate((10, 20, 40)):  # temporary error: retried with backoff
        task.request.retries = n
        with pytest.raises(RuntimeError, match=f"retry in {wait}"):
            worker._run(task, "Embedding", stage, "src_c")
    assert failed == []

    task.request.retries = worker.RETRIES  # out of retries: marked failed, not raised
    worker._run(task, "Embedding", stage, "src_c")
    assert failed == ["Embedding:src_c"]
