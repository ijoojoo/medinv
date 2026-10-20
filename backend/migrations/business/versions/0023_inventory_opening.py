from alembic import op

revision = "b0023"
down_revision = "b0022"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE med.inventory_opening (
      tenant_id uuid NOT NULL,id uuid NOT NULL,entity_id uuid NOT NULL,
      warehouse_id uuid NOT NULL,number varchar(60) NOT NULL,
      source_ref varchar(120) NOT NULL,reason varchar(200) NOT NULL,
      state varchar(20) NOT NULL DEFAULT 'DRAFT'
        CHECK(state IN ('DRAFT','APPROVED','REJECTED','CANCELLED','POSTED')),
      created_by uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
      reviewed_by uuid,reviewed_at timestamptz,review_note varchar(200),
      evidence_ref varchar(120),cancel_reason varchar(200),
      version bigint NOT NULL DEFAULT 1 CHECK(version>0),posting_id uuid,
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,number),
      FOREIGN KEY(tenant_id,warehouse_id,entity_id) REFERENCES
      med.warehouse(tenant_id,id,entity_id),
      FOREIGN KEY(tenant_id,posting_id) REFERENCES med.posting_batch(tenant_id,id),
      CHECK((state='POSTED')=(posting_id IS NOT NULL)),
      CHECK(state NOT IN ('APPROVED','POSTED') OR
        (reviewed_by IS NOT NULL AND reviewed_by<>created_by AND evidence_ref IS NOT NULL))
    );
    CREATE UNIQUE INDEX one_warehouse_opening ON med.inventory_opening(tenant_id,warehouse_id)
      WHERE state NOT IN ('CANCELLED','REJECTED');
    CREATE TABLE med.inventory_opening_line (
      tenant_id uuid NOT NULL,id uuid NOT NULL,opening_id uuid NOT NULL,line_no int NOT NULL,
      sku_id uuid NOT NULL,location_id uuid NOT NULL,lot_no varchar(100) NOT NULL,
      mfg_date date NOT NULL,expiry_date date NOT NULL,
      qty numeric(20,6) NOT NULL CHECK(qty>0),cost_amount numeric(18,2) NOT NULL
      CHECK(cost_amount>=0),
      product_snapshot jsonb NOT NULL,package_snapshot jsonb NOT NULL,
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,opening_id,line_no),
      UNIQUE(tenant_id,opening_id,sku_id,lot_no,location_id),
      FOREIGN KEY(tenant_id,opening_id) REFERENCES med.inventory_opening(tenant_id,id),
      FOREIGN KEY(tenant_id,sku_id) REFERENCES med.sku(tenant_id,id),
      FOREIGN KEY(tenant_id,location_id) REFERENCES med.location(tenant_id,id),
      CHECK(expiry_date>=mfg_date)
    );
    CREATE FUNCTION med.guard_inventory_opening() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_TABLE_NAME='inventory_opening' THEN
        IF OLD.state IN ('POSTED','CANCELLED','REJECTED') OR
          (NEW.tenant_id,NEW.id,NEW.entity_id,NEW.warehouse_id,NEW.number,NEW.source_ref,
           NEW.reason,NEW.created_by,NEW.created_at) IS DISTINCT FROM
          (OLD.tenant_id,OLD.id,OLD.entity_id,OLD.warehouse_id,OLD.number,OLD.source_ref,
           OLD.reason,OLD.created_by,OLD.created_at) THEN
          RAISE EXCEPTION 'Opening document is immutable';
        END IF;
      ELSE
        PERFORM 1 FROM med.inventory_opening WHERE tenant_id=NEW.tenant_id
          AND id=NEW.opening_id AND state='DRAFT' FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'Opening lines require a draft'; END IF;
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER opening_header_guard BEFORE UPDATE ON med.inventory_opening
      FOR EACH ROW EXECUTE FUNCTION med.guard_inventory_opening();
    CREATE TRIGGER opening_line_guard BEFORE INSERT ON med.inventory_opening_line
      FOR EACH ROW EXECUTE FUNCTION med.guard_inventory_opening();
    CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE ON med.inventory_opening_line
      FOR EACH ROW EXECUTE FUNCTION med.reject_fact_mutation();
    """)
    for table in ("inventory_opening", "inventory_opening_line"):
        op.execute(f"ALTER TABLE med.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE med.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""CREATE POLICY tenant_scope ON med.{table}
          USING(tenant_id=med.current_tenant()) WITH CHECK(tenant_id=med.current_tenant())""")
        op.execute(f"GRANT SELECT,INSERT ON med.{table} TO med_runtime")
    op.execute("GRANT UPDATE ON med.inventory_opening TO med_runtime")


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
