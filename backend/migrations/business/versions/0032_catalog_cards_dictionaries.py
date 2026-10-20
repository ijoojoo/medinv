from alembic import op

revision = "b0032"
down_revision = "b0031"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("product", "party"):
        op.execute(f"""ALTER TABLE med.{table}
          ADD COLUMN recommendation_level varchar(60) NOT NULL DEFAULT '',
          ADD COLUMN abc_class varchar(60) NOT NULL DEFAULT '',
          ADD COLUMN abc_secondary_class varchar(60) NOT NULL DEFAULT '',
          ADD COLUMN business_note varchar(500) NOT NULL DEFAULT ''""")
    op.execute("""
      CREATE TABLE med.catalog_dictionary (
        tenant_id uuid NOT NULL,id uuid NOT NULL,key varchar(40) NOT NULL,
        mode varchar(10) NOT NULL CHECK(mode IN ('SUGGEST','STRICT')),
        items jsonb NOT NULL CHECK(jsonb_typeof(items)='array'
          AND jsonb_array_length(items)<=200 AND octet_length(items::text)<=400000),
        version bigint NOT NULL DEFAULT 1 CHECK(version>0),
        created_by uuid NOT NULL,updated_by uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,key),
        CHECK(key IN ('dosage_form','trade_name','storage_conditions','base_unit',
          'recommendation_level','abc_class'))
      );
      ALTER TABLE med.catalog_dictionary ENABLE ROW LEVEL SECURITY;
      ALTER TABLE med.catalog_dictionary FORCE ROW LEVEL SECURITY;
      CREATE POLICY tenant_scope ON med.catalog_dictionary
        USING(tenant_id=med.current_tenant()) WITH CHECK(tenant_id=med.current_tenant());
      GRANT SELECT,INSERT,UPDATE ON med.catalog_dictionary TO med_runtime;
      DO $$ DECLARE c record; BEGIN
        FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='med.catalog_code'::regclass
          AND contype='c' AND pg_get_constraintdef(oid) LIKE '%scheme%'
        LOOP EXECUTE format('ALTER TABLE med.catalog_code DROP CONSTRAINT %I',c.conname); END LOOP;
      END $$;
      ALTER TABLE med.catalog_code ADD CONSTRAINT catalog_code_scheme_check
        CHECK(scheme IN ('BARCODE','UDI_DI','NHSA_DRUG','NHSA_CONSUMABLE','NHSA_IVD',
          'NHSA_ORGANIZATION','MSFX','MEITUAN','ELEME','TAOBAO','WANGDIANTONG','CUSTOM'));
      ALTER TABLE med.catalog_code ADD CONSTRAINT catalog_code_namespace_check
        CHECK((scheme IN ('BARCODE','UDI_DI','NHSA_DRUG','NHSA_CONSUMABLE',
          'NHSA_IVD','NHSA_ORGANIZATION') AND namespace='')
          OR (scheme IN ('MSFX','MEITUAN','ELEME','TAOBAO','WANGDIANTONG','CUSTOM')
            AND length(btrim(namespace))>0));
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
