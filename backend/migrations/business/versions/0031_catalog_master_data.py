from alembic import op

revision = "b0031"
down_revision = "b0030"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      ALTER TABLE med.party
        ADD COLUMN short_name varchar(100) NOT NULL DEFAULT '',
        ADD COLUMN search_alias varchar(100) NOT NULL DEFAULT '',
        ADD COLUMN organization_type varchar(30) NOT NULL DEFAULT 'UNKNOWN'
          CHECK(organization_type IN ('UNKNOWN','MANUFACTURER','WHOLESALER','RETAILER',
            'MEDICAL_INSTITUTION','OTHER')),
        ADD COLUMN contact_name varchar(100) NOT NULL DEFAULT '',
        ADD COLUMN contact_phone varchar(50) NOT NULL DEFAULT '',
        ADD COLUMN legal_representative varchar(100) NOT NULL DEFAULT '',
        ADD COLUMN registered_address varchar(300) NOT NULL DEFAULT '',
        ADD COLUMN business_address varchar(300) NOT NULL DEFAULT '',
        ADD COLUMN delivery_address varchar(300) NOT NULL DEFAULT '',
        ADD COLUMN business_scope_text varchar(1000) NOT NULL DEFAULT '',
        ADD COLUMN qualification_archive_ref varchar(120) NOT NULL DEFAULT '',
        ADD COLUMN authorization_archive_ref varchar(120) NOT NULL DEFAULT '',
        ADD COLUMN quality_agreement_ref varchar(120) NOT NULL DEFAULT '',
        ADD CONSTRAINT party_entity_identity UNIQUE(tenant_id,entity_id,id);
      CREATE TABLE med.catalog_code (
        tenant_id uuid NOT NULL,id uuid NOT NULL,
        sku_id uuid,party_id uuid,entity_id uuid,packaging_id uuid,
        scheme varchar(30) NOT NULL CHECK(scheme IN ('BARCODE','UDI_DI','NHSA_DRUG',
          'NHSA_CONSUMABLE','NHSA_IVD','NHSA_ORGANIZATION','MEITUAN','ELEME','TAOBAO','WANGDIANTONG','CUSTOM')),
        namespace varchar(80) NOT NULL DEFAULT '',
        external_code varchar(128) NOT NULL
          CHECK(external_code<>'' AND external_code!~'[[:space:][:cntrl:]]'),
        evidence_ref varchar(120) NOT NULL CHECK(length(btrim(evidence_ref))>0),
        state varchar(10) NOT NULL DEFAULT 'ACTIVE' CHECK(state IN ('ACTIVE','INACTIVE')),
        version bigint NOT NULL DEFAULT 1 CHECK(version>0),
        created_by uuid NOT NULL,updated_by uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(tenant_id,id),
        CHECK((sku_id IS NOT NULL AND party_id IS NULL AND entity_id IS NULL)
          OR (party_id IS NOT NULL AND sku_id IS NULL
            AND entity_id IS NOT NULL AND packaging_id IS NULL)),
        CHECK((scheme IN ('BARCODE','UDI_DI','NHSA_DRUG','NHSA_CONSUMABLE',
          'NHSA_IVD','NHSA_ORGANIZATION') AND namespace='')
          OR (scheme IN ('MEITUAN','ELEME','TAOBAO','WANGDIANTONG','CUSTOM')
            AND length(btrim(namespace))>0)),
        FOREIGN KEY(tenant_id,sku_id) REFERENCES med.sku(tenant_id,id),
        FOREIGN KEY(tenant_id,entity_id,party_id) REFERENCES med.party(tenant_id,entity_id,id),
        FOREIGN KEY(tenant_id,packaging_id) REFERENCES med.packaging(tenant_id,id)
      );
      CREATE UNIQUE INDEX catalog_code_active ON med.catalog_code
        (tenant_id,entity_id,scheme,namespace,external_code)
        NULLS NOT DISTINCT WHERE state='ACTIVE';
      CREATE INDEX catalog_code_sku ON med.catalog_code(tenant_id,sku_id,created_at);
      CREATE INDEX catalog_code_party ON med.catalog_code(tenant_id,party_id,created_at);
      ALTER TABLE med.catalog_code ENABLE ROW LEVEL SECURITY;
      ALTER TABLE med.catalog_code FORCE ROW LEVEL SECURITY;
      CREATE POLICY tenant_scope ON med.catalog_code
        USING(tenant_id=med.current_tenant()) WITH CHECK(tenant_id=med.current_tenant());
      GRANT SELECT,INSERT,UPDATE ON med.catalog_code TO med_runtime;
      CREATE FUNCTION med.protect_catalog_code() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'catalog code history cannot be deleted'; END IF;
        IF ROW(NEW.tenant_id,NEW.id,NEW.sku_id,NEW.party_id,NEW.entity_id,NEW.packaging_id,
          NEW.scheme,NEW.namespace,NEW.external_code,NEW.evidence_ref,NEW.created_by,NEW.created_at)
          IS DISTINCT FROM ROW(OLD.tenant_id,OLD.id,OLD.sku_id,OLD.party_id,OLD.entity_id,
          OLD.packaging_id,OLD.scheme,OLD.namespace,OLD.external_code,OLD.evidence_ref,
          OLD.created_by,OLD.created_at) THEN
          RAISE EXCEPTION 'disable the old mapping and add a corrected mapping';
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER protect_catalog_code BEFORE UPDATE OR DELETE ON med.catalog_code
        FOR EACH ROW EXECUTE FUNCTION med.protect_catalog_code();
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
