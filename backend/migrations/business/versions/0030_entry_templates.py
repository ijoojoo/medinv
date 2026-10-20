from alembic import op

revision = "b0030"
down_revision = "b0029"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE med.entry_template (
        tenant_id uuid NOT NULL,owner_id uuid NOT NULL,
        channel varchar(10) NOT NULL CHECK(channel IN ('purchase','sale')),
        template jsonb NOT NULL
          CHECK(jsonb_typeof(template)='object' AND octet_length(template::text)<=12000),
        version bigint NOT NULL DEFAULT 1 CHECK(version>0),
        updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(tenant_id,owner_id,channel)
      );
      ALTER TABLE med.entry_template ENABLE ROW LEVEL SECURITY;
      ALTER TABLE med.entry_template FORCE ROW LEVEL SECURITY;
      CREATE POLICY tenant_scope ON med.entry_template
        USING(tenant_id=med.current_tenant()) WITH CHECK(tenant_id=med.current_tenant());
      GRANT SELECT,INSERT,UPDATE ON med.entry_template TO med_runtime;
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
