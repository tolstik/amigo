"""Version analyte guide jobs so failed contracts are retried exactly once.

Revision ID: 20260821_0008
Revises: 20260821_0007
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260821_0008"
down_revision: str | None = "20260821_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "lab_analyte_guide_jobs",
        sa.Column(
            "contract_version",
            sa.String(64),
            nullable=False,
            server_default="amigo-lab-analyte-guide-v1",
        ),
    )
    op.alter_column(
        "lab_analyte_guide_jobs",
        "contract_version",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_column("lab_analyte_guide_jobs", "contract_version")
