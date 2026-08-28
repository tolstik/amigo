"""Store daily user-entered waist and hip measurements.

Revision ID: 20260828_0012
Revises: 20260825_0011
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260828_0012"
down_revision: str | None = "20260825_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "body_circumference_measurements",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("measured_on", sa.Date(), nullable=False),
        sa.Column("waist_cm", sa.Numeric(6, 2), nullable=True),
        sa.Column("hip_cm", sa.Numeric(6, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "waist_cm IS NOT NULL OR hip_cm IS NOT NULL",
            name="ck_body_circumference_has_value",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_body_circumference_measurements"),
        sa.UniqueConstraint("measured_on", name="uq_body_circumference_date"),
    )


def downgrade() -> None:
    op.drop_table("body_circumference_measurements")
