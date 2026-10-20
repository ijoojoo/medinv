from alembic import op

revision = "b0020"
down_revision = "b0019"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      ALTER TABLE med.purchase_order
        ADD COLUMN revision_no integer NOT NULL DEFAULT 1 CHECK (revision_no>0),
        ADD COLUMN revision_editors uuid[] NOT NULL DEFAULT '{}';

      CREATE FUNCTION med.protect_received_purchase() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF EXISTS (SELECT FROM med.receipt WHERE tenant_id=OLD.tenant_id AND order_id=OLD.id)
           AND ROW(NEW.entity_id,NEW.supplier_id,NEW.warehouse_id,NEW.approval_state,
                   NEW.approved_by,NEW.approved_at,NEW.revision_no,NEW.revision_editors)
           IS DISTINCT FROM
               ROW(OLD.entity_id,OLD.supplier_id,OLD.warehouse_id,OLD.approval_state,
                   OLD.approved_by,OLD.approved_at,OLD.revision_no,OLD.revision_editors)
        THEN RAISE EXCEPTION 'received purchase agreement is immutable'; END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER protect_received_purchase BEFORE UPDATE ON med.purchase_order
        FOR EACH ROW EXECUTE FUNCTION med.protect_received_purchase();

      CREATE FUNCTION med.protect_received_purchase_lines() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP<>'INSERT' AND EXISTS
           (SELECT FROM med.receipt WHERE tenant_id=OLD.tenant_id AND order_id=OLD.order_id)
        THEN
          IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'received purchase agreement lines are immutable';
          END IF;
          IF ROW(NEW.id,NEW.tenant_id,NEW.order_id,NEW.line_no,NEW.sku_id,NEW.qty,
                 NEW.unit_price,NEW.tax_rate,NEW.trade_qty,NEW.package_snapshot,
                 NEW.gross_amount,NEW.net_amount) IS DISTINCT FROM
             ROW(OLD.id,OLD.tenant_id,OLD.order_id,OLD.line_no,OLD.sku_id,OLD.qty,
                 OLD.unit_price,OLD.tax_rate,OLD.trade_qty,OLD.package_snapshot,
                 OLD.gross_amount,OLD.net_amount)
          THEN RAISE EXCEPTION 'received purchase agreement lines are immutable'; END IF;
        END IF;
        IF TG_OP='INSERT' OR (TG_OP='UPDATE' AND
           ROW(NEW.tenant_id,NEW.order_id) IS DISTINCT FROM ROW(OLD.tenant_id,OLD.order_id)) THEN
          IF EXISTS (SELECT FROM med.receipt
                     WHERE tenant_id=NEW.tenant_id AND order_id=NEW.order_id)
          THEN RAISE EXCEPTION 'cannot add lines to received purchase'; END IF;
        END IF;
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER protect_received_purchase_lines BEFORE INSERT OR UPDATE OR DELETE
        ON med.purchase_order_line FOR EACH ROW
        EXECUTE FUNCTION med.protect_received_purchase_lines();
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
