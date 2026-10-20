from alembic import op

revision = "b0033"
down_revision = "b0032"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      ALTER TABLE med.product
        ADD COLUMN drug_business_scope varchar(100) NOT NULL DEFAULT '',
        ADD COLUMN device_business_scope varchar(100) NOT NULL DEFAULT '',
        ADD COLUMN quality_note varchar(500) NOT NULL DEFAULT '';
      DO $$ DECLARE c record; BEGIN
        FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='med.catalog_dictionary'::regclass
          AND contype='c' AND pg_get_constraintdef(oid) LIKE '%key%'
        LOOP EXECUTE format('ALTER TABLE med.catalog_dictionary DROP CONSTRAINT %I',c.conname);
        END LOOP;
      END $$;
      ALTER TABLE med.catalog_dictionary ADD CONSTRAINT catalog_dictionary_key_check
        CHECK(key IN ('dosage_form','trade_name','storage_conditions','base_unit',
          'recommendation_level','abc_class','drug_business_scope','device_business_scope'));
      CREATE TABLE med.product_code_rule (
        tenant_id uuid NOT NULL, id uuid NOT NULL, category varchar(10) NOT NULL
          CHECK(category IN ('DRUG','DEVICE')),
        enabled boolean NOT NULL DEFAULT true,
        prefix varchar(12) NOT NULL CHECK(prefix ~ '^[A-Za-z0-9_-]*$'),
        date_format varchar(8) NOT NULL CHECK(date_format IN ('NONE','YYYY','YYYYMM','YYYYMMDD')),
        digits integer NOT NULL CHECK(digits BETWEEN 1 AND 12),
        next_number bigint NOT NULL CHECK(next_number BETWEEN 1 AND 1000000000000),
        version bigint NOT NULL DEFAULT 1 CHECK(version>0),
        created_by uuid NOT NULL,updated_by uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,category)
      );
      CREATE TABLE med.sku_reference_price (
        tenant_id uuid NOT NULL,id uuid NOT NULL,sku_id uuid NOT NULL,
        purchase_price numeric(18,6) CHECK(purchase_price>=0),
        wholesale_price numeric(18,6) CHECK(wholesale_price>=0),
        retail_price numeric(18,6) CHECK(retail_price>=0),
        note varchar(500) NOT NULL DEFAULT '',version bigint NOT NULL DEFAULT 1 CHECK(version>0),
        created_by uuid NOT NULL,updated_by uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,sku_id),
        FOREIGN KEY(tenant_id,sku_id) REFERENCES med.sku(tenant_id,id)
      );
    """)
    for table in ("product_code_rule", "sku_reference_price"):
        op.execute(f"""
          ALTER TABLE med.{table} ENABLE ROW LEVEL SECURITY;
          ALTER TABLE med.{table} FORCE ROW LEVEL SECURITY;
          CREATE POLICY tenant_scope ON med.{table}
            USING(tenant_id=med.current_tenant()) WITH CHECK(tenant_id=med.current_tenant());
          GRANT SELECT,INSERT,UPDATE ON med.{table} TO med_runtime;
        """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
