"""Separate quality holds from ordinary business availability switches."""

from alembic import op

revision = "b0049"
down_revision = "b0048"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE med.product_entity_settings "
        "ADD COLUMN quality_stopped boolean NOT NULL DEFAULT false"
    )


def downgrade():
    raise RuntimeError("Forward only: preserve quality stop decisions")
