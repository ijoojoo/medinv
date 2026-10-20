from alembic import op

revision = "b0039"
down_revision = "b0038"
branch_labels = None
depends_on = None


def upgrade():
    # Archiving removes retired warehouses from new-business choices without
    # rewriting historical stock, documents, or audit evidence.
    op.execute("ALTER TABLE med.warehouse ADD COLUMN archived_at timestamptz")


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
