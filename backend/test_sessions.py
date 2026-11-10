"""One attempt per interview: a fresh room per try until an agent starts, then only that room."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app import sessions, settings
from app.db import SessionRow


class FakeDb:
    def __init__(self, row: SessionRow):
        self.row = row

    async def get(self, _model, _id):
        return self.row

    async def commit(self):
        pass

    async def execute(self, _stmt, params=None):  # pg_notify from sessions.notify
        self.notified = [*getattr(self, "notified", []), params["payload"]] if params else []


def test_one_attempt_per_interview():
    async def run():
        s = SessionRow(id="ab" * 16, org_id=settings.DEFAULT_ORG, status="created", room_name="",
                       candidate_name="", plan={"job": {}})
        db = FakeDb(s)

        # No agent joined yet: each token request gets a new room, so the dispatch runs again.
        await sessions.interview_token(s.id, db)  # type: ignore[arg-type]
        first = s.room_name
        await sessions.interview_token(s.id, db)  # type: ignore[arg-type]
        assert s.room_name != first and s.status == "created"

        # An agent from the stale room can't start it; the latest room's agent can, once.
        with pytest.raises(HTTPException):
            await sessions.agent_start(s.id, sessions.StartIn(room=first), db)  # type: ignore[arg-type]
        await sessions.agent_start(s.id, sessions.StartIn(room=s.room_name), db)  # type: ignore[arg-type]
        assert s.status == "active" and s.started_at is not None
        with pytest.raises(HTTPException):
            await sessions.agent_start(s.id, sessions.StartIn(room=s.room_name), db)  # type: ignore[arg-type]

        # Rejoin while active: same room (the agent is waiting there).
        room = s.room_name
        await sessions.interview_token(s.id, db)  # type: ignore[arg-type]
        assert s.room_name == room

        # Once the agent reports the end, no more tokens.
        s.status = "completed"
        with pytest.raises(HTTPException):
            await sessions.interview_token(s.id, db)  # type: ignore[arg-type]

    asyncio.run(run())


def test_link_expiry_and_renew():
    async def run():
        s = SessionRow(id="cd" * 16, org_id=settings.DEFAULT_ORG, status="created", room_name="",
                       candidate_name="", plan={"job": {}})
        db = FakeDb(s)
        sessions.start_window(s)
        assert not sessions.expired(s)

        # Lapsed before starting: no token.
        s.invite_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        with pytest.raises(HTTPException) as e:
            await sessions.interview_token(s.id, db)  # type: ignore[arg-type]
        assert e.value.status_code == 410

        # Renew restarts the window.
        await sessions.renew_link(s.id, db)  # type: ignore[arg-type]
        assert not sessions.expired(s)
        await sessions.interview_token(s.id, db)  # type: ignore[arg-type]

        # Started, then the window passes: a rejoin still works, and renew is refused.
        s.status = "active"
        s.invite_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await sessions.interview_token(s.id, db)  # type: ignore[arg-type]
        with pytest.raises(HTTPException):
            await sessions.renew_link(s.id, db)  # type: ignore[arg-type]

    asyncio.run(run())


def test_completion_webhook_is_idempotent(monkeypatch):
    """A redelivered webhook (lost acknowledgement, retried delivery) changes nothing."""
    monkeypatch.setattr(sessions, "spawn", lambda coro: coro.close())  # no real scoring

    async def run():
        s = SessionRow(id="ef" * 16, org_id=settings.DEFAULT_ORG, job_id="job_x", status="active", room_name="r",
                       candidate_name="", plan={"job": {}}, score_status="none")
        db = FakeDb(s)
        body = sessions.CompleteIn(consented=True, turns=[{"idx": 0, "speaker": "candidate", "text": "hi"}])
        assert await sessions.agent_complete(s.id, body, db) == {"ok": True}  # type: ignore[arg-type]
        assert s.status == "completed" and s.score_status == "scoring" and len(db.notified) == 1
        first_end = s.ended_at

        again = sessions.CompleteIn(consented=False)  # even a different payload is ignored
        assert await sessions.agent_complete(s.id, again, db) == {"ok": True, "duplicate": True}  # type: ignore[arg-type]
        assert s.status == "completed" and s.ended_at == first_end and len(db.notified) == 1

    asyncio.run(run())


def test_webhook_retries_until_the_api_accepts(monkeypatch):
    import httpx

    from app import worker

    replies = iter([httpx.ConnectError("api down"), 503, 200])
    posted: list[str] = []

    def fake_post(url, **_):
        posted.append(url)
        r = next(replies)
        if isinstance(r, Exception):
            raise r
        return httpx.Response(r, json={}, request=httpx.Request("POST", url))

    monkeypatch.setattr(worker.httpx, "post", fake_post)
    waits: list[int] = []

    class Retry(Exception):
        pass

    def fake_retry(exc, countdown):
        waits.append(countdown)
        return Retry()

    task = worker.deliver_webhook
    monkeypatch.setattr(task, "retry", fake_retry)
    for attempt in range(3):
        task.request.retries = attempt
        try:
            task.run("s1", {"consented": False})
        except Retry:
            continue
    assert waits == [5, 10] and len(posted) == 3  # down, 503, then delivered: no third retry

    # 4xx: retrying can't fix it, so it stops without a retry.
    monkeypatch.setattr(worker.httpx, "post", lambda url, **_: httpx.Response(401, request=httpx.Request("POST", url)))
    task.run("s1", {"consented": False})
    assert waits == [5, 10]
