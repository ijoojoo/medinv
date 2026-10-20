from alembic import op

revision = "b0016"
down_revision = "b0015"
branch_labels = None
depends_on = None


def upgrade():
    # Existing and system-created locations display their code until explicitly named.
    op.execute("""
      ALTER TABLE med.location ADD COLUMN name varchar(200)
        CHECK(name IS NULL OR (length(btrim(name))>0 AND name=btrim(name)));
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
