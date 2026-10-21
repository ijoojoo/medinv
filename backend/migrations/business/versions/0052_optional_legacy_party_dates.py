"""Allow scope-based party records without a fabricated whole-company expiry."""

from alembic import op

revision = "b0052"
down_revision = "b0051"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE med.party ALTER COLUMN valid_from DROP NOT NULL")
    op.execute("ALTER TABLE med.party ALTER COLUMN valid_until DROP NOT NULL")


def downgrade():
    raise RuntimeError("Restore a verified backup; never fabricate missing qualification dates")
