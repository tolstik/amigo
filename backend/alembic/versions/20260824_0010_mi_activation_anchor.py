"""Anchor Xiaomi activation to the source enablement episode.

Revision ID: 20260824_0010
Revises: 20260824_0009
"""
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260824_0010"
down_revision: str | None = "20260824_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "mi_fitness_sources",
        sa.Column("activation_started_at", sa.DateTime(timezone=True)),
    )
    op.execute(
        """
        UPDATE mi_fitness_sources
        SET activation_started_at = created_at
        WHERE enabled IS TRUE
        """
    )
    # Keep the episode boundary correct if automatic recovery temporarily starts
    # the immediately previous application image, which does not map this column.
    op.execute(
        """
        CREATE FUNCTION amigo_set_mi_activation_episode() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.enabled THEN
                    NEW.activation_started_at := COALESCE(
                        NEW.activation_started_at,
                        CURRENT_TIMESTAMP
                    );
                ELSE
                    NEW.activation_started_at := NULL;
                END IF;
            ELSIF NEW.enabled IS DISTINCT FROM OLD.enabled THEN
                IF NEW.enabled THEN
                    NEW.activation_started_at := CURRENT_TIMESTAMP;
                ELSE
                    NEW.activation_started_at := NULL;
                END IF;
            ELSIF NOT NEW.enabled THEN
                NEW.activation_started_at := NULL;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_mi_activation_episode
        BEFORE INSERT OR UPDATE OF enabled ON mi_fitness_sources
        FOR EACH ROW EXECUTE FUNCTION amigo_set_mi_activation_episode()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_mi_activation_episode ON mi_fitness_sources"
    )
    op.execute("DROP FUNCTION IF EXISTS amigo_set_mi_activation_episode()")
    op.drop_column("mi_fitness_sources", "activation_started_at")
