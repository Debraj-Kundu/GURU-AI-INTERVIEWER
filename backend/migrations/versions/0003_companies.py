"""companies: jobs become a company's roles; knowledge-base sources belong to the company

Existing data: one company per distinct (org, company name). Sources move to their job's company and
are queued to re-embed (the API re-ingests queued sources on startup), because Qdrant chunks are now
keyed by company. Every job's version is bumped, so existing plans must be regenerated once.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("org_id", sa.String, nullable=False, index=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    )
    op.execute(
        "INSERT INTO companies (id, org_id, name) "
        "SELECT 'cmp_' || substr(md5(org_id || '/' || company_name), 1, 16), org_id, company_name "
        "FROM jobs GROUP BY org_id, company_name"
    )

    op.add_column("jobs", sa.Column("company_id", sa.String))
    op.execute(
        "UPDATE jobs SET company_id = c.id, version = version + 1 FROM companies c "
        "WHERE c.org_id = jobs.org_id AND c.name = jobs.company_name"
    )
    op.alter_column("jobs", "company_id", nullable=False)
    op.create_foreign_key("jobs_company_id_fkey", "jobs", "companies", ["company_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_jobs_company_id", "jobs", ["company_id"])
    op.drop_column("jobs", "company_name")

    op.add_column("kb_sources", sa.Column("company_id", sa.String))
    op.execute("UPDATE kb_sources s SET company_id = j.company_id FROM jobs j WHERE j.id = s.job_id")
    op.execute(
        "UPDATE kb_sources SET status = 'queued', error = NULL, chunks = 0 "
        "WHERE status IN ('ready', 'duplicate', 'parsing', 'embedding')"
    )
    op.alter_column("kb_sources", "company_id", nullable=False)
    op.create_foreign_key(
        "kb_sources_company_id_fkey", "kb_sources", "companies", ["company_id"], ["id"], ondelete="CASCADE"
    )
    op.create_index("ix_kb_sources_company_id", "kb_sources", ["company_id"])
    op.drop_index("ix_kb_sources_job_id", "kb_sources")
    op.drop_column("kb_sources", "job_id")


def downgrade() -> None:
    # Lossy: each source goes back to one (the first) role of its company.
    op.add_column("kb_sources", sa.Column("job_id", sa.String))
    op.execute(
        "UPDATE kb_sources s SET job_id = (SELECT min(j.id) FROM jobs j WHERE j.company_id = s.company_id)"
    )
    op.execute("DELETE FROM kb_sources WHERE job_id IS NULL")
    op.alter_column("kb_sources", "job_id", nullable=False)
    op.create_foreign_key("kb_sources_job_id_fkey", "kb_sources", "jobs", ["job_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_kb_sources_job_id", "kb_sources", ["job_id"])
    op.drop_column("kb_sources", "company_id")

    op.add_column("jobs", sa.Column("company_name", sa.String))
    op.execute("UPDATE jobs SET company_name = c.name FROM companies c WHERE c.id = jobs.company_id")
    op.alter_column("jobs", "company_name", nullable=False)
    op.drop_column("jobs", "company_id")
    op.drop_table("companies")
