from alembic import op

revision = "b0002"
down_revision = "b0001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE med.inventory_demand (
      tenant_id uuid NOT NULL, id uuid NOT NULL,
      entity_id uuid NOT NULL, warehouse_id uuid NOT NULL,
      source_type varchar(40) NOT NULL CHECK (source_type IN ('SALES_ORDER',
                'RETAIL_SALE','PURCHASE_RETURN')),
      state varchar(30) NOT NULL CHECK (state IN ('APPROVED','PAID_PROTECTED',
                'CANCELLED','CLOSED')),
      checks_completed boolean NOT NULL DEFAULT false,
      eligible_until timestamptz NOT NULL,
      version bigint NOT NULL DEFAULT 1 CHECK (version>0),
      PRIMARY KEY (tenant_id,id),
      UNIQUE (tenant_id,id,entity_id,warehouse_id,source_type),
      FOREIGN KEY (tenant_id,entity_id) REFERENCES med.legal_entity(tenant_id,id),
      FOREIGN KEY (tenant_id,warehouse_id,entity_id) REFERENCES med.warehouse(tenant_id,
                id,entity_id)
    );
    CREATE TABLE med.inventory_demand_line (
      tenant_id uuid NOT NULL, id uuid NOT NULL, demand_id uuid NOT NULL, sku_id uuid
                NOT NULL,
      requested_qty numeric(20,6) NOT NULL CHECK (requested_qty>0),
      reserved_qty numeric(20,6) NOT NULL DEFAULT 0 CHECK (reserved_qty>=0),
      fulfilled_qty numeric(20,6) NOT NULL DEFAULT 0 CHECK (fulfilled_qty>=0),
      PRIMARY KEY (tenant_id,id),
      CHECK (requested_qty>=reserved_qty+fulfilled_qty),
      FOREIGN KEY (tenant_id,demand_id) REFERENCES med.inventory_demand(tenant_id,id),
      FOREIGN KEY (tenant_id,sku_id) REFERENCES med.sku(tenant_id,id)
    );
    ALTER TABLE med.reservation ADD COLUMN warehouse_id uuid NOT NULL;
    ALTER TABLE med.reservation ADD CONSTRAINT reservation_demand_fk
      FOREIGN KEY (tenant_id,source_id,entity_id,warehouse_id,source_type)
      REFERENCES med.inventory_demand(tenant_id,id,entity_id,warehouse_id,source_type);
    ALTER TABLE med.reservation_allocation ADD CONSTRAINT allocation_source_line_fk
      FOREIGN KEY (tenant_id,source_line_id) REFERENCES
                med.inventory_demand_line(tenant_id,id);
    CREATE TABLE med.command_result (
      tenant_id uuid NOT NULL, client_operation_id uuid NOT NULL,
      principal_id uuid NOT NULL, entity_id uuid NOT NULL,
      request_hash char(64) NOT NULL, result_snapshot jsonb NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY (tenant_id,client_operation_id),
      FOREIGN KEY (tenant_id,entity_id) REFERENCES med.legal_entity(tenant_id,id)
    );
    CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE ON med.command_result
      FOR EACH ROW EXECUTE FUNCTION med.reject_fact_mutation();
    CREATE INDEX demand_lines ON med.inventory_demand_line(tenant_id,demand_id,id);
    CREATE INDEX reservation_source ON med.reservation(tenant_id,source_id,id);
    """)
    for table in ("inventory_demand", "inventory_demand_line", "command_result"):
        op.execute(f"ALTER TABLE med.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE med.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""CREATE POLICY tenant_scope ON med.{table}
            USING (tenant_id=med.current_tenant()) WITH CHECK (tenant_id=med.current_tenant())""")
        op.execute(f"GRANT SELECT, INSERT ON med.{table} TO med_runtime")
    op.execute("GRANT UPDATE ON med.inventory_demand, med.inventory_demand_line TO med_runtime")


def downgrade():
    raise RuntimeError("Destructive downgrade disabled; restore a verified backup")
