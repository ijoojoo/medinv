"""Additive MDM configuration, typed profile, explicit relations and history."""

from alembic import op

revision = "b0035"
down_revision = "b0034"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      ALTER TABLE med.product DROP CONSTRAINT product_category_check;
      ALTER TABLE med.product ADD CHECK(category IN
        ('DRUG','HERB','DEVICE','FOOD','HEALTH','FSMP','INFANT','COSMETIC','DAILY','SERVICE'));
      ALTER TABLE med.product DROP CONSTRAINT product_profile_category;
      ALTER TABLE med.product ADD CONSTRAINT product_profile_category CHECK(
        (category<>'DEVICE' OR (generic_name='' AND dosage_form='' AND authorization_holder=''
          AND authorization_holder_address='' AND drug_business_scope='')) AND
        (category='DEVICE' OR (device_class='UNKNOWN' AND device_model='' AND registrant_name=''
          AND registrant_address='' AND device_business_scope='')));
      ALTER TABLE med.product_code_rule DROP CONSTRAINT product_code_rule_category_check;
      ALTER TABLE med.product_code_rule ADD CHECK(category IN
        ('DRUG','HERB','DEVICE','FOOD','HEALTH','FSMP','INFANT','COSMETIC','DAILY','SERVICE'));
      CREATE TABLE med.catalog_schema (
        tenant_id uuid NOT NULL,id uuid NOT NULL,revision bigint NOT NULL,
        state varchar(12) NOT NULL CHECK(state IN ('DRAFT','PUBLISHED')),
        base_revision bigint NOT NULL,version bigint NOT NULL DEFAULT 1,
        payload jsonb NOT NULL,created_by uuid NOT NULL,updated_by uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),published_at timestamptz,
        PRIMARY KEY(tenant_id,id));
      CREATE UNIQUE INDEX catalog_schema_published ON med.catalog_schema(tenant_id,revision)
        WHERE state='PUBLISHED';
      CREATE FUNCTION med.immutable_published_schema() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN IF OLD.state='PUBLISHED' THEN RAISE EXCEPTION 'Published schema is immutable';
      END IF; RETURN NEW; END $$;
      CREATE TRIGGER immutable_schema BEFORE UPDATE OR DELETE ON med.catalog_schema
        FOR EACH ROW EXECUTE FUNCTION med.immutable_published_schema();
      CREATE TABLE med.catalog_category (
        tenant_id uuid NOT NULL,id uuid NOT NULL,node_code varchar(60) NOT NULL,
        name varchar(120) NOT NULL,parent_id uuid,sort_order integer NOT NULL,
        enabled boolean NOT NULL,is_assignable boolean NOT NULL,field_profile_id varchar(30),
        PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,node_code),
        FOREIGN KEY(tenant_id,parent_id) REFERENCES med.catalog_category(tenant_id,id)
          DEFERRABLE INITIALLY DEFERRED);
      ALTER TABLE med.product ADD COLUMN category_node_id uuid,
        ADD COLUMN form_revision bigint,ADD COLUMN data_version bigint NOT NULL DEFAULT 1,
        ADD COLUMN record_status varchar(12) NOT NULL DEFAULT 'ACTIVE'
          CHECK(record_status IN ('ACTIVE','INACTIVE')),
        ADD FOREIGN KEY(tenant_id,category_node_id) REFERENCES med.catalog_category(tenant_id,id);
      CREATE TABLE med.catalog_organization (
        tenant_id uuid NOT NULL,id uuid NOT NULL,name varchar(200) NOT NULL,
        legal_code varchar(60) NOT NULL DEFAULT '',kind varchar(15) NOT NULL
          CHECK(kind IN ('ORGANIZATION','DEPARTMENT')),parent_id uuid,
        state varchar(12) NOT NULL DEFAULT 'ACTIVE' CHECK(state IN ('ACTIVE','INACTIVE')),
        version bigint NOT NULL DEFAULT 1,created_by uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(tenant_id,id),
        FOREIGN KEY(tenant_id,parent_id) REFERENCES med.catalog_organization(tenant_id,id));
      CREATE UNIQUE INDEX catalog_organization_legal_code ON med.catalog_organization(tenant_id,legal_code)
        WHERE legal_code<>'' AND kind='ORGANIZATION';
      CREATE TABLE med.product_master_detail (
        tenant_id uuid NOT NULL,product_id uuid NOT NULL,
        regulatory_subtype varchar(60),base_unit varchar(30),
        service_department uuid,service_price numeric(20,6),price_note varchar(500),
        temperature_min numeric(10,4),temperature_max numeric(10,4),humidity_max numeric(10,4),
        shelf_life_months integer,lot_management boolean,expiry_management boolean,
        sterile boolean,single_use boolean,brand varchar(500),short_name varchar(500),
        herb_origin varchar(500),herb_processing varchar(500),execution_standard varchar(500),
        herb_grade varchar(500),herb_part varchar(500),ingredients varchar(500),allergens varchar(500),
        nutrition varchar(500),target_population varchar(500),unsuitable_population varchar(500),
        health_function varchar(500),fsmp_use varchar(500),infant_stage varchar(500),
        cosmetic_purpose varchar(500),cosmetic_usage varchar(500),material varchar(500),daily_use varchar(500),
        service_duration_minutes integer,service_instructions varchar(500),custom_values jsonb NOT NULL DEFAULT '{}',
        PRIMARY KEY(tenant_id,product_id),
        FOREIGN KEY(tenant_id,product_id) REFERENCES med.product(tenant_id,id),
        FOREIGN KEY(tenant_id,service_department) REFERENCES med.catalog_organization(tenant_id,id),
        CHECK(temperature_min IS NULL OR temperature_max IS NULL OR temperature_min<=temperature_max),
        CHECK(humidity_max IS NULL OR humidity_max BETWEEN 0 AND 100),
        CHECK(service_price IS NULL OR service_price>=0),
        CHECK(shelf_life_months IS NULL OR shelf_life_months>0),
        CHECK(service_duration_minutes IS NULL OR service_duration_minutes>0));
      CREATE TABLE med.product_organization_role (
        tenant_id uuid NOT NULL,id uuid NOT NULL,product_id uuid NOT NULL,organization_id uuid NOT NULL,
        role_code varchar(40) NOT NULL,evidence_ref varchar(120) NOT NULL DEFAULT '',
        source_certificate_id uuid,source_certificate_version bigint,
        PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,product_id,organization_id,role_code),
        FOREIGN KEY(tenant_id,product_id) REFERENCES med.product(tenant_id,id),
        FOREIGN KEY(tenant_id,organization_id) REFERENCES med.catalog_organization(tenant_id,id),
        FOREIGN KEY(tenant_id,source_certificate_id) REFERENCES med.product_certificate(tenant_id,id));
      CREATE TABLE med.product_certificate_link (
        tenant_id uuid NOT NULL,product_id uuid NOT NULL,certificate_id uuid NOT NULL,
        certificate_version bigint NOT NULL,snapshot jsonb NOT NULL,
        PRIMARY KEY(tenant_id,product_id,certificate_id),
        FOREIGN KEY(tenant_id,product_id) REFERENCES med.product(tenant_id,id),
        FOREIGN KEY(tenant_id,certificate_id) REFERENCES med.product_certificate(tenant_id,id));
      CREATE TABLE med.product_master_revision (
        tenant_id uuid NOT NULL,id uuid NOT NULL,product_id uuid NOT NULL,data_version bigint NOT NULL,
        form_revision bigint NOT NULL,category_path jsonb NOT NULL,snapshot jsonb NOT NULL,
        created_by uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,product_id,data_version),
        FOREIGN KEY(tenant_id,product_id) REFERENCES med.product(tenant_id,id));
    """)
    for table in (
        "catalog_schema",
        "catalog_category",
        "catalog_organization",
        "product_master_detail",
        "product_organization_role",
        "product_certificate_link",
        "product_master_revision",
    ):
        op.execute(f"""ALTER TABLE med.{table} ENABLE ROW LEVEL SECURITY;
          ALTER TABLE med.{table} FORCE ROW LEVEL SECURITY;
          CREATE POLICY tenant_scope ON med.{table} USING(tenant_id=med.current_tenant())
            WITH CHECK(tenant_id=med.current_tenant());
          GRANT SELECT,INSERT,UPDATE ON med.{table} TO med_runtime;""")
    op.execute(
        "GRANT DELETE ON med.product_organization_role,med.product_certificate_link TO med_runtime"
    )


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
