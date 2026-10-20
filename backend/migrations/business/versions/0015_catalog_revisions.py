from alembic import op

revision = "b0015"
down_revision = "b0014"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      ALTER TABLE med.product ADD COLUMN revision_editors uuid[] NOT NULL DEFAULT '{}';
      ALTER TABLE med.party ADD COLUMN revision_editors uuid[] NOT NULL DEFAULT '{}';
      CREATE INDEX audit_resource_history
        ON med.audit_event(tenant_id,resource_type,resource_id,created_at DESC,id);
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
