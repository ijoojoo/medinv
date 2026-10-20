from alembic import op

revision = "b0012"
down_revision = "b0011"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE med.transfer (
      tenant_id uuid NOT NULL,id uuid NOT NULL,entity_id uuid NOT NULL,
      source_warehouse_id uuid NOT NULL,destination_warehouse_id uuid NOT NULL,
      destination_location_id uuid NOT NULL,number varchar(60) NOT NULL,
      state varchar(20) NOT NULL DEFAULT 'DRAFT'
        CHECK(state IN ('DRAFT','APPROVED','IN_TRANSIT','PART_RECEIVED','RECEIVED','CANCELLED')),
      version bigint NOT NULL DEFAULT 1 CHECK(version>0),reason varchar(200) NOT NULL,
      created_by uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
      reviewed_by uuid,reviewed_at timestamptz,review_note varchar(200),
      dispatch_posting_id uuid,transit_warehouse_id uuid,transit_location_id uuid,
      cancel_note varchar(200),
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,number),
      FOREIGN KEY(tenant_id,source_warehouse_id,entity_id)
        REFERENCES med.warehouse(tenant_id,id,entity_id),
      FOREIGN KEY(tenant_id,destination_warehouse_id,entity_id)
        REFERENCES med.warehouse(tenant_id,id,entity_id),
      FOREIGN KEY(tenant_id,destination_location_id,destination_warehouse_id)
        REFERENCES med.location(tenant_id,id,warehouse_id),
      FOREIGN KEY(tenant_id,transit_warehouse_id,entity_id)
        REFERENCES med.warehouse(tenant_id,id,entity_id),
      FOREIGN KEY(tenant_id,transit_location_id,transit_warehouse_id)
        REFERENCES med.location(tenant_id,id,warehouse_id),
      FOREIGN KEY(tenant_id,dispatch_posting_id) REFERENCES med.posting_batch(tenant_id,id),
      CHECK(source_warehouse_id<>destination_warehouse_id),
      CHECK((state IN ('IN_TRANSIT','PART_RECEIVED','RECEIVED'))=
        (dispatch_posting_id IS NOT NULL)),
      CHECK((dispatch_posting_id IS NULL)=(transit_location_id IS NULL)),
      CHECK((transit_warehouse_id IS NULL)=(transit_location_id IS NULL)),
      CHECK(state NOT IN ('APPROVED','IN_TRANSIT','PART_RECEIVED','RECEIVED') OR
        (reviewed_by IS NOT NULL AND reviewed_by<>created_by))
    );
    CREATE INDEX transfer_entity_list ON med.transfer(tenant_id,entity_id,created_at DESC,id);
    CREATE TABLE med.transfer_line (
      tenant_id uuid NOT NULL,id uuid NOT NULL,transfer_id uuid NOT NULL,
      line_no integer NOT NULL CHECK(line_no>0),balance_id uuid NOT NULL,
      qty numeric(20,6) NOT NULL CHECK(qty>0),
      product_snapshot jsonb NOT NULL,package_snapshot jsonb NOT NULL,
      created_by uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,transfer_id,balance_id),
      UNIQUE(tenant_id,transfer_id,line_no),UNIQUE(tenant_id,id,transfer_id),
      FOREIGN KEY(tenant_id,transfer_id) REFERENCES med.transfer(tenant_id,id),
      FOREIGN KEY(tenant_id,balance_id) REFERENCES med.stock_balance(tenant_id,id)
    );
    CREATE TABLE med.transfer_receipt (
      tenant_id uuid NOT NULL,id uuid NOT NULL,transfer_id uuid NOT NULL,
      number varchar(60) NOT NULL,posting_id uuid NOT NULL,note varchar(200) NOT NULL,
      created_by uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,number),UNIQUE(tenant_id,posting_id),
      UNIQUE(tenant_id,id,transfer_id),
      FOREIGN KEY(tenant_id,transfer_id) REFERENCES med.transfer(tenant_id,id),
      FOREIGN KEY(tenant_id,posting_id) REFERENCES med.posting_batch(tenant_id,id)
    );
    CREATE TABLE med.transfer_receipt_line (
      tenant_id uuid NOT NULL,id uuid NOT NULL,receipt_id uuid NOT NULL,
      transfer_id uuid NOT NULL,line_id uuid NOT NULL,
      qty numeric(20,6) NOT NULL CHECK(qty>0),
      quality_status varchar(20) NOT NULL CHECK(quality_status IN ('QUALIFIED','QUARANTINE')),
      created_by uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,receipt_id,line_id),
      FOREIGN KEY(tenant_id,receipt_id,transfer_id)
        REFERENCES med.transfer_receipt(tenant_id,id,transfer_id),
      FOREIGN KEY(tenant_id,line_id,transfer_id)
        REFERENCES med.transfer_line(tenant_id,id,transfer_id)
    );
    CREATE INDEX transfer_received_qty ON med.transfer_receipt_line(tenant_id,line_id);
    """)
    for table in ("transfer", "transfer_line", "transfer_receipt", "transfer_receipt_line"):
        op.execute(f"""ALTER TABLE med.{table} ENABLE ROW LEVEL SECURITY;
          ALTER TABLE med.{table} FORCE ROW LEVEL SECURITY;
          CREATE POLICY tenant_scope ON med.{table}
            USING(tenant_id=med.current_tenant()) WITH CHECK(tenant_id=med.current_tenant());
          GRANT SELECT,INSERT ON med.{table} TO med_runtime;""")
        if table != "transfer":
            op.execute(f"""CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE ON med.{table}
              FOR EACH ROW EXECUTE FUNCTION med.reject_fact_mutation();""")
    op.execute("GRANT UPDATE ON med.transfer TO med_runtime")


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
