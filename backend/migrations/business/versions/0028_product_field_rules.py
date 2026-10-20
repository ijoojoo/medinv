from alembic import op

revision = "b0028"
down_revision = "b0027"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE med.product_field_rules (
        tenant_id uuid NOT NULL,id uuid NOT NULL,
        category varchar(10) NOT NULL CHECK(category IN ('DRUG','DEVICE')),
        fields jsonb NOT NULL CHECK(jsonb_typeof(fields)='array'),
        version bigint NOT NULL DEFAULT 1 CHECK(version>0),
        created_by uuid NOT NULL,updated_by uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,category)
      );
      ALTER TABLE med.product_field_rules ENABLE ROW LEVEL SECURITY;
      ALTER TABLE med.product_field_rules FORCE ROW LEVEL SECURITY;
      CREATE POLICY tenant_scope ON med.product_field_rules
        USING(tenant_id=med.current_tenant()) WITH CHECK(tenant_id=med.current_tenant());
      GRANT SELECT,INSERT,UPDATE ON med.product_field_rules TO med_runtime;
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
