from alembic import op

revision = "b0045"
down_revision = "b0044"
branch_labels = None
depends_on = None


def upgrade():
    # Each entity's party owns its evidence; shared organization identity is not mutated.
    # Existing license fields and trade gates remain intact until structured admission lands.
    op.execute("""
      CREATE TABLE med.party_certificate (
        tenant_id uuid NOT NULL,id uuid NOT NULL,party_id uuid NOT NULL,
        certificate_type varchar(40) NOT NULL CHECK(certificate_type IN
          ('BUSINESS_LICENSE','DRUG_BUSINESS_LICENSE','DRUG_PRODUCTION_LICENSE',
           'DEVICE_BUSINESS_LICENSE','DEVICE_BUSINESS_FILING','DEVICE_PRODUCTION_LICENSE',
           'DEVICE_PRODUCTION_FILING','MEDICAL_INSTITUTION_LICENSE','AUTHORIZATION','OTHER')),
        number varchar(120) NOT NULL CHECK(length(btrim(number))>0),
        holder_name varchar(200) NOT NULL DEFAULT '',issuer varchar(200) NOT NULL DEFAULT '',
        business_uses text[] NOT NULL CHECK(cardinality(business_uses) BETWEEN 1 AND 2
          AND business_uses <@ ARRAY['SUPPLY','PURCHASE']::text[]
          AND array_position(business_uses,NULL) IS NULL
          AND (cardinality(business_uses)=1 OR business_uses[1]<>business_uses[2])),
        scope_text varchar(1000) NOT NULL DEFAULT '',
        authorized_name varchar(100) NOT NULL DEFAULT '',
        authorized_phone varchar(50) NOT NULL DEFAULT '',
        authorization_duties varchar(500) NOT NULL DEFAULT '',
        issued_on date,
        validity_mode varchar(20) NOT NULL CHECK(validity_mode IN
          ('FIXED','LONG_TERM','NOT_STATED','UNCONFIRMED')),valid_from date,valid_until date,
        evidence_ref varchar(120) NOT NULL DEFAULT '',note varchar(500) NOT NULL DEFAULT '',
        state varchar(12) NOT NULL DEFAULT 'DRAFT' CHECK(state IN ('DRAFT','VERIFIED','INACTIVE')),
        version bigint NOT NULL DEFAULT 1 CHECK(version>0),
        revision_editors uuid[] NOT NULL DEFAULT ARRAY[]::uuid[],
        reviewed_by uuid,reviewed_at timestamptz,
        created_by uuid NOT NULL,updated_by uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(tenant_id,id),
        FOREIGN KEY(tenant_id,party_id) REFERENCES med.party(tenant_id,id),
        CHECK((validity_mode='FIXED' AND valid_from IS NOT NULL AND valid_until IS NOT NULL
          AND valid_from<=valid_until) OR
          (validity_mode<>'FIXED' AND valid_from IS NULL AND valid_until IS NULL)),
        CHECK(issued_on IS NULL OR valid_until IS NULL OR issued_on<=valid_until),
        CHECK(certificate_type='AUTHORIZATION' OR
          (authorized_name='' AND authorized_phone='' AND authorization_duties='')),
        CHECK(state<>'VERIFIED' OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL
          AND validity_mode<>'UNCONFIRMED' AND length(btrim(holder_name))>0
          AND length(btrim(issuer))>0 AND length(btrim(evidence_ref))>0
          AND (certificate_type<>'AUTHORIZATION' OR
            (length(btrim(authorized_name))>0 AND length(btrim(authorized_phone))>0
             AND length(btrim(authorization_duties))>0))))
      );
      CREATE UNIQUE INDEX party_certificate_number ON med.party_certificate
        (tenant_id,party_id,certificate_type,lower(btrim(number)));
      ALTER TABLE med.party_certificate ENABLE ROW LEVEL SECURITY;
      ALTER TABLE med.party_certificate FORCE ROW LEVEL SECURITY;
      CREATE POLICY tenant_scope ON med.party_certificate
        USING(tenant_id=med.current_tenant()) WITH CHECK(tenant_id=med.current_tenant());
      GRANT SELECT,INSERT,UPDATE ON med.party_certificate TO med_runtime;
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
