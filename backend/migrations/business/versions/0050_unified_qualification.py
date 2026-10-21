"""Stage master changes separately from live trading data; scoped qualifications."""

from alembic import op

revision = "b0050"
down_revision = "b0049"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE med.catalog_change_bundle (
        tenant_id uuid NOT NULL,kind varchar(12) NOT NULL CHECK(kind IN ('product','party')),
        target_id uuid NOT NULL,payload jsonb NOT NULL DEFAULT '{}',
        before_snapshot jsonb NOT NULL DEFAULT '{}',editors uuid[] NOT NULL DEFAULT '{}',
        created_by uuid NOT NULL,updated_by uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(tenant_id,kind,target_id)
      );
      CREATE TABLE med.party_scope_profile (
        tenant_id uuid NOT NULL,party_id uuid NOT NULL,entity_id uuid NOT NULL,
        payload jsonb NOT NULL,version bigint NOT NULL DEFAULT 1,
        reviewed_by uuid NOT NULL,reviewed_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(tenant_id,party_id),
        FOREIGN KEY(tenant_id,party_id) REFERENCES med.party(tenant_id,id),
        FOREIGN KEY(tenant_id,entity_id) REFERENCES med.legal_entity(tenant_id,id)
      );
      CREATE TABLE med.qualification_rules (
        tenant_id uuid NOT NULL PRIMARY KEY,id uuid NOT NULL,
        payload jsonb NOT NULL,version bigint NOT NULL DEFAULT 1,
        updated_by uuid NOT NULL,updated_at timestamptz NOT NULL DEFAULT now()
      );
    """)
    for table in ("catalog_change_bundle", "party_scope_profile", "qualification_rules"):
        op.execute(f"""
          ALTER TABLE med.{table} ENABLE ROW LEVEL SECURITY;
          ALTER TABLE med.{table} FORCE ROW LEVEL SECURITY;
          CREATE POLICY tenant_scope ON med.{table}
            USING(tenant_id=med.current_tenant()) WITH CHECK(tenant_id=med.current_tenant());
          GRANT SELECT,INSERT,UPDATE ON med.{table} TO med_runtime;
        """)
    op.execute("GRANT DELETE ON med.catalog_change_bundle TO med_runtime")


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
