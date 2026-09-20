"""Keep a private normalized texture for the body mannequin."""
from alembic import op
import sqlalchemy as sa

revision = "20260920_0015"
down_revision = "20260901_0014"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "user_body_face",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("user_body_face")
