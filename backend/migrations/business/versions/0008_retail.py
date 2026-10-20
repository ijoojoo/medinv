from alembic import op

revision = "b0008"
down_revision = "b0007"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE med.product ADD COLUMN drug_sale_class varchar(10) NOT NULL DEFAULT 'UNKNOWN'
      CHECK(drug_sale_class IN ('UNKNOWN','OTC','RX'));
    CREATE TABLE med.retail_sale (
      tenant_id uuid NOT NULL,id uuid NOT NULL,entity_id uuid NOT NULL,warehouse_id uuid NOT NULL,
      reservation_id uuid NOT NULL,number varchar(60) NOT NULL,
      state varchar(24) NOT NULL DEFAULT 'RESERVED'
        CHECK(state IN ('RESERVED','PAID_PENDING_POST','COMPLETED','CANCELLED','REFUNDED')),
      gross_amount numeric(20,2) NOT NULL CHECK(gross_amount>0),
      net_amount numeric(20,2) NOT NULL CHECK(net_amount>=0 AND net_amount<=gross_amount),
      posting_id uuid,version bigint NOT NULL DEFAULT 1 CHECK(version>0),
      created_by uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
      CHECK((state='COMPLETED')=(posting_id IS NOT NULL)),
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,number),UNIQUE(tenant_id,reservation_id),
      FOREIGN KEY(tenant_id,warehouse_id,entity_id)
        REFERENCES med.warehouse(tenant_id,id,entity_id),
      FOREIGN KEY(tenant_id,reservation_id) REFERENCES med.reservation(tenant_id,id),
      FOREIGN KEY(tenant_id,posting_id) REFERENCES med.posting_batch(tenant_id,id)
    );
    CREATE TABLE med.retail_sale_line (
      tenant_id uuid NOT NULL,id uuid NOT NULL,sale_id uuid NOT NULL,line_no integer NOT NULL,
      sku_id uuid NOT NULL,qty numeric(20,6) NOT NULL CHECK(qty>0),
      unit_price numeric(20,6) NOT NULL CHECK(unit_price>=0),
      tax_rate numeric(7,6) NOT NULL CHECK(tax_rate BETWEEN 0 AND 1),
      gross_amount numeric(20,2) NOT NULL CHECK(gross_amount>=0),
      net_amount numeric(20,2) NOT NULL CHECK(net_amount>=0 AND net_amount<=gross_amount),
      product_snapshot jsonb NOT NULL,package_snapshot jsonb NOT NULL,
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,sale_id,line_no),
      FOREIGN KEY(tenant_id,sale_id) REFERENCES med.retail_sale(tenant_id,id),
      FOREIGN KEY(tenant_id,sku_id) REFERENCES med.sku(tenant_id,id)
    );
    CREATE TABLE med.retail_payment (
      tenant_id uuid NOT NULL,id uuid NOT NULL,sale_id uuid NOT NULL,entity_id uuid NOT NULL,
      account_id uuid NOT NULL,direction varchar(10) NOT NULL
        CHECK(direction IN ('RECEIPT','REFUND')),
      amount numeric(20,2) NOT NULL CHECK(amount>0),
      tendered numeric(20,2) NOT NULL CHECK(tendered>=amount),
      change_amount numeric(20,2) NOT NULL CHECK(change_amount=tendered-amount),
      method varchar(10) NOT NULL DEFAULT 'CASH' CHECK(method='CASH'),
      note varchar(200) NOT NULL,created_by uuid NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,sale_id,direction),
      FOREIGN KEY(tenant_id,sale_id) REFERENCES med.retail_sale(tenant_id,id),
      FOREIGN KEY(tenant_id,account_id,entity_id)
        REFERENCES med.cash_account(tenant_id,id,entity_id)
    );
    CREATE INDEX retail_entity ON med.retail_sale(tenant_id,entity_id,created_at,id);
    CREATE INDEX retail_lines ON med.retail_sale_line(tenant_id,sale_id);
    """)
    for table in ("retail_sale", "retail_sale_line", "retail_payment"):
        op.execute(f"ALTER TABLE med.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE med.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""CREATE POLICY tenant_scope ON med.{table}
          USING(tenant_id=med.current_tenant()) WITH CHECK(tenant_id=med.current_tenant())""")
        op.execute(f"GRANT SELECT,INSERT ON med.{table} TO med_runtime")
        mutation = "DELETE" if table == "retail_sale" else "UPDATE OR DELETE"
        op.execute(f"""CREATE TRIGGER immutable_fact BEFORE {mutation} ON med.{table}
          FOR EACH ROW EXECUTE FUNCTION med.reject_fact_mutation()""")
    op.execute("GRANT UPDATE(state,posting_id,version) ON med.retail_sale TO med_runtime")


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
