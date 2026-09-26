"""Store optional private model assets beside the legacy face texture.

The legacy content column stays intact for previous-release rollback.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260926_0016"
down_revision = "20260920_0015"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("user_body_face", sa.Column("model_assets_version", sa.Integer(), nullable=True))
    op.add_column("user_body_face", sa.Column("model_assets_content", sa.LargeBinary(), nullable=True))


def downgrade():
    op.drop_column("user_body_face", "model_assets_content")
    op.drop_column("user_body_face", "model_assets_version")
