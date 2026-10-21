"""Additive enterprise records and dictionary definitions, no historical rewrites."""

from alembic import op

revision = "b0048"
down_revision = "b0047"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      ALTER TABLE med.catalog_dictionary DROP CONSTRAINT catalog_dictionary_key_check;
      ALTER TABLE med.catalog_dictionary ADD CONSTRAINT catalog_dictionary_key_check CHECK(key IN
      (
        'dosage_form','trade_name','storage_conditions','base_unit','recommendation_level','abc_class',
        'drug_business_scope','device_business_scope','purchase_approval','inspection_qualified',
        'inspection_mixed','inspection_rejected','supplier_grade','customer_grade','delivery_method',
        'payment_method','department','staff_status','maintenance_type'));
      CREATE TABLE med.management_record (
        tenant_id uuid NOT NULL, id uuid NOT NULL, entity_id uuid NOT NULL,
        kind varchar(30) NOT NULL CHECK(kind IN
        ('staff','settings','approval_design','maintenance','notification_read')),
        record_key varchar(150) NOT NULL, payload jsonb NOT NULL
          CHECK(jsonb_typeof(payload)='object' AND octet_length(payload::text)<=100000),
        version bigint NOT NULL DEFAULT 1 CHECK(version>0),
        created_by uuid NOT NULL, updated_by uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT
        now(),
        PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,entity_id,kind,record_key),
        FOREIGN KEY(tenant_id,entity_id) REFERENCES med.legal_entity(tenant_id,id)
      );
      ALTER TABLE med.management_record ENABLE ROW LEVEL SECURITY;
      ALTER TABLE med.management_record FORCE ROW LEVEL SECURITY;
      CREATE POLICY tenant_scope ON med.management_record
        USING(tenant_id=med.current_tenant()) WITH CHECK(tenant_id=med.current_tenant());
      GRANT SELECT,INSERT,UPDATE ON med.management_record TO med_runtime;
      CREATE INDEX management_record_list ON
      med.management_record(tenant_id,entity_id,kind,updated_at DESC);
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
