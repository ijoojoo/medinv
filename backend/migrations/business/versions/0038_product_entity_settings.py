from alembic import op

revision = "b0038"
down_revision = "b0037"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE med.product_entity_settings (
        tenant_id uuid NOT NULL,product_id uuid NOT NULL,entity_id uuid NOT NULL,
        enabled boolean NOT NULL DEFAULT true,purchase_enabled boolean NOT NULL DEFAULT true,
        sales_enabled boolean NOT NULL DEFAULT true,recommendation_level varchar(60) NOT NULL DEFAULT '',
        abc_class varchar(60) NOT NULL DEFAULT '',abc_secondary_class varchar(60) NOT NULL DEFAULT '',
        business_note varchar(500) NOT NULL DEFAULT '',version bigint NOT NULL DEFAULT 1,
        updated_by uuid NOT NULL,updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(tenant_id,product_id,entity_id),
        FOREIGN KEY(tenant_id,product_id) REFERENCES med.product(tenant_id,id),
        FOREIGN KEY(tenant_id,entity_id) REFERENCES med.legal_entity(tenant_id,id));
      ALTER TABLE med.product_entity_settings ENABLE ROW LEVEL SECURITY;
      ALTER TABLE med.product_entity_settings FORCE ROW LEVEL SECURITY;
      CREATE POLICY tenant_scope ON med.product_entity_settings
        USING(tenant_id=med.current_tenant()) WITH CHECK(tenant_id=med.current_tenant());
      GRANT SELECT,INSERT,UPDATE ON med.product_entity_settings TO med_runtime;
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
