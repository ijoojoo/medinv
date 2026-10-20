from alembic import op

revision = "b0003"
down_revision = "b0002"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
               ALTER TABLE med.product ADD COLUMN specification varchar(200) NOT NULL
               DEFAULT '',
               ADD COLUMN approval_number varchar(120) NOT NULL DEFAULT '',
               ADD COLUMN valid_from date, ADD COLUMN valid_until date,
               ADD COLUMN reviewed_by uuid, ADD COLUMN reviewed_at timestamptz;
               CREATE TABLE med.party (
               tenant_id uuid NOT NULL, id uuid NOT NULL, entity_id uuid NOT NULL,
               code varchar(60) NOT NULL, name varchar(200) NOT NULL, credit_code
               varchar(32) NOT NULL,
               kind varchar(20) NOT NULL CHECK (kind IN ('SUPPLIER','CUSTOMER','BOTH')),
               state varchar(20) NOT NULL DEFAULT 'DRAFT' CHECK (state IN ('DRAFT',
               'APPROVED','SUSPENDED')),
               license_no varchar(120) NOT NULL, valid_from date NOT NULL, valid_until date
               NOT NULL,
               categories text[] NOT NULL CHECK (cardinality(categories)>0 AND categories
               <@ ARRAY['DRUG','DEVICE']),
               created_by uuid NOT NULL, updated_by uuid NOT NULL,
               created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT
               NULL DEFAULT now(),
               version bigint NOT NULL DEFAULT 1 CHECK (version>0), reviewed_by uuid,
               reviewed_at timestamptz,
               PRIMARY KEY (tenant_id,id), UNIQUE (tenant_id,entity_id,code),
               UNIQUE (tenant_id,id,entity_id), CHECK (valid_until>=valid_from),
               FOREIGN KEY (tenant_id,entity_id) REFERENCES med.legal_entity(tenant_id,id)
               );
               CREATE TABLE med.accounting_period (
               tenant_id uuid NOT NULL, entity_id uuid NOT NULL, period varchar(7) NOT NULL,
               
               state varchar(10) NOT NULL DEFAULT 'OPEN' CHECK (state IN ('OPEN','CLOSED')),
               
               PRIMARY KEY (tenant_id,entity_id,period),
               FOREIGN KEY (tenant_id,entity_id) REFERENCES med.legal_entity(tenant_id,id)
               );
               CREATE TABLE med.purchase_order (
               tenant_id uuid NOT NULL, id uuid NOT NULL, entity_id uuid NOT NULL,
               supplier_id uuid NOT NULL,
               warehouse_id uuid NOT NULL, number varchar(60) NOT NULL,
               approval_state varchar(20) NOT NULL DEFAULT 'DRAFT'
               CHECK (approval_state IN ('DRAFT','APPROVED','CANCELLED')),
               fulfillment_state varchar(20) NOT NULL DEFAULT 'OPEN'
               CHECK (fulfillment_state IN ('OPEN','RECEIVED')),
               created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
               approved_by uuid, approved_at timestamptz, version bigint NOT NULL DEFAULT 1
               CHECK (version>0),
               PRIMARY KEY (tenant_id,id), UNIQUE (tenant_id,number),
               FOREIGN KEY (tenant_id,supplier_id,entity_id) REFERENCES med.party(tenant_id,
               id,entity_id),
               FOREIGN KEY (tenant_id,warehouse_id,entity_id) REFERENCES
               med.warehouse(tenant_id,id,entity_id)
               );
               CREATE TABLE med.purchase_order_line (
               tenant_id uuid NOT NULL, id uuid NOT NULL, order_id uuid NOT NULL, line_no
               integer NOT NULL,
               sku_id uuid NOT NULL, qty numeric(20,6) NOT NULL CHECK(qty>0),
               received_qty numeric(20,6) NOT NULL DEFAULT 0 CHECK(received_qty>=0 AND
               received_qty<=qty),
               unit_price numeric(20,6) NOT NULL CHECK(unit_price>=0),
               tax_rate numeric(7,6) NOT NULL CHECK(tax_rate>=0 AND tax_rate<=1),
               PRIMARY KEY (tenant_id,id), UNIQUE(tenant_id,order_id,line_no),
               FOREIGN KEY (tenant_id,order_id) REFERENCES med.purchase_order(tenant_id,id),
               
               FOREIGN KEY (tenant_id,sku_id) REFERENCES med.sku(tenant_id,id)
               );
               CREATE TABLE med.receipt (
               tenant_id uuid NOT NULL, id uuid NOT NULL, order_id uuid NOT NULL, number
               varchar(60) NOT NULL,
               shipment_ref varchar(120) NOT NULL, transport_note varchar(500) NOT NULL,
               created_by uuid NOT NULL, received_at timestamptz NOT NULL DEFAULT now(),
               version bigint NOT NULL DEFAULT 1 CHECK(version>0),
               PRIMARY KEY (tenant_id,id), UNIQUE(tenant_id,number),
               FOREIGN KEY (tenant_id,order_id) REFERENCES med.purchase_order(tenant_id,id)
               );
               CREATE TABLE med.receipt_line (
               tenant_id uuid NOT NULL, id uuid NOT NULL, receipt_id uuid NOT NULL,
               order_line_id uuid NOT NULL, sku_id uuid NOT NULL, lot_id uuid NOT NULL,
               location_id uuid NOT NULL,
               qty numeric(20,6) NOT NULL CHECK(qty>0),
               qualified_qty numeric(20,6) NOT NULL DEFAULT 0 CHECK(qualified_qty>=0),
               rejected_qty numeric(20,6) NOT NULL DEFAULT 0 CHECK(rejected_qty>=0),
               posted_qty numeric(20,6) NOT NULL DEFAULT 0 CHECK(posted_qty>=0 AND
               posted_qty<=qualified_qty),
               CHECK(qualified_qty+rejected_qty<=qty), PRIMARY KEY(tenant_id,id),
               FOREIGN KEY (tenant_id,receipt_id) REFERENCES med.receipt(tenant_id,id),
               FOREIGN KEY (tenant_id,order_line_id) REFERENCES
               med.purchase_order_line(tenant_id,id),
               FOREIGN KEY (tenant_id,lot_id,sku_id) REFERENCES med.lot(tenant_id,id,
               sku_id),
               FOREIGN KEY (tenant_id,location_id) REFERENCES med.location(tenant_id,id)
               );
               CREATE TABLE med.inspection (
               tenant_id uuid NOT NULL, id uuid NOT NULL, receipt_id uuid NOT NULL,
               created_by uuid NOT NULL, inspected_at timestamptz NOT NULL DEFAULT now(),
               note varchar(500) NOT NULL,
               PRIMARY KEY (tenant_id,id),
               FOREIGN KEY (tenant_id,receipt_id) REFERENCES med.receipt(tenant_id,id)
               );
               CREATE TABLE med.inspection_line (
               tenant_id uuid NOT NULL, id uuid NOT NULL, inspection_id uuid NOT NULL,
               receipt_line_id uuid NOT NULL,
               qualified_qty numeric(20,6) NOT NULL CHECK(qualified_qty>=0),
               rejected_qty numeric(20,6) NOT NULL CHECK(rejected_qty>=0),
               CHECK(qualified_qty+rejected_qty>0), PRIMARY KEY (tenant_id,id),
               UNIQUE(tenant_id,inspection_id,receipt_line_id),
               FOREIGN KEY (tenant_id,inspection_id) REFERENCES med.inspection(tenant_id,
               id),
               FOREIGN KEY (tenant_id,receipt_line_id) REFERENCES
               med.receipt_line(tenant_id,id)
               );
               CREATE TABLE med.receipt_posting (
               tenant_id uuid NOT NULL, id uuid NOT NULL, inspection_id uuid NOT NULL,
               state varchar(20) NOT NULL DEFAULT 'APPROVED' CHECK(state IN ('APPROVED',
               'POSTED')),
               version bigint NOT NULL DEFAULT 1 CHECK(version>0), posting_id uuid,
               PRIMARY KEY (tenant_id,id), UNIQUE(tenant_id,inspection_id),
               FOREIGN KEY (tenant_id,inspection_id) REFERENCES med.inspection(tenant_id,
               id),
               FOREIGN KEY (tenant_id,posting_id) REFERENCES med.posting_batch(tenant_id,
               id)
               );
               CREATE TABLE med.payable_obligation (
               tenant_id uuid NOT NULL, id uuid NOT NULL, entity_id uuid NOT NULL,
               supplier_id uuid NOT NULL,
               posting_id uuid NOT NULL, gross_amount numeric(20,2) NOT NULL
               CHECK(gross_amount>=0),
               net_amount numeric(20,2) NOT NULL CHECK(net_amount>=0), tax_amount
               numeric(20,2) NOT NULL,
               currency varchar(3) NOT NULL DEFAULT 'CNY' CHECK(currency='CNY'),
               policy_version varchar(40) NOT NULL, created_at timestamptz NOT NULL DEFAULT
               now(),
               CHECK(tax_amount>=0 AND net_amount+tax_amount=gross_amount),
               PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,posting_id),
               FOREIGN KEY (tenant_id,supplier_id,entity_id) REFERENCES med.party(tenant_id,
               id,entity_id),
               FOREIGN KEY (tenant_id,posting_id) REFERENCES med.posting_batch(tenant_id,
               id)
               );
               CREATE INDEX receipt_by_order ON med.receipt(tenant_id,order_id);
               CREATE INDEX order_lines ON med.purchase_order_line(tenant_id,order_id,id);
               CREATE INDEX receipt_lines ON med.receipt_line(tenant_id,receipt_id,id);
               CREATE INDEX inspection_lines ON med.inspection_line(tenant_id,inspection_id,
               id);
               """)
    tables = (
        "party",
        "accounting_period",
        "purchase_order",
        "purchase_order_line",
        "receipt",
        "receipt_line",
        "inspection",
        "inspection_line",
        "receipt_posting",
        "payable_obligation",
    )
    immutable = {"inspection", "inspection_line", "payable_obligation"}
    for table in tables:
        op.execute(f"ALTER TABLE med.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE med.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""CREATE POLICY tenant_scope ON med.{table}
                    USING (tenant_id=med.current_tenant())
                    WITH CHECK (tenant_id=med.current_tenant())""")
        op.execute(f"GRANT SELECT, INSERT ON med.{table} TO med_runtime")
        if table in immutable:
            op.execute(f"""CREATE TRIGGER immutable_fact
                        BEFORE UPDATE OR DELETE ON med.{table}
                        FOR EACH ROW EXECUTE FUNCTION med.reject_fact_mutation()""")
        elif table != "accounting_period":
            op.execute(f"GRANT UPDATE ON med.{table} TO med_runtime")
    # Row-lock privilege only; period state changes belong to a later close-period service.
    op.execute("GRANT UPDATE(period) ON med.accounting_period TO med_runtime")


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
