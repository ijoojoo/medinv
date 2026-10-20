from alembic import op

revision = "b0021"
down_revision = "b0020"
branch_labels = None
depends_on = None


def upgrade():
    # Replacing unreceived draft lines is needed for amendment. Historical agreement
    # snapshots live in the append-only audit; receipt references must never be removed.
    op.execute("""
      CREATE FUNCTION med.protect_purchase_line_replacement() RETURNS trigger
      LANGUAGE plpgsql AS $$
      BEGIN
        IF OLD.received_qty<>0 OR OLD.closed_qty<>0 OR NOT EXISTS
          (SELECT FROM med.purchase_order WHERE tenant_id=OLD.tenant_id AND id=OLD.order_id
           AND approval_state='DRAFT' AND fulfillment_state='OPEN' AND revision_no>1)
        THEN RAISE EXCEPTION 'only revised unreceived draft lines may be replaced'; END IF;
        RETURN OLD;
      END $$;
      CREATE TRIGGER protect_purchase_line_replacement BEFORE DELETE ON med.purchase_order_line
        FOR EACH ROW EXECUTE FUNCTION med.protect_purchase_line_replacement();
      GRANT DELETE ON med.purchase_order_line TO med_runtime;
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
