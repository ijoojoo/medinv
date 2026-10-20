from alembic import op

revision = "b0005"
down_revision = "b0004"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE med.cash_account (
      tenant_id uuid NOT NULL, id uuid NOT NULL, entity_id uuid NOT NULL,
      code varchar(60) NOT NULL, name varchar(200) NOT NULL,
      method varchar(10) NOT NULL CHECK(method IN ('BANK','CASH')),
      created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,id,entity_id), UNIQUE(tenant_id,entity_id,code),
      FOREIGN KEY(tenant_id,entity_id) REFERENCES med.legal_entity(tenant_id,id)
    );
    CREATE TABLE med.cash_document (
      tenant_id uuid NOT NULL, id uuid NOT NULL, entity_id uuid NOT NULL,
      party_id uuid NOT NULL, account_id uuid NOT NULL,
      direction varchar(10) NOT NULL CHECK(direction IN ('RECEIPT','PAYMENT')),
      number varchar(60) NOT NULL, reference varchar(120) NOT NULL, note varchar(500) NOT NULL,
      amount numeric(20,2) NOT NULL CHECK(amount>0),
      allocated_amount numeric(20,2) NOT NULL DEFAULT 0 CHECK(allocated_amount>=0),
      currency varchar(3) NOT NULL DEFAULT 'CNY' CHECK(currency='CNY'),
      occurred_on date NOT NULL, confirmation_source varchar(10) NOT NULL DEFAULT 'MANUAL'
        CHECK(confirmation_source='MANUAL'),
      state varchar(10) NOT NULL DEFAULT 'CONFIRMED' CHECK(state IN ('CONFIRMED','VOIDED')),
      created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
      version bigint NOT NULL DEFAULT 1 CHECK(version>0),
      CHECK(allocated_amount<=amount AND (state<>'VOIDED' OR allocated_amount=0)),
      PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,number),
      FOREIGN KEY(tenant_id,party_id,entity_id) REFERENCES med.party(tenant_id,id,entity_id),
      FOREIGN KEY(tenant_id,account_id,entity_id)
        REFERENCES med.cash_account(tenant_id,id,entity_id)
    );
    CREATE UNIQUE INDEX cash_reference ON med.cash_document
      (tenant_id,entity_id,account_id,direction,reference) WHERE state='CONFIRMED';
    CREATE INDEX cash_by_entity ON med.cash_document(tenant_id,entity_id,created_at,id);
    CREATE TABLE med.settlement_allocation (
      tenant_id uuid NOT NULL, id uuid NOT NULL, cash_document_id uuid NOT NULL,
      receivable_id uuid, payable_id uuid, amount numeric(20,2) NOT NULL CHECK(amount>0),
      created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
      CHECK((receivable_id IS NOT NULL)::int + (payable_id IS NOT NULL)::int = 1),
      PRIMARY KEY(tenant_id,id),
      FOREIGN KEY(tenant_id,cash_document_id) REFERENCES med.cash_document(tenant_id,id),
      FOREIGN KEY(tenant_id,receivable_id) REFERENCES med.receivable_obligation(tenant_id,id),
      FOREIGN KEY(tenant_id,payable_id) REFERENCES med.payable_obligation(tenant_id,id)
    );
    CREATE INDEX allocation_cash ON med.settlement_allocation(tenant_id,cash_document_id,id);
    CREATE INDEX allocation_ar ON med.settlement_allocation(tenant_id,receivable_id);
    CREATE INDEX allocation_ap ON med.settlement_allocation(tenant_id,payable_id);
    CREATE TABLE med.settlement_reversal (
      tenant_id uuid NOT NULL, id uuid NOT NULL, allocation_id uuid NOT NULL,
      reason varchar(200) NOT NULL, created_by uuid NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,allocation_id),
      FOREIGN KEY(tenant_id,allocation_id) REFERENCES med.settlement_allocation(tenant_id,id)
    );
    CREATE TABLE med.cash_void (
      tenant_id uuid NOT NULL, id uuid NOT NULL, cash_document_id uuid NOT NULL,
      reason varchar(200) NOT NULL, created_by uuid NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,cash_document_id),
      FOREIGN KEY(tenant_id,cash_document_id) REFERENCES med.cash_document(tenant_id,id)
    );
    CREATE TABLE med.settlement_operation (
      tenant_id uuid NOT NULL, id uuid NOT NULL, created_by uuid NOT NULL,
      kind varchar(40) NOT NULL, request_hash char(64) NOT NULL,
      response jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,id)
    );
    """)
    for table in (
        "cash_account",
        "cash_document",
        "settlement_allocation",
        "settlement_reversal",
        "cash_void",
        "settlement_operation",
    ):
        op.execute(f"ALTER TABLE med.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE med.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""CREATE POLICY tenant_scope ON med.{table}
          USING(tenant_id=med.current_tenant()) WITH CHECK(tenant_id=med.current_tenant())""")
        op.execute(f"GRANT SELECT, INSERT ON med.{table} TO med_runtime")
        op.execute(f"""CREATE TRIGGER immutable_fact BEFORE DELETE ON med.{table}
          FOR EACH ROW EXECUTE FUNCTION med.reject_fact_mutation()""")
        if table != "cash_document":
            op.execute(f"""CREATE TRIGGER immutable_update BEFORE UPDATE ON med.{table}
              FOR EACH ROW EXECUTE FUNCTION med.reject_fact_mutation()""")
    op.execute("GRANT UPDATE(allocated_amount,state,version) ON med.cash_document TO med_runtime")
    # Invoker security applies source-table RLS; no owner-rights view or backfill bypass is used.
    op.execute("""
    CREATE VIEW med.settlement_item WITH (security_invoker=true) AS
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
      (o.gross_amount-coalesce(m.settled_amount,0))::numeric(20,2) AS outstanding_amount,
      coalesce(m.version,1)::bigint AS version
    FROM obligations o LEFT JOIN movements m
      ON (m.tenant_id,m.item_id,m.kind)=(o.tenant_id,o.id,o.kind);
    GRANT SELECT ON med.settlement_item TO med_runtime;
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
