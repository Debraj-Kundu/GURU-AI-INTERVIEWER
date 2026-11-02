"""Postgres tables (PLAN §4.2 subset) and the async session factory."""

import uuid
from collections.abc import AsyncIterator
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from . import settings

engine = create_async_engine(settings.DATABASE_URL, pool_size=5, max_overflow=5, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class Base(DeclarativeBase):
    pass


class JobRow(Base):
    __tablename__ = "jobs"
    __table_args__ = (CheckConstraint("max_duration_sec BETWEEN 300 AND 3600", name="jobs_duration_range"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("job"))
    org_id: Mapped[str] = mapped_column(String, index=True)
    title: Mapped[str] = mapped_column(String)
    company_name: Mapped[str] = mapped_column(String)
    jd_text: Mapped[str] = mapped_column(Text)
    max_duration_sec: Mapped[int] = mapped_column(Integer, default=600)
    # ponytail: question bank and criteria live as JSONB on the job; they're always edited and
    # read as whole lists. Split into tables when something needs to query single rows.
    questions: Mapped[list] = mapped_column(JSONB, default=list)
    criteria: Mapped[list] = mapped_column(JSONB, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)  # bumped on edit; makes plans stale
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SourceRow(Base):
    """One knowledge-base source: an uploaded file or a URL."""

    __tablename__ = "kb_sources"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("src"))
    org_id: Mapped[str] = mapped_column(String, index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String)  # pdf | docx | txt | md | csv | url
    name: Mapped[str] = mapped_column(String)  # file name or URL; cited in company answers
    s3_key: Mapped[str | None] = mapped_column(String, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # uploading -> queued -> parsing -> embedding -> ready | failed | duplicate
    status: Mapped[str] = mapped_column(String, default="queued")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunks: Mapped[int] = mapped_column(Integer, default=0)
    excerpt: Mapped[str] = mapped_column(Text, default="")  # start of the text; feeds the planner
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlanRow(Base):
    __tablename__ = "interview_plans"
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    job_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String)  # generating | ready | failed
    plan: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # guru.models.Plan (job + plan)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SessionRow(Base):
    __tablename__ = "interview_sessions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: uuid.uuid4().hex)
    org_id: Mapped[str] = mapped_column(String, index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    candidate_name: Mapped[str] = mapped_column(String, default="")
    room_name: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="created")  # created | active | completed | declined
    # Snapshot taken when the link is created, so editing the job never changes a live interview.
    plan: Mapped[dict] = mapped_column(JSONB)
    # ponytail: transcript, notes, and usage are JSONB on the session (always read whole).
    # PLAN §4.2 splits transcript_turns/answer_notes out; do that when turns need querying.
    transcript: Mapped[list] = mapped_column(JSONB, default=list)
    notes: Mapped[list] = mapped_column(JSONB, default=list)
    usage: Mapped[dict] = mapped_column(JSONB, default=dict)  # model usage, latency, llm call counts
    duration_sec: Mapped[float | None] = mapped_column(nullable=True)
    score_status: Mapped[str] = mapped_column(String, default="none")  # none | scoring | scored | failed
    score: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    score_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as db:
        yield db
