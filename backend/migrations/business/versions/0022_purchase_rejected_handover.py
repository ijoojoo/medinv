from alembic import op

revision = "b0022"
down_revision = "b0021"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE med.receipt_line
      ADD COLUMN rejected_returned_qty numeric(20,6) NOT NULL DEFAULT 0,
      ADD CONSTRAINT rejected_returned_bounds
        CHECK(rejected_returned_qty>=0 AND rejected_returned_qty<=rejected_qty),
      ADD CONSTRAINT receipt_line_receipt_identity UNIQUE(tenant_id,id,receipt_id);
    CREATE TABLE med.purchase_rejected_handover (
      tenant_id uuid NOT NULL,id uuid NOT NULL,receipt_id uuid NOT NULL,
      handover_ref varchar(120) NOT NULL,recipient varchar(200) NOT NULL,
      reason varchar(200) NOT NULL,created_by uuid NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,id,receipt_id),
      FOREIGN KEY(tenant_id,receipt_id) REFERENCES med.receipt(tenant_id,id)
    );
    CREATE INDEX rejected_handover_receipt
      ON med.purchase_rejected_handover(tenant_id,receipt_id,created_at,id);
    CREATE TABLE med.purchase_rejected_handover_line (
      tenant_id uuid NOT NULL,id uuid NOT NULL,handover_id uuid NOT NULL,
      receipt_id uuid NOT NULL,receipt_line_id uuid NOT NULL,
      qty numeric(20,6) NOT NULL CHECK(qty>0),
      PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,handover_id,receipt_line_id),
      FOREIGN KEY(tenant_id,handover_id,receipt_id)
        REFERENCES med.purchase_rejected_handover(tenant_id,id,receipt_id),
      FOREIGN KEY(tenant_id,receipt_line_id,receipt_id)
        REFERENCES med.receipt_line(tenant_id,id,receipt_id)
    );
    """)
    for table in ("purchase_rejected_handover", "purchase_rejected_handover_line"):
        op.execute(f"ALTER TABLE med.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE med.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""CREATE POLICY tenant_scope ON med.{table}
          USING(tenant_id=med.current_tenant()) WITH CHECK(tenant_id=med.current_tenant())""")
        op.execute(f"GRANT SELECT,INSERT ON med.{table} TO med_runtime")
        op.execute(f"""CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE ON med.{table}
          FOR EACH ROW EXECUTE FUNCTION med.reject_fact_mutation()""")


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
