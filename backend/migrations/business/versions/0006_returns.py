from alembic import op

revision = "b0006"
down_revision = "b0005"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE med.return_document (
      tenant_id uuid NOT NULL,id uuid NOT NULL,entity_id uuid NOT NULL,party_id uuid NOT NULL,
      source_posting_id uuid NOT NULL,receivable_id uuid,payable_id uuid,
      kind varchar(10) NOT NULL CHECK(kind IN ('SALES','PURCHASE')),
      number varchar(60) NOT NULL,reason varchar(200) NOT NULL,
      gross_amount numeric(20,2) NOT NULL CHECK(gross_amount>=0),
      net_amount numeric(20,2) NOT NULL CHECK(net_amount>=0 AND net_amount<=gross_amount),
      state varchar(12) NOT NULL DEFAULT 'DRAFT'
        CHECK(state IN ('DRAFT','APPROVED','POSTED','CANCELLED')),
      created_by uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
      reviewed_by uuid,review_note varchar(200),reviewed_at timestamptz,
      posting_id uuid,version bigint NOT NULL DEFAULT 1 CHECK(version>0),
      CHECK((kind='SALES' AND receivable_id IS NOT NULL AND payable_id IS NULL)
         OR (kind='PURCHASE' AND payable_id IS NOT NULL AND receivable_id IS NULL)),
      CHECK(state NOT IN ('APPROVED','POSTED') OR
        (reviewed_by IS NOT NULL AND reviewed_by<>created_by AND review_note IS NOT NULL)),
      CHECK((state='POSTED')=(posting_id IS NOT NULL)),
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,number),
      FOREIGN KEY(tenant_id,party_id,entity_id) REFERENCES med.party(tenant_id,id,entity_id),
      FOREIGN KEY(tenant_id,source_posting_id) REFERENCES med.posting_batch(tenant_id,id),
      FOREIGN KEY(tenant_id,posting_id) REFERENCES med.posting_batch(tenant_id,id),
      FOREIGN KEY(tenant_id,receivable_id) REFERENCES med.receivable_obligation(tenant_id,id),
      FOREIGN KEY(tenant_id,payable_id) REFERENCES med.payable_obligation(tenant_id,id)
    );
    CREATE UNIQUE INDEX one_active_return ON med.return_document(tenant_id,source_posting_id)
      WHERE state<>'CANCELLED';
    CREATE INDEX return_entity ON med.return_document(tenant_id,entity_id,created_at,id);
    ALTER TABLE med.cash_document ADD COLUMN refunded_amount numeric(20,2) NOT NULL DEFAULT 0;
    ALTER TABLE med.cash_document ADD CONSTRAINT cash_refund_balance
      CHECK(refunded_amount>=0 AND allocated_amount+refunded_amount<=amount
        AND (state<>'VOIDED' OR refunded_amount=0));
    GRANT UPDATE(refunded_amount) ON med.cash_document TO med_runtime;
    CREATE TABLE med.cash_refund (
      tenant_id uuid NOT NULL,id uuid NOT NULL,entity_id uuid NOT NULL,
      cash_document_id uuid NOT NULL,return_document_id uuid NOT NULL,account_id uuid NOT NULL,
      direction varchar(10) NOT NULL CHECK(direction IN ('RECEIPT','PAYMENT')),
      number varchar(60) NOT NULL,reference varchar(120) NOT NULL,
      amount numeric(20,2) NOT NULL CHECK(amount>0),
      occurred_on date NOT NULL,note varchar(500) NOT NULL,
      state varchar(10) NOT NULL DEFAULT 'CONFIRMED' CHECK(state IN ('CONFIRMED','VOIDED')),
      created_by uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,number),
      FOREIGN KEY(tenant_id,cash_document_id) REFERENCES med.cash_document(tenant_id,id),
      FOREIGN KEY(tenant_id,return_document_id) REFERENCES med.return_document(tenant_id,id),
      FOREIGN KEY(tenant_id,account_id,entity_id) REFERENCES
              med.cash_account(tenant_id,id,entity_id)
    );
    CREATE UNIQUE INDEX refund_reference ON med.cash_refund
      (tenant_id,entity_id,account_id,direction,reference) WHERE state='CONFIRMED';
    CREATE INDEX refund_return ON med.cash_refund(tenant_id,return_document_id);
    CREATE INDEX refund_cash ON med.cash_refund(tenant_id,cash_document_id);
    CREATE TABLE med.cash_refund_void (
      tenant_id uuid NOT NULL,id uuid NOT NULL,refund_id uuid NOT NULL,
      reason varchar(200) NOT NULL,created_by uuid NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,refund_id),
      FOREIGN KEY(tenant_id,refund_id) REFERENCES med.cash_refund(tenant_id,id)
    );
    """)
    for table in ("return_document", "cash_refund", "cash_refund_void"):
        op.execute(f"ALTER TABLE med.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE med.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""CREATE POLICY tenant_scope ON med.{table}
          USING(tenant_id=med.current_tenant()) WITH CHECK(tenant_id=med.current_tenant())""")
        op.execute(f"GRANT SELECT,INSERT ON med.{table} TO med_runtime")
        op.execute(f"""CREATE TRIGGER immutable_fact BEFORE DELETE ON med.{table}
          FOR EACH ROW EXECUTE FUNCTION med.reject_fact_mutation()""")
    op.execute("""
    GRANT UPDATE(state,reviewed_by,review_note,reviewed_at,posting_id,version)
      ON med.return_document TO med_runtime;
    GRANT UPDATE(state) ON med.cash_refund TO med_runtime;
    CREATE TRIGGER immutable_update BEFORE UPDATE ON med.cash_refund_void
      FOR EACH ROW EXECUTE FUNCTION med.reject_fact_mutation();
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
    )
    SELECT o.*,coalesce(m.settled_amount,0)::numeric(20,2) AS settled_amount,
      (o.gross_amount-coalesce(m.settled_amount,0)-coalesce(r.gross_amount,0))
        ::numeric(20,2) AS outstanding_amount,
      (coalesce(m.version,1)+CASE WHEN r.id IS NULL THEN 0 ELSE 1 END)::bigint AS version,
      coalesce(r.gross_amount,0)::numeric(20,2) AS returned_amount
    FROM obligations o LEFT JOIN movements m
      ON (m.tenant_id,m.item_id,m.kind)=(o.tenant_id,o.id,o.kind)
    LEFT JOIN med.return_document r ON (r.tenant_id,r.source_posting_id)=(o.tenant_id,o.posting_id)
      AND r.state='POSTED';
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
