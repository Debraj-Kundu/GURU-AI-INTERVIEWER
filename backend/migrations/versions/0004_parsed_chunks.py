"""kb_sources.parsed_chunks: parse output kept between the parse and embed stages

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("kb_sources", sa.Column("parsed_chunks", JSONB))


def downgrade() -> None:
    op.drop_column("kb_sources", "parsed_chunks")
