"""outbox: Celery tasks to publish, written in the same commit as the source's stage change

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outbox",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("task", sa.String, nullable=False),
        sa.Column("source_id", sa.String, sa.ForeignKey("kb_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer, server_default="0", nullable=False),
        sa.Column("last_error", sa.Text),
    )
    op.create_index("ix_outbox_source_id", "outbox", ["source_id"])
    # The relay's poll reads only unsent rows.
    op.create_index("ix_outbox_unsent", "outbox", ["id"], postgresql_where=sa.text("sent_at IS NULL"))


def downgrade() -> None:
    op.drop_table("outbox")
