from alembic import op

revision = "b0017"
down_revision = "b0016"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      ALTER TABLE med.packaging ADD COLUMN state varchar(20) NOT NULL DEFAULT 'RETIRED'
        CHECK(state IN ('DRAFT','APPROVED','RETIRED')),
        ADD COLUMN reviewed_by uuid, ADD COLUMN reviewed_at timestamptz;
      ALTER TABLE med.packaging ALTER COLUMN state SET DEFAULT 'DRAFT';
      CREATE UNIQUE INDEX packaging_current_unit ON med.packaging(tenant_id,sku_id,unit_name)
        WHERE state='APPROVED';
      CREATE UNIQUE INDEX packaging_pending_unit ON med.packaging(tenant_id,sku_id,unit_name)
        WHERE state='DRAFT';
      CREATE FUNCTION med.protect_packaging() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'packaging versions cannot be deleted'; END IF;
        IF ROW(NEW.tenant_id,NEW.id,NEW.sku_id,NEW.unit_name,NEW.numerator,NEW.denominator,
               NEW.revision,NEW.effective_from,NEW.created_by,NEW.created_at) IS DISTINCT FROM
           ROW(OLD.tenant_id,OLD.id,OLD.sku_id,OLD.unit_name,OLD.numerator,OLD.denominator,
               OLD.revision,OLD.effective_from,OLD.created_by,OLD.created_at)
           OR OLD.state='RETIRED' THEN
          RAISE EXCEPTION 'packaging definition is immutable';
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER protect_packaging BEFORE UPDATE OR DELETE ON med.packaging
        FOR EACH ROW EXECUTE FUNCTION med.protect_packaging();
      ALTER TABLE med.purchase_order_line ADD COLUMN trade_qty numeric(20,6),
        ADD COLUMN package_snapshot jsonb,
        ADD COLUMN gross_amount numeric(20,2), ADD COLUMN net_amount numeric(20,2),
        ADD CHECK ((trade_qty IS NULL AND package_snapshot IS NULL AND gross_amount IS NULL
                    AND net_amount IS NULL) OR
                   (trade_qty IS NOT NULL AND trade_qty>0 AND package_snapshot IS NOT NULL
                    AND gross_amount IS NOT NULL AND net_amount IS NOT NULL AND gross_amount>=0
                    AND net_amount>=0 AND net_amount<=gross_amount));
      ALTER TABLE med.sales_order_line ADD COLUMN trade_qty numeric(20,6) CHECK(trade_qty>0),
        ADD COLUMN package_snapshot jsonb;
      ALTER TABLE med.retail_sale_line ADD COLUMN trade_qty numeric(20,6) CHECK(trade_qty>0);
      CREATE FUNCTION med.protect_order_package() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF ROW(NEW.trade_qty,NEW.package_snapshot) IS DISTINCT FROM
           ROW(OLD.trade_qty,OLD.package_snapshot) OR
           (OLD.package_snapshot IS NOT NULL AND
            ROW(NEW.qty,NEW.unit_price,NEW.tax_rate,NEW.gross_amount,NEW.net_amount)
              IS DISTINCT FROM
            ROW(OLD.qty,OLD.unit_price,OLD.tax_rate,OLD.gross_amount,OLD.net_amount)) THEN
          RAISE EXCEPTION 'order packaging and pricing snapshot is immutable';
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER protect_order_package BEFORE UPDATE ON med.purchase_order_line
        FOR EACH ROW EXECUTE FUNCTION med.protect_order_package();
      CREATE TRIGGER protect_order_package BEFORE UPDATE ON med.sales_order_line
        FOR EACH ROW EXECUTE FUNCTION med.protect_order_package();
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
