"""invite link expiry

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Null = never expires (links made before this migration).
    op.add_column("interview_sessions", sa.Column("invite_expires_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("interview_sessions", "invite_expires_at")
