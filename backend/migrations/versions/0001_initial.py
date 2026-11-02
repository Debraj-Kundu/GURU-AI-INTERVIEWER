"""initial tables: jobs, kb_sources, interview_plans, interview_sessions

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("org_id", sa.String, nullable=False, index=True),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("company_name", sa.String, nullable=False),
        sa.Column("jd_text", sa.Text, nullable=False),
        sa.Column("max_duration_sec", sa.Integer, nullable=False),
        sa.Column("questions", JSONB, nullable=False),
        sa.Column("criteria", JSONB, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.CheckConstraint("max_duration_sec BETWEEN 300 AND 3600", name="jobs_duration_range"),
    )
    op.create_table(
        "kb_sources",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("org_id", sa.String, nullable=False, index=True),
        sa.Column("job_id", sa.String, sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("s3_key", sa.String),
        sa.Column("url", sa.Text),
        sa.Column("size_bytes", sa.Integer),
        sa.Column("sha256", sa.String, index=True),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("chunks", sa.Integer, nullable=False),
        sa.Column("excerpt", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    )
    op.create_table(
        "interview_plans",
        sa.Column("job_id", sa.String, sa.ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("job_version", sa.Integer, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("plan", JSONB),
        sa.Column("error", sa.Text),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    )
    op.create_table(
        "interview_sessions",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("org_id", sa.String, nullable=False, index=True),
        sa.Column("job_id", sa.String, sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("candidate_name", sa.String, nullable=False),
        sa.Column("room_name", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("plan", JSONB, nullable=False),
        sa.Column("transcript", JSONB, nullable=False),
        sa.Column("notes", JSONB, nullable=False),
        sa.Column("usage", JSONB, nullable=False),
        sa.Column("duration_sec", sa.Float),
        sa.Column("score_status", sa.String, nullable=False),
        sa.Column("score", JSONB),
        sa.Column("score_error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    for t in ("interview_sessions", "interview_plans", "kb_sources", "jobs"):
        op.drop_table(t)
