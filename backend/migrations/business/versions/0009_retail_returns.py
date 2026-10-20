from alembic import op

revision = "b0009"
down_revision = "b0008"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE med.retail_sale ADD CONSTRAINT retail_return_source
      UNIQUE(tenant_id,id,entity_id,posting_id);
    ALTER TABLE med.return_document ALTER COLUMN party_id DROP NOT NULL;
    ALTER TABLE med.return_document ADD COLUMN retail_sale_id uuid;
    ALTER TABLE med.return_document DROP CONSTRAINT return_document_kind_check;
    ALTER TABLE med.return_document DROP CONSTRAINT return_document_check1;
    ALTER TABLE med.return_document ADD CONSTRAINT return_document_kind_check
      CHECK(kind IN ('SALES','PURCHASE','RETAIL'));
    ALTER TABLE med.return_document ADD CONSTRAINT return_document_source_check CHECK(
      (kind='SALES' AND party_id IS NOT NULL AND receivable_id IS NOT NULL
        AND payable_id IS NULL AND retail_sale_id IS NULL) OR
      (kind='PURCHASE' AND party_id IS NOT NULL AND payable_id IS NOT NULL
        AND receivable_id IS NULL AND retail_sale_id IS NULL) OR
      (kind='RETAIL' AND party_id IS NULL AND receivable_id IS NULL
        AND payable_id IS NULL AND retail_sale_id IS NOT NULL AND workflow_version=2));
    ALTER TABLE med.return_document ADD CONSTRAINT return_retail_source_fk
      FOREIGN KEY(tenant_id,retail_sale_id,entity_id,source_posting_id)
      REFERENCES med.retail_sale(tenant_id,id,entity_id,posting_id);
    ALTER TABLE med.return_document ADD CONSTRAINT return_retail_identity
      UNIQUE(tenant_id,id,retail_sale_id,entity_id);
    ALTER TABLE med.retail_payment ADD CONSTRAINT retail_payment_identity
      UNIQUE(tenant_id,id,sale_id,entity_id,account_id);
    CREATE TABLE med.retail_return_refund (
      tenant_id uuid NOT NULL,id uuid NOT NULL,entity_id uuid NOT NULL,
      return_id uuid NOT NULL,sale_id uuid NOT NULL,receipt_id uuid NOT NULL,
      account_id uuid NOT NULL,amount numeric(20,2) NOT NULL CHECK(amount>0),
      number varchar(60) NOT NULL,note varchar(200) NOT NULL,created_by uuid NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,return_id),UNIQUE(tenant_id,number),
      FOREIGN KEY(tenant_id,return_id,sale_id,entity_id)
        REFERENCES med.return_document(tenant_id,id,retail_sale_id,entity_id),
      FOREIGN KEY(tenant_id,receipt_id,sale_id,entity_id,account_id)
        REFERENCES med.retail_payment(tenant_id,id,sale_id,entity_id,account_id)
    );
    CREATE INDEX retail_refund_sale ON med.retail_return_refund(tenant_id,sale_id);
    CREATE INDEX retail_return_sale ON med.return_document(tenant_id,retail_sale_id)
      WHERE retail_sale_id IS NOT NULL;
    ALTER TABLE med.retail_return_refund ENABLE ROW LEVEL SECURITY;
    ALTER TABLE med.retail_return_refund FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_scope ON med.retail_return_refund
      USING(tenant_id=med.current_tenant()) WITH CHECK(tenant_id=med.current_tenant());
    GRANT SELECT,INSERT ON med.retail_return_refund TO med_runtime;
    CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE ON med.retail_return_refund
      FOR EACH ROW EXECUTE FUNCTION med.reject_fact_mutation();
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
