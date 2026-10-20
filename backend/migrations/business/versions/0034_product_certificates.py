from alembic import op

revision = "b0034"
down_revision = "b0033"
branch_labels = None
depends_on = None


def upgrade():
    # Additive only: legacy qualification fields and historic document keys stay intact.
    # Legacy values are exposed as an unclassified read-only projection, not auto-verified.
    op.execute("""
      CREATE TABLE med.product_certificate (
        tenant_id uuid NOT NULL,id uuid NOT NULL,product_id uuid NOT NULL,
        certificate_type varchar(30) NOT NULL CHECK(certificate_type IN
          ('DRUG_APPROVAL','DEVICE_REGISTRATION','DEVICE_FILING')),
        number varchar(120) NOT NULL CHECK(length(btrim(number))>0),
        holder_name varchar(200) NOT NULL DEFAULT '',issuer varchar(200) NOT NULL DEFAULT '',
        covered_models varchar(500) NOT NULL DEFAULT '',issued_on date,
        validity_mode varchar(20) NOT NULL CHECK(validity_mode IN
          ('FIXED','NOT_STATED','UNCONFIRMED')),valid_from date,valid_until date,
        evidence_ref varchar(120) NOT NULL DEFAULT '',note varchar(500) NOT NULL DEFAULT '',
        state varchar(12) NOT NULL DEFAULT 'DRAFT' CHECK(state IN ('DRAFT','VERIFIED','INACTIVE')),
        version bigint NOT NULL DEFAULT 1 CHECK(version>0),
        revision_editors uuid[] NOT NULL DEFAULT ARRAY[]::uuid[],
        reviewed_by uuid,reviewed_at timestamptz,
        created_by uuid NOT NULL,updated_by uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(tenant_id,id),
        FOREIGN KEY(tenant_id,product_id) REFERENCES med.product(tenant_id,id),
        CHECK((validity_mode='FIXED' AND valid_from IS NOT NULL AND valid_until IS NOT NULL
          AND valid_from<=valid_until) OR
          (validity_mode<>'FIXED' AND valid_from IS NULL AND valid_until IS NULL)),
        CHECK(issued_on IS NULL OR valid_until IS NULL OR issued_on<=valid_until),
        CHECK(state<>'VERIFIED' OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL
          AND validity_mode<>'UNCONFIRMED' AND length(btrim(holder_name))>0
          AND length(btrim(issuer))>0 AND length(btrim(evidence_ref))>0
          AND (certificate_type='DEVICE_FILING' OR validity_mode='FIXED')))
      );
      CREATE UNIQUE INDEX product_certificate_number ON med.product_certificate
        (tenant_id,product_id,certificate_type,number);
      ALTER TABLE med.product_certificate ENABLE ROW LEVEL SECURITY;
      ALTER TABLE med.product_certificate FORCE ROW LEVEL SECURITY;
      CREATE POLICY tenant_scope ON med.product_certificate
        USING(tenant_id=med.current_tenant()) WITH CHECK(tenant_id=med.current_tenant());
      GRANT SELECT,INSERT,UPDATE ON med.product_certificate TO med_runtime;
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
