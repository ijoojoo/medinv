"""Published purchase approval definitions and immutable per-revision bindings."""

from alembic import op

revision = "b0054"
down_revision = "b0053"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE med.approval_policy (
        tenant_id uuid NOT NULL,entity_id uuid NOT NULL,id uuid NOT NULL,
        enabled boolean NOT NULL,design_id uuid NOT NULL,design_version bigint NOT NULL,
        definition jsonb NOT NULL CHECK(jsonb_typeof(definition)='object'),
        version bigint NOT NULL DEFAULT 1 CHECK(version>0),
        updated_by uuid NOT NULL,updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(tenant_id,entity_id),UNIQUE(tenant_id,id),
        FOREIGN KEY(tenant_id,entity_id) REFERENCES med.legal_entity(tenant_id,id),
        FOREIGN KEY(tenant_id,design_id) REFERENCES med.management_record(tenant_id,id)
      );
      CREATE TABLE med.purchase_approval_instance (
        tenant_id uuid NOT NULL,id uuid NOT NULL,entity_id uuid NOT NULL,order_id uuid NOT NULL,
        revision_no integer NOT NULL,policy_version bigint NOT NULL,
        definition jsonb NOT NULL CHECK(jsonb_typeof(definition)='object'),
        state varchar(20) NOT NULL CHECK(state IN
          ('PENDING','RETURNED','APPROVED','CANCELLED','SUPERSEDED')),
        step_index integer NOT NULL DEFAULT 0 CHECK(step_index>=0),
        round integer NOT NULL DEFAULT 1 CHECK(round>0),
        decisions jsonb NOT NULL DEFAULT '[]' CHECK(jsonb_typeof(decisions)='array'),
        created_by uuid NOT NULL,updated_by uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,order_id,revision_no),
        FOREIGN KEY(tenant_id,entity_id) REFERENCES med.legal_entity(tenant_id,id),
        FOREIGN KEY(tenant_id,order_id) REFERENCES med.purchase_order(tenant_id,id)
      );
    """)
    for table in ("approval_policy", "purchase_approval_instance"):
        op.execute(f"""
          ALTER TABLE med.{table} ENABLE ROW LEVEL SECURITY;
          ALTER TABLE med.{table} FORCE ROW LEVEL SECURITY;
          CREATE POLICY tenant_scope ON med.{table}
            USING(tenant_id=med.current_tenant()) WITH CHECK(tenant_id=med.current_tenant());
          GRANT SELECT,INSERT,UPDATE ON med.{table} TO med_runtime;
        """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
