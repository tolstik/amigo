"""Store day notes, daily self-assessments and medication date ranges.

All changes are additive so the immediately previous release keeps working on
the preserved PostgreSQL volume.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261107_0017"
down_revision: str | None = "20260926_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "day_notes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("text", sa.String(length=280), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "category IN ('illness','holiday','travel','medication','stress','other')",
            name="ck_day_notes_category",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_day_notes"),
    )
    op.create_index("ix_day_notes_day", "day_notes", ["day"])
    op.create_table(
        "daily_checkins",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("energy", sa.SmallInteger(), nullable=True),
        sa.Column("hunger", sa.SmallInteger(), nullable=True),
        sa.Column("mood", sa.SmallInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "energy IS NOT NULL OR hunger IS NOT NULL OR mood IS NOT NULL",
            name="ck_daily_checkins_has_value",
        ),
        sa.CheckConstraint("energy IS NULL OR (energy BETWEEN 1 AND 5)", name="ck_daily_checkins_energy"),
        sa.CheckConstraint("hunger IS NULL OR (hunger BETWEEN 1 AND 5)", name="ck_daily_checkins_hunger"),
        sa.CheckConstraint("mood IS NULL OR (mood BETWEEN 1 AND 5)", name="ck_daily_checkins_mood"),
        sa.PrimaryKeyConstraint("day", name="pk_daily_checkins"),
    )
    op.add_column("medications", sa.Column("started_on", sa.Date(), nullable=True))
    op.add_column("medications", sa.Column("stopped_on", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("medications", "stopped_on")
    op.drop_column("medications", "started_on")
    op.drop_table("daily_checkins")
    op.drop_index("ix_day_notes_day", table_name="day_notes")
    op.drop_table("day_notes")
