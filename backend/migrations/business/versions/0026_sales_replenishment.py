from alembic import op

revision = "b0026"
down_revision = "b0025"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      ALTER TABLE med.purchase_order
        ADD COLUMN source_sales_order_id uuid,
        ADD COLUMN source_sales_version bigint,
        ADD CONSTRAINT purchase_sales_source_fk FOREIGN KEY(tenant_id,source_sales_order_id)
          REFERENCES med.sales_order(tenant_id,id),
        ADD CONSTRAINT purchase_sales_source_pair CHECK(
          (source_sales_order_id IS NULL AND source_sales_version IS NULL) OR
          (source_sales_order_id IS NOT NULL AND source_sales_version IS NOT NULL
            AND source_sales_version>0));
      CREATE INDEX purchase_sales_source ON med.purchase_order(tenant_id,source_sales_order_id)
        WHERE source_sales_order_id IS NOT NULL;
      CREATE FUNCTION med.guard_purchase_sales_source() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP='UPDATE' AND ROW(NEW.source_sales_order_id,NEW.source_sales_version)
          IS DISTINCT FROM ROW(OLD.source_sales_order_id,OLD.source_sales_version)
        THEN RAISE EXCEPTION 'purchase sales source is immutable'; END IF;
        IF TG_OP='INSERT' AND NEW.source_sales_order_id IS NOT NULL AND NOT EXISTS
          (SELECT FROM med.sales_order WHERE tenant_id=NEW.tenant_id
           AND id=NEW.source_sales_order_id AND entity_id=NEW.entity_id
           AND warehouse_id=NEW.warehouse_id AND version=NEW.source_sales_version
           AND state='APPROVED')
        THEN RAISE EXCEPTION 'purchase sales source must match approved sales scope'; END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER guard_purchase_sales_source BEFORE INSERT OR UPDATE ON med.purchase_order
        FOR EACH ROW EXECUTE FUNCTION med.guard_purchase_sales_source();
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
