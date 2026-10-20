from alembic import op

revision = "b0010"
down_revision = "b0009"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE med.stock_count (
      tenant_id uuid NOT NULL,id uuid NOT NULL,entity_id uuid NOT NULL,
      warehouse_id uuid NOT NULL,number varchar(60) NOT NULL,
      state varchar(20) NOT NULL DEFAULT 'COUNTING'
        CHECK(state IN ('COUNTING','SUBMITTED','APPROVED','POSTED','CANCELLED')),
      version bigint NOT NULL DEFAULT 1 CHECK(version>0),reason varchar(200) NOT NULL,
      created_by uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
      submitted_by uuid,submitted_at timestamptz,submit_note varchar(200),
      reviewed_by uuid,reviewed_at timestamptz,review_note varchar(200),
      posting_id uuid,cancel_note varchar(200),
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,number),
      FOREIGN KEY(tenant_id,warehouse_id,entity_id)
        REFERENCES med.warehouse(tenant_id,id,entity_id),
      FOREIGN KEY(tenant_id,posting_id) REFERENCES med.posting_batch(tenant_id,id),
      CHECK((state='POSTED')=(posting_id IS NOT NULL)),
      CHECK(state NOT IN ('SUBMITTED','APPROVED','POSTED') OR submitted_by IS NOT NULL),
      CHECK(state NOT IN ('APPROVED','POSTED') OR
        (reviewed_by IS NOT NULL AND reviewed_by<>created_by AND reviewed_by<>submitted_by))
    );
    CREATE UNIQUE INDEX stock_count_active ON med.stock_count(tenant_id,warehouse_id)
      WHERE state IN ('COUNTING','SUBMITTED','APPROVED');
    CREATE TABLE med.stock_count_line (
      tenant_id uuid NOT NULL,id uuid NOT NULL,count_id uuid NOT NULL,
      line_no integer NOT NULL CHECK(line_no>0),balance_id uuid NOT NULL,
      cost_balance_id uuid NOT NULL,stock_version bigint NOT NULL,cost_version bigint NOT NULL,
      book_qty numeric(20,6) NOT NULL CHECK(book_qty>=0),
      reserved_qty numeric(20,6) NOT NULL CHECK(reserved_qty>=0),
      blocked_qty numeric(20,6) NOT NULL CHECK(blocked_qty>=0),
      pool_qty numeric(20,6) NOT NULL CHECK(pool_qty>=0),
      pool_amount numeric(24,8) NOT NULL CHECK(pool_amount>=0),
      product_snapshot jsonb NOT NULL,package_snapshot jsonb NOT NULL,
      created_by uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,count_id,balance_id),
      UNIQUE(tenant_id,count_id,line_no),UNIQUE(tenant_id,id,count_id),
      FOREIGN KEY(tenant_id,count_id) REFERENCES med.stock_count(tenant_id,id),
      FOREIGN KEY(tenant_id,balance_id) REFERENCES med.stock_balance(tenant_id,id),
      FOREIGN KEY(tenant_id,cost_balance_id) REFERENCES med.cost_balance(tenant_id,id)
    );
    CREATE TABLE med.stock_count_measurement (
      tenant_id uuid NOT NULL,line_id uuid NOT NULL,count_id uuid NOT NULL,
      actual_qty numeric(20,6) NOT NULL CHECK(actual_qty>=0),
      qty_delta numeric(20,6) NOT NULL,cost_delta numeric(24,8) NOT NULL,
      gain_unit_cost numeric(24,8),note varchar(200) NOT NULL,
      created_by uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,line_id),
      FOREIGN KEY(tenant_id,line_id,count_id)
        REFERENCES med.stock_count_line(tenant_id,id,count_id),
      CHECK(gain_unit_cost IS NULL OR (gain_unit_cost>0 AND qty_delta>0))
    );
    """)
    for table in ("stock_count", "stock_count_line", "stock_count_measurement"):
        op.execute(f"""ALTER TABLE med.{table} ENABLE ROW LEVEL SECURITY;
          ALTER TABLE med.{table} FORCE ROW LEVEL SECURITY;
          CREATE POLICY tenant_scope ON med.{table}
            USING(tenant_id=med.current_tenant()) WITH CHECK(tenant_id=med.current_tenant());
          GRANT SELECT,INSERT ON med.{table} TO med_runtime;""")
    op.execute("GRANT UPDATE ON med.stock_count TO med_runtime")
    for table in ("stock_count_line", "stock_count_measurement"):
        op.execute(f"""CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE ON med.{table}
          FOR EACH ROW EXECUTE FUNCTION med.reject_fact_mutation();""")


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
