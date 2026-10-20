from alembic import op

revision = "b0040"
down_revision = "b0039"
branch_labels = None
depends_on = None


def upgrade():
    # Existing party IDs and document references remain intact. Old identities
    # require explicit reconciliation; names alone cannot safely backfill them.
    op.execute("""ALTER TABLE med.party ADD COLUMN organization_id uuid,
        ADD FOREIGN KEY(tenant_id,organization_id)
        REFERENCES med.catalog_organization(tenant_id,id);
        CREATE UNIQUE INDEX party_entity_organization
        ON med.party(tenant_id,entity_id,organization_id)
        WHERE organization_id IS NOT NULL;""")


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
