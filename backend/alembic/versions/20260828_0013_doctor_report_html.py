"""Store the size of the standalone HTML doctor report."""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260828_0013"
down_revision: str | None = "20260828_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("doctor_report_snapshots", sa.Column("html_size_bytes", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("doctor_report_snapshots", "html_size_bytes")
