from alembic import op

revision = "b0004"
down_revision = "b0003"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE med.credit_account (
      tenant_id uuid NOT NULL, entity_id uuid NOT NULL, customer_id uuid NOT NULL,
      credit_limit numeric(20,2) NOT NULL DEFAULT 0 CHECK(credit_limit>=0),
      exposure numeric(20,2) NOT NULL DEFAULT 0 CHECK(exposure>=0),
      receivable numeric(20,2) NOT NULL DEFAULT 0 CHECK(receivable>=0),
      valid_until date NOT NULL, version bigint NOT NULL DEFAULT 1 CHECK(version>0),
      updated_by uuid NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,entity_id,customer_id),
      FOREIGN KEY(tenant_id,customer_id,entity_id) REFERENCES med.party(tenant_id,id,entity_id)
    );
    CREATE TABLE med.sales_order (
      tenant_id uuid NOT NULL, id uuid NOT NULL, entity_id uuid NOT NULL,
      customer_id uuid NOT NULL, warehouse_id uuid NOT NULL, number varchar(60) NOT NULL,
      state varchar(20) NOT NULL DEFAULT 'DRAFT'
        CHECK(state IN ('DRAFT','APPROVED','CANCELLED','CLOSED','FULFILLED')),
      delivery_address varchar(500) NOT NULL,
      minimum_shelf_days integer NOT NULL CHECK(minimum_shelf_days BETWEEN 0 AND 3650),
      credit_days integer NOT NULL CHECK(credit_days BETWEEN 0 AND 365),
      gross_amount numeric(20,2) NOT NULL CHECK(gross_amount>=0),
      remaining_exposure numeric(20,2) NOT NULL DEFAULT 0 CHECK(remaining_exposure>=0),
      customer_snapshot jsonb, approved_by uuid, approved_at timestamptz,
      created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
      version bigint NOT NULL DEFAULT 1 CHECK(version>0),
      PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,number),
      FOREIGN KEY(tenant_id,customer_id,entity_id) REFERENCES med.party(tenant_id,id,entity_id),
      FOREIGN KEY(tenant_id,warehouse_id,entity_id) REFERENCES med.warehouse(tenant_id,id,entity_id)
    );
    CREATE TABLE med.sales_order_line (
      tenant_id uuid NOT NULL, id uuid NOT NULL, order_id uuid NOT NULL,
      line_no integer NOT NULL CHECK(line_no>0), sku_id uuid NOT NULL,
      qty numeric(20,6) NOT NULL CHECK(qty>0),
      unit_price numeric(20,6) NOT NULL CHECK(unit_price>=0),
      tax_rate numeric(7,6) NOT NULL CHECK(tax_rate BETWEEN 0 AND 1),
      gross_amount numeric(20,2) NOT NULL CHECK(gross_amount>=0),
      net_amount numeric(20,2) NOT NULL CHECK(net_amount>=0),
      PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,order_id,line_no),
      FOREIGN KEY(tenant_id,order_id) REFERENCES med.sales_order(tenant_id,id),
      FOREIGN KEY(tenant_id,sku_id) REFERENCES med.sku(tenant_id,id)
    );
    CREATE TABLE med.shipment (
      tenant_id uuid NOT NULL, id uuid NOT NULL, order_id uuid NOT NULL,
      reservation_id uuid NOT NULL, number varchar(60) NOT NULL,
      state varchar(20) NOT NULL DEFAULT 'ALLOCATED'
        CHECK(state IN ('ALLOCATED','PICKED','VERIFIED','POSTED','CANCELLED')),
      created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
      client_operation_id uuid NOT NULL, request_hash char(64) NOT NULL,
      creation_result jsonb NOT NULL,
      picked_by uuid, picked_at timestamptz, pick_note varchar(500),
      verified_by uuid, verified_at timestamptz, verify_note varchar(500),
      shipping_ref varchar(120),
      posting_id uuid, version bigint NOT NULL DEFAULT 1 CHECK(version>0),
      PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,number), UNIQUE(tenant_id,reservation_id),
      UNIQUE(tenant_id,client_operation_id),
      FOREIGN KEY(tenant_id,order_id) REFERENCES med.sales_order(tenant_id,id),
      FOREIGN KEY(tenant_id,reservation_id) REFERENCES med.reservation(tenant_id,id),
      FOREIGN KEY(tenant_id,posting_id) REFERENCES med.posting_batch(tenant_id,id)
    );
    CREATE TABLE med.shipment_line (
      tenant_id uuid NOT NULL, id uuid NOT NULL, shipment_id uuid NOT NULL,
      allocation_id uuid NOT NULL, picked_qty numeric(20,6), scanned_lot_no varchar(100),
      PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,allocation_id),
      CHECK(picked_qty IS NULL OR picked_qty>0),
      FOREIGN KEY(tenant_id,shipment_id) REFERENCES med.shipment(tenant_id,id),
      FOREIGN KEY(tenant_id,allocation_id) REFERENCES med.reservation_allocation(tenant_id,id)
    );
    CREATE TABLE med.receivable_obligation (
      tenant_id uuid NOT NULL, id uuid NOT NULL, entity_id uuid NOT NULL,
      customer_id uuid NOT NULL, shipment_id uuid NOT NULL, posting_id uuid NOT NULL,
      gross_amount numeric(20,2) NOT NULL CHECK(gross_amount>=0),
      net_amount numeric(20,2) NOT NULL CHECK(net_amount>=0),
      tax_amount numeric(20,2) NOT NULL CHECK(tax_amount>=0),
      due_date date NOT NULL, currency varchar(3) NOT NULL DEFAULT 'CNY' CHECK(currency='CNY'),
      policy_version varchar(40) NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,posting_id), UNIQUE(tenant_id,shipment_id),
      CHECK(gross_amount=net_amount+tax_amount),
      FOREIGN KEY(tenant_id,customer_id,entity_id) REFERENCES med.party(tenant_id,id,entity_id),
      FOREIGN KEY(tenant_id,shipment_id) REFERENCES med.shipment(tenant_id,id),
      FOREIGN KEY(tenant_id,posting_id) REFERENCES med.posting_batch(tenant_id,id)
    );
    CREATE INDEX sales_by_entity ON med.sales_order(tenant_id,entity_id,created_at,id);
    CREATE INDEX sales_lines ON med.sales_order_line(tenant_id,order_id,id);
    CREATE INDEX shipment_by_order ON med.shipment(tenant_id,order_id,id);
    CREATE INDEX shipment_lines ON med.shipment_line(tenant_id,shipment_id,id);
    CREATE INDEX receivable_customer ON med.receivable_obligation(tenant_id,entity_id,customer_id);
    """)
    for table in (
        "credit_account",
        "sales_order",
        "sales_order_line",
        "shipment",
        "shipment_line",
        "receivable_obligation",
    ):
        op.execute(f"ALTER TABLE med.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE med.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""CREATE POLICY tenant_scope ON med.{table}
          USING(tenant_id=med.current_tenant()) WITH CHECK(tenant_id=med.current_tenant())""")
        op.execute(f"GRANT SELECT, INSERT ON med.{table} TO med_runtime")
        if table in {"sales_order_line", "receivable_obligation"}:
            op.execute(f"""CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE ON med.{table}
              FOR EACH ROW EXECUTE FUNCTION med.reject_fact_mutation()""")
        else:
            op.execute(f"GRANT UPDATE ON med.{table} TO med_runtime")


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
