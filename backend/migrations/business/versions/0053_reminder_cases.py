"""Shared reminder follow-up, independent of personal read receipts and business state."""

from alembic import op

revision = "b0053"
down_revision = "b0052"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      ALTER TABLE med.management_record ALTER COLUMN record_key TYPE varchar(300);
      CREATE TABLE med.reminder_case (
        tenant_id uuid NOT NULL,id uuid NOT NULL,entity_id uuid NOT NULL,
        source varchar(20) NOT NULL CHECK(source IN
          ('work','certificate','scope','catalog_review','maintenance')),
        source_key varchar(180) NOT NULL,
        snapshot jsonb NOT NULL CHECK(jsonb_typeof(snapshot)='object'),
        state varchar(12) NOT NULL CHECK(state IN ('PROCESSING','CLOSED')),
        claimed_by uuid,assigned_staff_id uuid,assigned_name varchar(200) NOT NULL DEFAULT '',
        due_on date,closed_at timestamptz,closed_by uuid,
        version bigint NOT NULL DEFAULT 1 CHECK(version>0),
        created_by uuid NOT NULL,updated_by uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,entity_id,source_key),
        FOREIGN KEY(tenant_id,entity_id) REFERENCES med.legal_entity(tenant_id,id),
        FOREIGN KEY(tenant_id,assigned_staff_id) REFERENCES med.management_record(tenant_id,id),
        CHECK((state='CLOSED')=(closed_at IS NOT NULL AND closed_by IS NOT NULL))
      );
      ALTER TABLE med.reminder_case ENABLE ROW LEVEL SECURITY;
      ALTER TABLE med.reminder_case FORCE ROW LEVEL SECURITY;
      CREATE POLICY tenant_scope ON med.reminder_case
        USING(tenant_id=med.current_tenant()) WITH CHECK(tenant_id=med.current_tenant());
      GRANT SELECT,INSERT,UPDATE ON med.reminder_case TO med_runtime;
      CREATE INDEX reminder_case_followup ON
        med.reminder_case(tenant_id,entity_id,state,updated_at DESC,id);
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
