from alembic import op

revision = "b0024"
down_revision = "b0023"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      ALTER TABLE med.sales_order
        ADD COLUMN revision_no integer NOT NULL DEFAULT 1 CHECK(revision_no>0),
        ADD COLUMN revision_editors uuid[] NOT NULL DEFAULT '{}';

      CREATE FUNCTION med.guard_sales_agreement() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF ROW(NEW.tenant_id,NEW.id,NEW.entity_id,NEW.number,NEW.created_by,NEW.created_at)
           IS DISTINCT FROM
           ROW(OLD.tenant_id,OLD.id,OLD.entity_id,OLD.number,OLD.created_by,OLD.created_at)
        THEN RAISE EXCEPTION 'sales identity is immutable'; END IF;
        IF OLD.state IN ('CANCELLED','CLOSED','FULFILLED') OR EXISTS
          (SELECT FROM med.reservation WHERE tenant_id=OLD.tenant_id AND source_id=OLD.id)
          OR EXISTS (SELECT FROM med.shipment WHERE tenant_id=OLD.tenant_id AND order_id=OLD.id)
        THEN
          IF NEW.state='DRAFT' OR ROW(NEW.customer_id,NEW.warehouse_id,NEW.delivery_address,
             NEW.minimum_shelf_days,NEW.credit_days,NEW.gross_amount,NEW.approved_by,
             NEW.approved_at,NEW.customer_snapshot,NEW.revision_no,NEW.revision_editors)
             IS DISTINCT FROM
             ROW(OLD.customer_id,OLD.warehouse_id,OLD.delivery_address,
             OLD.minimum_shelf_days,OLD.credit_days,OLD.gross_amount,OLD.approved_by,
             OLD.approved_at,OLD.customer_snapshot,OLD.revision_no,OLD.revision_editors)
          THEN RAISE EXCEPTION 'started sales agreement is immutable'; END IF;
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER guard_sales_agreement BEFORE UPDATE ON med.sales_order
        FOR EACH ROW EXECUTE FUNCTION med.guard_sales_agreement();

      CREATE FUNCTION med.guard_sales_line_replacement() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE row_tenant uuid; row_order uuid; doc med.sales_order;
      BEGIN
        IF TG_OP='DELETE' THEN row_tenant:=OLD.tenant_id; row_order:=OLD.order_id;
        ELSE row_tenant:=NEW.tenant_id; row_order:=NEW.order_id; END IF;
        SELECT * INTO doc FROM med.sales_order WHERE tenant_id=row_tenant AND id=row_order
          FOR UPDATE;
        IF NOT FOUND OR doc.state<>'DRAFT' OR (TG_OP='DELETE' AND doc.revision_no<=1)
           OR EXISTS (SELECT FROM med.reservation WHERE tenant_id=row_tenant
             AND source_id=row_order)
           OR EXISTS (SELECT FROM med.shipment WHERE tenant_id=row_tenant AND order_id=row_order)
        THEN RAISE EXCEPTION 'only unallocated sales draft lines may be replaced'; END IF;
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
      END $$;
      DROP TRIGGER immutable_fact ON med.sales_order_line;
      CREATE TRIGGER immutable_fact BEFORE UPDATE ON med.sales_order_line
        FOR EACH ROW EXECUTE FUNCTION med.reject_fact_mutation();
      CREATE TRIGGER guard_sales_line_replacement BEFORE INSERT OR DELETE ON med.sales_order_line
        FOR EACH ROW EXECUTE FUNCTION med.guard_sales_line_replacement();
      GRANT DELETE ON med.sales_order_line TO med_runtime;

      CREATE FUNCTION med.guard_sales_demand_replacement() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE demand med.inventory_demand;
      BEGIN
        SELECT * INTO demand FROM med.inventory_demand
          WHERE tenant_id=OLD.tenant_id AND id=OLD.demand_id FOR UPDATE;
        IF NOT FOUND OR demand.source_type<>'SALES_ORDER' OR demand.state<>'CANCELLED'
           OR demand.checks_completed OR OLD.reserved_qty<>0 OR OLD.fulfilled_qty<>0
        THEN RAISE EXCEPTION 'cannot remove active demand lines'; END IF;
        PERFORM 1 FROM med.sales_order WHERE tenant_id=OLD.tenant_id AND id=OLD.demand_id
          AND state='DRAFT' AND revision_no>1 FOR UPDATE;
        IF NOT FOUND OR EXISTS
          (SELECT FROM med.reservation WHERE tenant_id=OLD.tenant_id AND source_id=OLD.demand_id)
          OR EXISTS (SELECT FROM med.shipment WHERE tenant_id=OLD.tenant_id
             AND order_id=OLD.demand_id)
        THEN RAISE EXCEPTION 'only revised unallocated sales demand lines may be replaced'; END IF;
        RETURN OLD;
      END $$;
      CREATE TRIGGER guard_sales_demand_replacement BEFORE DELETE ON med.inventory_demand_line
        FOR EACH ROW EXECUTE FUNCTION med.guard_sales_demand_replacement();
      GRANT DELETE ON med.inventory_demand_line TO med_runtime;
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
