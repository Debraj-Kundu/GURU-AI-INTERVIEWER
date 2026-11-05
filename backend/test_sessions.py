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
