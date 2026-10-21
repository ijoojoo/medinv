from alembic import op

revision = "b0047"
down_revision = "b0046"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE med.admission_policy (
        tenant_id uuid NOT NULL,id uuid NOT NULL,entity_id uuid NOT NULL,
        channel varchar(12) NOT NULL CHECK(channel IN ('PURCHASE','WHOLESALE')),
        version bigint NOT NULL DEFAULT 1 CHECK(version>0),
        active_version bigint NOT NULL DEFAULT 0
          CHECK(active_version>=0 AND active_version<=version),
        payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
        active_payload jsonb NOT NULL DEFAULT jsonb_build_object(
          'enabled',false,'own_scopes','[]'::jsonb,'party_scopes','[]'::jsonb),
        editors jsonb NOT NULL CHECK(jsonb_typeof(editors)='array'),
        created_by uuid NOT NULL,updated_by uuid NOT NULL,reviewed_by uuid,
        created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
        reviewed_at timestamptz,PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,entity_id,channel),
        FOREIGN KEY(tenant_id,entity_id) REFERENCES med.legal_entity(tenant_id,id)
      );
      ALTER TABLE med.admission_policy ENABLE ROW LEVEL SECURITY;
      ALTER TABLE med.admission_policy FORCE ROW LEVEL SECURITY;
      CREATE POLICY tenant_scope ON med.admission_policy
        USING(tenant_id=med.current_tenant()) WITH CHECK(tenant_id=med.current_tenant());
      GRANT SELECT,INSERT,UPDATE ON med.admission_policy TO med_runtime;
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
