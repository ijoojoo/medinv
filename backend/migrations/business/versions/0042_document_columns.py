from alembic import op

revision = "b0042"
down_revision = "b0041"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      ALTER TABLE med.purchase_order_line ADD COLUMN document_snapshot jsonb;
      CREATE TABLE med.document_column_profile (
        tenant_id uuid NOT NULL, entity_id uuid NOT NULL, owner_id uuid NOT NULL,
        document_type varchar(20) NOT NULL CHECK(document_type IN
          ('orders','receiving','inspection','posting')),
        mode varchar(10) NOT NULL CHECK(mode IN ('editor','view')),
        template jsonb NOT NULL CHECK(jsonb_typeof(template)='object'
          AND octet_length(template::text)<=40000),
        version bigint NOT NULL DEFAULT 1 CHECK(version>0),
        updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(tenant_id,entity_id,owner_id,document_type,mode),
        FOREIGN KEY(tenant_id,entity_id) REFERENCES med.legal_entity(tenant_id,id)
      );
      ALTER TABLE med.document_column_profile ENABLE ROW LEVEL SECURITY;
      ALTER TABLE med.document_column_profile FORCE ROW LEVEL SECURITY;
      CREATE POLICY tenant_scope ON med.document_column_profile
        USING(tenant_id=med.current_tenant()) WITH CHECK(tenant_id=med.current_tenant());
      GRANT SELECT,INSERT,UPDATE ON med.document_column_profile TO med_runtime;
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
