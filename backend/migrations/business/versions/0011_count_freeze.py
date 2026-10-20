from alembic import op

revision = "b0011"
down_revision = "b0010"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE med.stock_count_line ADD COLUMN stock_entry_count bigint NOT NULL DEFAULT 0;
    ALTER TABLE med.stock_count_line ADD COLUMN cost_entry_count bigint NOT NULL DEFAULT 0;
    CREATE INDEX count_pool_scope ON med.stock_count_line(tenant_id,cost_balance_id,count_id);
    CREATE FUNCTION med.enforce_count_stock_freeze() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF EXISTS (
        SELECT 1 FROM med.stock_count_line l JOIN med.stock_count d
          ON (d.tenant_id,d.id)=(l.tenant_id,l.count_id)
        JOIN med.cost_balance c ON (c.tenant_id,c.id)=(l.tenant_id,l.cost_balance_id)
        WHERE d.state IN ('COUNTING','SUBMITTED','APPROVED')
          AND c.tenant_id=med.current_tenant()
          AND ((c.entity_id=NEW.entity_id AND c.sku_id=NEW.sku_id
                AND c.lot_id IS NOT DISTINCT FROM NEW.lot_id)
            OR (c.entity_id=OLD.entity_id AND c.sku_id=OLD.sku_id
                AND c.lot_id IS NOT DISTINCT FROM OLD.lot_id))
      ) THEN RAISE EXCEPTION 'stock count freeze' USING ERRCODE='PZ001'; END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
    END $$;
    CREATE FUNCTION med.enforce_count_cost_freeze() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF EXISTS (
        SELECT 1 FROM med.stock_count_line l JOIN med.stock_count d
          ON (d.tenant_id,d.id)=(l.tenant_id,l.count_id)
        WHERE d.state IN ('COUNTING','SUBMITTED','APPROVED')
          AND l.tenant_id=med.current_tenant()
          AND (l.cost_balance_id=NEW.id OR l.cost_balance_id=OLD.id)
      ) THEN RAISE EXCEPTION 'stock count freeze' USING ERRCODE='PZ001'; END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
    END $$;
    CREATE TRIGGER count_freeze BEFORE INSERT OR UPDATE OR DELETE ON med.stock_balance
      FOR EACH ROW EXECUTE FUNCTION med.enforce_count_stock_freeze();
    CREATE TRIGGER count_freeze BEFORE UPDATE OR DELETE ON med.cost_balance
      FOR EACH ROW EXECUTE FUNCTION med.enforce_count_cost_freeze();
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
