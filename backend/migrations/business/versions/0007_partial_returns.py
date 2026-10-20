from alembic import op

revision = "b0007"
down_revision = "b0006"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE med.posting_line ADD COLUMN gross_amount numeric(20,2),
      ADD COLUMN net_amount numeric(20,2),
      ADD CONSTRAINT posting_line_money CHECK(
        (gross_amount IS NULL AND net_amount IS NULL) OR
        (gross_amount IS NOT NULL AND net_amount IS NOT NULL AND
         gross_amount>=0 AND net_amount>=0 AND net_amount<=gross_amount));
    ALTER TABLE med.return_document ADD COLUMN workflow_version smallint NOT NULL DEFAULT 1
      CHECK(workflow_version IN (1,2));
    ALTER TABLE med.return_document DROP CONSTRAINT return_document_state_check;
    ALTER TABLE med.return_document ADD CONSTRAINT return_document_state_check
      CHECK(state IN ('DRAFT','RECEIVED','REJECTED','APPROVED','POSTED','CANCELLED'));
    DROP INDEX med.one_active_return;
    CREATE UNIQUE INDEX one_active_return ON med.return_document(tenant_id,source_posting_id)
      WHERE state NOT IN ('POSTED','CANCELLED');
    CREATE TABLE med.return_line (
      tenant_id uuid NOT NULL,id uuid NOT NULL,return_id uuid NOT NULL,
      source_posting_line_id uuid NOT NULL,qty numeric(20,6) NOT NULL CHECK(qty>0),
      gross_amount numeric(20,2),net_amount numeric(20,2),
      original_cost numeric(24,8) NOT NULL CHECK(original_cost>=0),
      created_by uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
      CHECK((gross_amount IS NULL AND net_amount IS NULL) OR
        (gross_amount IS NOT NULL AND net_amount IS NOT NULL AND gross_amount>=0
         AND net_amount>=0 AND net_amount<=gross_amount)),
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,return_id,source_posting_line_id),
      FOREIGN KEY(tenant_id,return_id) REFERENCES med.return_document(tenant_id,id),
      FOREIGN KEY(tenant_id,source_posting_line_id) REFERENCES med.posting_line(tenant_id,id)
    );
    CREATE INDEX return_line_source ON med.return_line(tenant_id,source_posting_line_id);
    CREATE TABLE med.return_custody (
      tenant_id uuid NOT NULL,id uuid NOT NULL,return_id uuid NOT NULL,
      zone_label varchar(200) NOT NULL,note varchar(200) NOT NULL,
      created_by uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,return_id),
      FOREIGN KEY(tenant_id,return_id) REFERENCES med.return_document(tenant_id,id)
    );
    CREATE TABLE med.return_custody_close (
      tenant_id uuid NOT NULL,id uuid NOT NULL,return_id uuid NOT NULL,
      outcome varchar(20) NOT NULL CHECK(outcome IN ('ACCEPTED','HANDED_BACK')),
      note varchar(200) NOT NULL,created_by uuid NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,return_id),
      FOREIGN KEY(tenant_id,return_id) REFERENCES med.return_custody(tenant_id,return_id)
    );
    """)
    for table in ("return_line", "return_custody", "return_custody_close"):
        op.execute(f"ALTER TABLE med.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE med.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""CREATE POLICY tenant_scope ON med.{table}
          USING(tenant_id=med.current_tenant()) WITH CHECK(tenant_id=med.current_tenant())""")
        op.execute(f"GRANT SELECT,INSERT ON med.{table} TO med_runtime")
        op.execute(f"""CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE ON med.{table}
          FOR EACH ROW EXECUTE FUNCTION med.reject_fact_mutation()""")
    op.execute("""
    CREATE OR REPLACE VIEW med.settlement_item WITH (security_invoker=true) AS
    WITH movements AS (
      SELECT a.tenant_id,coalesce(a.receivable_id,a.payable_id) AS item_id,
        CASE WHEN a.receivable_id IS NOT NULL THEN 'AR' ELSE 'AP' END AS kind,
        sum(CASE WHEN r.id IS NULL THEN a.amount ELSE 0 END)::numeric(20,2) AS settled_amount,
        (count(a.id)+count(r.id)+1)::bigint AS version
      FROM med.settlement_allocation a LEFT JOIN med.settlement_reversal r
        ON (r.tenant_id,r.allocation_id)=(a.tenant_id,a.id)
      GROUP BY a.tenant_id,coalesce(a.receivable_id,a.payable_id),
        CASE WHEN a.receivable_id IS NOT NULL THEN 'AR' ELSE 'AP' END
    ), obligations AS (
      SELECT tenant_id,id,entity_id,customer_id AS party_id,'AR'::text AS kind,
        gross_amount,currency,due_date,posting_id,created_at FROM med.receivable_obligation
      UNION ALL
      SELECT tenant_id,id,entity_id,supplier_id,'AP'::text,gross_amount,currency,
        NULL::date,posting_id,created_at FROM med.payable_obligation
    ), returns AS (
      SELECT tenant_id,source_posting_id,sum(gross_amount) AS gross_amount,count(*) AS count
      FROM med.return_document WHERE state='POSTED' GROUP BY tenant_id,source_posting_id
    )
    SELECT o.*,coalesce(m.settled_amount,0)::numeric(20,2) AS settled_amount,
      (o.gross_amount-coalesce(m.settled_amount,0)-coalesce(r.gross_amount,0))
        ::numeric(20,2) AS outstanding_amount,
      (coalesce(m.version,1)+coalesce(r.count,0))::bigint AS version,
      coalesce(r.gross_amount,0)::numeric(20,2) AS returned_amount
    FROM obligations o LEFT JOIN movements m
      ON (m.tenant_id,m.item_id,m.kind)=(o.tenant_id,o.id,o.kind)
    LEFT JOIN returns r ON (r.tenant_id,r.source_posting_id)=(o.tenant_id,o.posting_id);
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
