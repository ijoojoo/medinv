from alembic import op

revision = "b0013"
down_revision = "b0012"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE med.quality_disposition (
      tenant_id uuid NOT NULL,id uuid NOT NULL,entity_id uuid NOT NULL,
      number varchar(60) NOT NULL,kind varchar(20) NOT NULL
        CHECK(kind IN ('RELEASE','WRITE_OFF')),
      reason varchar(200) NOT NULL,evidence varchar(200) NOT NULL,
      state varchar(20) NOT NULL DEFAULT 'DRAFT'
        CHECK(state IN ('DRAFT','APPROVED','POSTED','CANCELLED')),
      version bigint NOT NULL DEFAULT 1 CHECK(version>0),
      created_by uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
      reviewed_by uuid,reviewed_at timestamptz,review_note varchar(200),
      posting_id uuid,cancel_note varchar(200),
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,number),
      FOREIGN KEY(tenant_id,entity_id) REFERENCES med.legal_entity(tenant_id,id),
      FOREIGN KEY(tenant_id,posting_id) REFERENCES med.posting_batch(tenant_id,id),
      CHECK((state='POSTED')=(posting_id IS NOT NULL)),
      CHECK(state NOT IN ('APPROVED','POSTED') OR
        (reviewed_by IS NOT NULL AND reviewed_by<>created_by AND review_note IS NOT NULL))
    );
    CREATE INDEX quality_disposition_entity_list
      ON med.quality_disposition(tenant_id,entity_id,created_at DESC,id);
    CREATE TABLE med.quality_disposition_item (
      tenant_id uuid NOT NULL,id uuid NOT NULL,disposition_id uuid NOT NULL,
      balance_id uuid NOT NULL,qty numeric(20,6) NOT NULL CHECK(qty>0),
      source_quality varchar(20) NOT NULL CHECK(source_quality IN ('QUARANTINE','UNQUALIFIED')),
      lot_no varchar(200) NOT NULL,product_snapshot jsonb NOT NULL,package_snapshot jsonb NOT NULL,
      created_by uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,disposition_id),
      FOREIGN KEY(tenant_id,disposition_id) REFERENCES med.quality_disposition(tenant_id,id),
      FOREIGN KEY(tenant_id,balance_id) REFERENCES med.stock_balance(tenant_id,id)
    );
    CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE ON med.quality_disposition_item
      FOR EACH ROW EXECUTE FUNCTION med.reject_fact_mutation();
    CREATE FUNCTION med.protect_quality_disposition() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.state IN ('POSTED','CANCELLED') OR
        ROW(NEW.tenant_id,NEW.id,NEW.entity_id,NEW.number,NEW.kind,NEW.reason,NEW.evidence,
            NEW.created_by,NEW.created_at) IS DISTINCT FROM
        ROW(OLD.tenant_id,OLD.id,OLD.entity_id,OLD.number,OLD.kind,OLD.reason,OLD.evidence,
            OLD.created_by,OLD.created_at) THEN
        RAISE EXCEPTION 'quality disposition facts are immutable';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER protect_facts BEFORE UPDATE ON med.quality_disposition
      FOR EACH ROW EXECUTE FUNCTION med.protect_quality_disposition();
    """)
    for table in ("quality_disposition", "quality_disposition_item"):
        op.execute(f"""ALTER TABLE med.{table} ENABLE ROW LEVEL SECURITY;
          ALTER TABLE med.{table} FORCE ROW LEVEL SECURITY;
          CREATE POLICY tenant_scope ON med.{table}
            USING(tenant_id=med.current_tenant()) WITH CHECK(tenant_id=med.current_tenant());
          GRANT SELECT,INSERT ON med.{table} TO med_runtime;""")
    op.execute("GRANT UPDATE ON med.quality_disposition TO med_runtime")


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
