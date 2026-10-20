from alembic import op

revision = "c0003"
down_revision = "c0002"
branch_labels = None
depends_on = None


def upgrade():
    # PostgreSQL row locking requires UPDATE on at least one column.
    # Name and activation state remain unavailable to the auth runtime role.
    op.execute("GRANT UPDATE (id) ON control.tenant TO control_auth")


def downgrade():
    op.execute("REVOKE UPDATE (id) ON control.tenant FROM control_auth")
