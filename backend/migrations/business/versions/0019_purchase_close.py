from alembic import op

revision = "b0019"
down_revision = "b0018"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      ALTER TABLE med.purchase_order
        DROP CONSTRAINT purchase_order_fulfillment_state_check,
        ADD CHECK (fulfillment_state IN ('OPEN','RECEIVED','CLOSED')),
        ADD COLUMN closed_by uuid,
        ADD COLUMN closed_at timestamptz,
        ADD COLUMN close_reason text,
        ADD CHECK (
          (fulfillment_state='CLOSED' AND approval_state='APPROVED'
           AND closed_by IS NOT NULL AND closed_at IS NOT NULL
           AND close_reason IS NOT NULL AND length(btrim(close_reason))>0)
          OR (fulfillment_state<>'CLOSED' AND closed_by IS NULL
              AND closed_at IS NULL AND close_reason IS NULL));
      ALTER TABLE med.purchase_order_line
        ADD COLUMN closed_qty numeric(20,6) NOT NULL DEFAULT 0,
        ADD CHECK (closed_qty>=0 AND received_qty+closed_qty<=qty);

      CREATE FUNCTION med.protect_purchase_close() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF OLD.fulfillment_state='CLOSED' AND
           ROW(NEW.fulfillment_state,NEW.approval_state,NEW.closed_by,NEW.closed_at,NEW.close_reason)
           IS DISTINCT FROM
           ROW(OLD.fulfillment_state,OLD.approval_state,OLD.closed_by,OLD.closed_at,OLD.close_reason)
        THEN
          RAISE EXCEPTION 'purchase closure is immutable';
        END IF;
        IF NEW.fulfillment_state='CLOSED' AND OLD.fulfillment_state<>'CLOSED' THEN
          IF OLD.approval_state<>'APPROVED' OR OLD.fulfillment_state<>'OPEN'
             OR NOT EXISTS (SELECT FROM med.purchase_order_line
                  WHERE tenant_id=NEW.tenant_id AND order_id=NEW.id AND closed_qty>0)
             OR EXISTS (SELECT FROM med.purchase_order_line
                  WHERE tenant_id=NEW.tenant_id AND order_id=NEW.id
                    AND qty<>received_qty+closed_qty)
          THEN
            RAISE EXCEPTION 'purchase closure must settle all unreceived quantity';
          END IF;
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER protect_purchase_close BEFORE UPDATE ON med.purchase_order
        FOR EACH ROW EXECUTE FUNCTION med.protect_purchase_close();

      CREATE FUNCTION med.protect_closed_purchase_lines() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE t uuid; o uuid;
      BEGIN
        IF TG_OP='INSERT' THEN t=NEW.tenant_id; o=NEW.order_id;
        ELSE t=OLD.tenant_id; o=OLD.order_id; END IF;
        IF EXISTS (SELECT FROM med.purchase_order WHERE tenant_id=t AND id=o
                   AND fulfillment_state='CLOSED') THEN
          RAISE EXCEPTION 'closed purchase lines are immutable';
        END IF;
        IF TG_OP='UPDATE' AND ROW(NEW.tenant_id,NEW.order_id) IS DISTINCT FROM
                             ROW(OLD.tenant_id,OLD.order_id) AND
           EXISTS (SELECT FROM med.purchase_order WHERE tenant_id=NEW.tenant_id
                   AND id=NEW.order_id AND fulfillment_state='CLOSED') THEN
          RAISE EXCEPTION 'cannot move lines into closed purchase';
        END IF;
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER protect_closed_purchase_lines BEFORE INSERT OR UPDATE OR DELETE
        ON med.purchase_order_line FOR EACH ROW
        EXECUTE FUNCTION med.protect_closed_purchase_lines();
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
