"""Persist AI-generated analyte guides and their bounded backfill queue.

Revision ID: 20260821_0007
Revises: 20260820_0006
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260821_0007"
down_revision: str | None = "20260820_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lab_analyte_guides",
        sa.Column(
            "analyte_id",
            sa.String(120),
            sa.ForeignKey("lab_analytes.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("why_tested", sa.Text(), nullable=False),
        sa.Column("low_meaning", sa.Text(), nullable=False),
        sa.Column("high_meaning", sa.Text(), nullable=False),
        sa.Column("contract_version", sa.String(64), nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "lab_analyte_guide_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "analyte_id",
            sa.String(120),
            sa.ForeignKey("lab_analytes.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_lab_analyte_guide_jobs_claim",
        "lab_analyte_guide_jobs",
        ["status", "available_at", "id"],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE TRIGGER trg_lab_analyte_guide_jobs_notify "
            "AFTER INSERT OR UPDATE OF status ON lab_analyte_guide_jobs "
            "FOR EACH ROW WHEN (NEW.status = 'pending') "
            "EXECUTE FUNCTION amigo_notify_background_work()"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_lab_analyte_guide_jobs_notify "
            "ON lab_analyte_guide_jobs"
        )
    op.drop_index(
        "ix_lab_analyte_guide_jobs_claim", table_name="lab_analyte_guide_jobs"
    )
    op.drop_table("lab_analyte_guide_jobs")
    op.drop_table("lab_analyte_guides")
