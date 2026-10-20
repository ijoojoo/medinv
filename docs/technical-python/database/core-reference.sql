-- PostgreSQL 18 core design reference v1.0; Python implementation edition
-- Alembic must implement full reviewed migrations; this file is language-neutral.
-- EMPTY REVIEW DATABASE ONLY. Not a complete production migration.
-- Runtime roles and application rules must be configured separately.
BEGIN;
CREATE SCHEMA med;
CREATE FUNCTION med.current_tenant() RETURNS uuid LANGUAGE sql STABLE
AS $$ SELECT nullif(current_setting('app.tenant_id', true), '')::uuid $$;
CREATE FUNCTION med.reject_fact_mutation() RETURNS trigger LANGUAGE plpgsql
AS $$ BEGIN RAISE EXCEPTION 'immutable fact: use compensating entries'; END $$;

CREATE TABLE med.legal_entity (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  updated_by uuid NOT NULL,
  version bigint NOT NULL DEFAULT 1 CHECK (version>0),
  name text NOT NULL,
  credit_code varchar(32) NOT NULL,
  status varchar(20) NOT NULL CHECK (status IN ('ACTIVE','SUSPENDED')),
  PRIMARY KEY (tenant_id,id),
  UNIQUE (tenant_id, credit_code)
);

COMMENT ON TABLE med.legal_entity IS '租户内法律主体';

ALTER TABLE med.legal_entity ENABLE ROW LEVEL SECURITY;
ALTER TABLE med.legal_entity FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON med.legal_entity
  USING (tenant_id = med.current_tenant())
  WITH CHECK (tenant_id = med.current_tenant());

CREATE TABLE med.warehouse (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  updated_by uuid NOT NULL,
  version bigint NOT NULL DEFAULT 1 CHECK (version>0),
  entity_id uuid NOT NULL,
  code varchar(50) NOT NULL,
  kind varchar(20) NOT NULL CHECK (kind IN ('PHYSICAL','TRANSIT')),
  name text NOT NULL,
  PRIMARY KEY (tenant_id,id),
  UNIQUE (tenant_id, entity_id, code),
  UNIQUE (tenant_id, id, entity_id),
  FOREIGN KEY (tenant_id, entity_id) REFERENCES med.legal_entity(tenant_id,id)
);

COMMENT ON TABLE med.warehouse IS '主体所属仓库 包括虚拟在途库';

ALTER TABLE med.warehouse ENABLE ROW LEVEL SECURITY;
ALTER TABLE med.warehouse FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON med.warehouse
  USING (tenant_id = med.current_tenant())
  WITH CHECK (tenant_id = med.current_tenant());

CREATE TABLE med.location (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  updated_by uuid NOT NULL,
  version bigint NOT NULL DEFAULT 1 CHECK (version>0),
  warehouse_id uuid NOT NULL,
  code varchar(50) NOT NULL,
  PRIMARY KEY (tenant_id,id),
  UNIQUE (tenant_id, warehouse_id, code),
  UNIQUE (tenant_id, id, warehouse_id),
  FOREIGN KEY (tenant_id, warehouse_id) REFERENCES med.warehouse(tenant_id,id)
);

COMMENT ON TABLE med.location IS '仓库库位';

ALTER TABLE med.location ENABLE ROW LEVEL SECURITY;
ALTER TABLE med.location FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON med.location
  USING (tenant_id = med.current_tenant())
  WITH CHECK (tenant_id = med.current_tenant());

CREATE TABLE med.product (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  updated_by uuid NOT NULL,
  version bigint NOT NULL DEFAULT 1 CHECK (version>0),
  code varchar(60) NOT NULL,
  name text NOT NULL,
  category varchar(20) NOT NULL CHECK (category IN ('DRUG','DEVICE')),
  approval_state varchar(20) NOT NULL,
  restricted_category text,
  manufacturer_name text NOT NULL,
  PRIMARY KEY (tenant_id,id),
  UNIQUE (tenant_id, code)
);

COMMENT ON TABLE med.product IS '药械经营商品的已审核身份';

ALTER TABLE med.product ENABLE ROW LEVEL SECURITY;
ALTER TABLE med.product FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON med.product
  USING (tenant_id = med.current_tenant())
  WITH CHECK (tenant_id = med.current_tenant());

CREATE TABLE med.sku (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  updated_by uuid NOT NULL,
  version bigint NOT NULL DEFAULT 1 CHECK (version>0),
  product_id uuid NOT NULL,
  code varchar(60) NOT NULL,
  base_unit varchar(30) NOT NULL,
  qty_scale smallint NOT NULL CHECK (qty_scale BETWEEN 0 AND 6),
  lot_required boolean NOT NULL DEFAULT true,
  serial_required boolean NOT NULL DEFAULT false,
  PRIMARY KEY (tenant_id,id),
  UNIQUE (tenant_id, code),
  FOREIGN KEY (tenant_id, product_id) REFERENCES med.product(tenant_id,id)
);

COMMENT ON TABLE med.sku IS '库存单位和精度策略';

ALTER TABLE med.sku ENABLE ROW LEVEL SECURITY;
ALTER TABLE med.sku FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON med.sku
  USING (tenant_id = med.current_tenant())
  WITH CHECK (tenant_id = med.current_tenant());

CREATE TABLE med.packaging (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  updated_by uuid NOT NULL,
  version bigint NOT NULL DEFAULT 1 CHECK (version>0),
  sku_id uuid NOT NULL,
  unit_name varchar(30) NOT NULL,
  numerator bigint NOT NULL CHECK (numerator>0),
  denominator bigint NOT NULL CHECK (denominator>0),
  revision integer NOT NULL CHECK (revision>0),
  effective_from timestamptz NOT NULL,
  effective_until timestamptz,
  PRIMARY KEY (tenant_id,id),
  UNIQUE (tenant_id, sku_id, unit_name, revision),
  CHECK (effective_until IS NULL OR effective_until>effective_from),
  FOREIGN KEY (tenant_id, sku_id) REFERENCES med.sku(tenant_id,id)
);

COMMENT ON TABLE med.packaging IS '包装换算版本';

ALTER TABLE med.packaging ENABLE ROW LEVEL SECURITY;
ALTER TABLE med.packaging FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON med.packaging
  USING (tenant_id = med.current_tenant())
  WITH CHECK (tenant_id = med.current_tenant());

CREATE TABLE med.lot (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  updated_by uuid NOT NULL,
  version bigint NOT NULL DEFAULT 1 CHECK (version>0),
  sku_id uuid NOT NULL,
  manufacturer_key varchar(100) NOT NULL,
  lot_no varchar(100) NOT NULL,
  mfg_date date,
  expiry_kind varchar(20) NOT NULL CHECK (expiry_kind IN ('DATE','MONTH','NOT_APPLICABLE','UNRESOLVED')),
  expiry_exclusive timestamptz,
  raw_expiry text,
  quality_blocked boolean NOT NULL DEFAULT false,
  PRIMARY KEY (tenant_id,id),
  UNIQUE (tenant_id, sku_id, manufacturer_key, lot_no),
  UNIQUE (tenant_id, id, sku_id),
  CHECK ((expiry_kind IN ('DATE','MONTH') AND expiry_exclusive IS NOT NULL) OR (expiry_kind IN ('NOT_APPLICABLE','UNRESOLVED') AND expiry_exclusive IS NULL)),
  FOREIGN KEY (tenant_id, sku_id) REFERENCES med.sku(tenant_id,id)
);

COMMENT ON TABLE med.lot IS '批次身份与规范失效边界';

ALTER TABLE med.lot ENABLE ROW LEVEL SECURITY;
ALTER TABLE med.lot FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON med.lot
  USING (tenant_id = med.current_tenant())
  WITH CHECK (tenant_id = med.current_tenant());

CREATE TABLE med.stock_gate (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  updated_by uuid NOT NULL,
  version bigint NOT NULL DEFAULT 1 CHECK (version>0),
  entity_id uuid NOT NULL,
  sku_id uuid NOT NULL,
  blocked boolean NOT NULL DEFAULT false,
  reason text,
  PRIMARY KEY (tenant_id,id),
  UNIQUE (tenant_id, entity_id, sku_id),
  FOREIGN KEY (tenant_id, entity_id) REFERENCES med.legal_entity(tenant_id,id),
  FOREIGN KEY (tenant_id, sku_id) REFERENCES med.sku(tenant_id,id)
);

COMMENT ON TABLE med.stock_gate IS 'SKU稳定闸门用于召回和普通交易协调';

ALTER TABLE med.stock_gate ENABLE ROW LEVEL SECURITY;
ALTER TABLE med.stock_gate FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON med.stock_gate
  USING (tenant_id = med.current_tenant())
  WITH CHECK (tenant_id = med.current_tenant());

CREATE TABLE med.stock_balance (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  updated_by uuid NOT NULL,
  version bigint NOT NULL DEFAULT 1 CHECK (version>0),
  entity_id uuid NOT NULL,
  owner_id uuid NOT NULL,
  warehouse_id uuid NOT NULL,
  location_id uuid NOT NULL,
  sku_id uuid NOT NULL,
  lot_id uuid,
  quality_status varchar(20) NOT NULL CHECK (quality_status IN ('QUALIFIED','QUARANTINE','UNQUALIFIED','RECALL')),
  on_hand numeric(20,6) NOT NULL DEFAULT 0,
  reserved numeric(20,6) NOT NULL DEFAULT 0,
  blocked numeric(20,6) NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant_id,id),
  UNIQUE NULLS NOT DISTINCT (tenant_id,entity_id,owner_id,warehouse_id,location_id,sku_id,lot_id,quality_status),
  CHECK (owner_id=entity_id),
  CHECK (on_hand>=0 AND reserved>=0 AND blocked>=0 AND on_hand>=reserved+blocked),
  FOREIGN KEY (tenant_id,warehouse_id,entity_id) REFERENCES med.warehouse(tenant_id,id,entity_id),
  FOREIGN KEY (tenant_id,location_id,warehouse_id) REFERENCES med.location(tenant_id,id,warehouse_id),
  FOREIGN KEY (tenant_id,sku_id) REFERENCES med.sku(tenant_id,id),
  FOREIGN KEY (tenant_id,lot_id,sku_id) REFERENCES med.lot(tenant_id,id,sku_id)
);

COMMENT ON TABLE med.stock_balance IS '数量投影 必须由过账服务维护';

ALTER TABLE med.stock_balance ENABLE ROW LEVEL SECURITY;
ALTER TABLE med.stock_balance FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON med.stock_balance
  USING (tenant_id = med.current_tenant())
  WITH CHECK (tenant_id = med.current_tenant());

CREATE TABLE med.reservation (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  updated_by uuid NOT NULL,
  version bigint NOT NULL DEFAULT 1 CHECK (version>0),
  entity_id uuid NOT NULL,
  source_type varchar(40) NOT NULL,
  source_id uuid NOT NULL,
  client_operation_id uuid NOT NULL,
  state varchar(20) NOT NULL CHECK (state IN ('ACTIVE','CONSUMED','RELEASED','INVALIDATED')),
  expires_at timestamptz NOT NULL,
  protected_until timestamptz,
  PRIMARY KEY (tenant_id,id),
  UNIQUE (tenant_id,client_operation_id),
  FOREIGN KEY (tenant_id,entity_id) REFERENCES med.legal_entity(tenant_id,id)
);

COMMENT ON TABLE med.reservation IS '一次业务预占聚合';

ALTER TABLE med.reservation ENABLE ROW LEVEL SECURITY;
ALTER TABLE med.reservation FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON med.reservation
  USING (tenant_id = med.current_tenant())
  WITH CHECK (tenant_id = med.current_tenant());

CREATE TABLE med.reservation_allocation (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  updated_by uuid NOT NULL,
  version bigint NOT NULL DEFAULT 1 CHECK (version>0),
  reservation_id uuid NOT NULL,
  source_line_id uuid NOT NULL,
  balance_id uuid NOT NULL,
  allocated_qty numeric(20,6) NOT NULL CHECK (allocated_qty>0),
  consumed_qty numeric(20,6) NOT NULL DEFAULT 0,
  released_qty numeric(20,6) NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant_id,id),
  CHECK (consumed_qty>=0 AND released_qty>=0 AND consumed_qty+released_qty<=allocated_qty),
  UNIQUE (tenant_id,reservation_id,source_line_id,balance_id),
  FOREIGN KEY (tenant_id,reservation_id) REFERENCES med.reservation(tenant_id,id),
  FOREIGN KEY (tenant_id,balance_id) REFERENCES med.stock_balance(tenant_id,id)
);

COMMENT ON TABLE med.reservation_allocation IS '预占在原业务行和余额之间的分配';

ALTER TABLE med.reservation_allocation ENABLE ROW LEVEL SECURITY;
ALTER TABLE med.reservation_allocation FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON med.reservation_allocation
  USING (tenant_id = med.current_tenant())
  WITH CHECK (tenant_id = med.current_tenant());

CREATE TABLE med.posting_batch (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid NOT NULL,
  entity_id uuid NOT NULL,
  source_type varchar(40) NOT NULL,
  source_id uuid NOT NULL,
  action varchar(40) NOT NULL,
  segment_id uuid NOT NULL,
  business_date date NOT NULL,
  posted_at timestamptz NOT NULL,
  request_id uuid NOT NULL,
  reversal_of uuid,
  result_snapshot jsonb NOT NULL,
  PRIMARY KEY (tenant_id,id),
  UNIQUE (tenant_id,source_type,source_id,action,segment_id),
  FOREIGN KEY (tenant_id,entity_id) REFERENCES med.legal_entity(tenant_id,id),
  FOREIGN KEY (tenant_id,reversal_of) REFERENCES med.posting_batch(tenant_id,id)
);

COMMENT ON TABLE med.posting_batch IS '永久业务过账登记 仅在成功事务提交';

ALTER TABLE med.posting_batch ENABLE ROW LEVEL SECURITY;
ALTER TABLE med.posting_batch FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON med.posting_batch
  USING (tenant_id = med.current_tenant())
  WITH CHECK (tenant_id = med.current_tenant());

CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE ON med.posting_batch
  FOR EACH ROW EXECUTE FUNCTION med.reject_fact_mutation();

CREATE TABLE med.posting_line (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid NOT NULL,
  posting_id uuid NOT NULL,
  source_line_id uuid NOT NULL,
  line_no integer NOT NULL CHECK (line_no>0),
  sku_id uuid NOT NULL,
  base_qty numeric(20,6) NOT NULL CHECK (base_qty>=0),
  product_snapshot jsonb NOT NULL,
  package_snapshot jsonb NOT NULL,
  PRIMARY KEY (tenant_id,id),
  UNIQUE (tenant_id,posting_id,line_no),
  FOREIGN KEY (tenant_id,posting_id) REFERENCES med.posting_batch(tenant_id,id),
  FOREIGN KEY (tenant_id,sku_id) REFERENCES med.sku(tenant_id,id)
);

COMMENT ON TABLE med.posting_line IS '过账源行与快照';

ALTER TABLE med.posting_line ENABLE ROW LEVEL SECURITY;
ALTER TABLE med.posting_line FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON med.posting_line
  USING (tenant_id = med.current_tenant())
  WITH CHECK (tenant_id = med.current_tenant());

CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE ON med.posting_line
  FOR EACH ROW EXECUTE FUNCTION med.reject_fact_mutation();

CREATE TABLE med.stock_ledger (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid NOT NULL,
  posting_line_id uuid NOT NULL,
  effect_index integer NOT NULL CHECK (effect_index>=0),
  balance_id uuid NOT NULL,
  qty_delta numeric(20,6) NOT NULL CHECK (qty_delta<>0),
  event_type varchar(40) NOT NULL,
  posted_at timestamptz NOT NULL,
  reversal_of uuid,
  PRIMARY KEY (tenant_id,id),
  UNIQUE (tenant_id,posting_line_id,effect_index),
  FOREIGN KEY (tenant_id,posting_line_id) REFERENCES med.posting_line(tenant_id,id),
  FOREIGN KEY (tenant_id,balance_id) REFERENCES med.stock_balance(tenant_id,id),
  FOREIGN KEY (tenant_id,reversal_of) REFERENCES med.stock_ledger(tenant_id,id)
);

COMMENT ON TABLE med.stock_ledger IS '不可变数量流水 一个过账行可有多个维度效果';

ALTER TABLE med.stock_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE med.stock_ledger FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON med.stock_ledger
  USING (tenant_id = med.current_tenant())
  WITH CHECK (tenant_id = med.current_tenant());

CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE ON med.stock_ledger
  FOR EACH ROW EXECUTE FUNCTION med.reject_fact_mutation();

CREATE TABLE med.cost_balance (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  updated_by uuid NOT NULL,
  version bigint NOT NULL DEFAULT 1 CHECK (version>0),
  entity_id uuid NOT NULL,
  owner_id uuid NOT NULL,
  sku_id uuid NOT NULL,
  lot_id uuid,
  qty numeric(20,6) NOT NULL CHECK (qty>=0),
  amount numeric(24,8) NOT NULL CHECK (amount>=0),
  PRIMARY KEY (tenant_id,id),
  CHECK (owner_id=entity_id),
  UNIQUE NULLS NOT DISTINCT (tenant_id,entity_id,owner_id,sku_id,lot_id),
  FOREIGN KEY (tenant_id,entity_id) REFERENCES med.legal_entity(tenant_id,id),
  FOREIGN KEY (tenant_id,sku_id) REFERENCES med.sku(tenant_id,id),
  FOREIGN KEY (tenant_id,lot_id,sku_id) REFERENCES med.lot(tenant_id,id,sku_id)
);

COMMENT ON TABLE med.cost_balance IS '主体批次成本池 包含同主体在途与隔离';

ALTER TABLE med.cost_balance ENABLE ROW LEVEL SECURITY;
ALTER TABLE med.cost_balance FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON med.cost_balance
  USING (tenant_id = med.current_tenant())
  WITH CHECK (tenant_id = med.current_tenant());

CREATE TABLE med.cost_ledger (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid NOT NULL,
  posting_line_id uuid NOT NULL,
  effect_index integer NOT NULL CHECK (effect_index>=0),
  cost_balance_id uuid NOT NULL,
  qty_delta numeric(20,6) NOT NULL,
  amount_delta numeric(24,8) NOT NULL,
  origin_cost_entry_id uuid,
  business_date date NOT NULL,
  PRIMARY KEY (tenant_id,id),
  CHECK (qty_delta<>0 OR amount_delta<>0),
  UNIQUE (tenant_id,posting_line_id,effect_index),
  FOREIGN KEY (tenant_id,posting_line_id) REFERENCES med.posting_line(tenant_id,id),
  FOREIGN KEY (tenant_id,cost_balance_id) REFERENCES med.cost_balance(tenant_id,id),
  FOREIGN KEY (tenant_id,origin_cost_entry_id) REFERENCES med.cost_ledger(tenant_id,id)
);

COMMENT ON TABLE med.cost_ledger IS '不可变成本流水 价差允许仅金额变化';

ALTER TABLE med.cost_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE med.cost_ledger FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON med.cost_ledger
  USING (tenant_id = med.current_tenant())
  WITH CHECK (tenant_id = med.current_tenant());

CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE ON med.cost_ledger
  FOR EACH ROW EXECUTE FUNCTION med.reject_fact_mutation();

CREATE TABLE med.idempotency_record (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  updated_by uuid NOT NULL,
  version bigint NOT NULL DEFAULT 1 CHECK (version>0),
  principal_id varchar(100) NOT NULL,
  command varchar(80) NOT NULL,
  target varchar(100) NOT NULL,
  key_hash char(64) NOT NULL,
  request_hash char(64) NOT NULL,
  state varchar(20) NOT NULL CHECK (state IN ('IN_PROGRESS','SUCCEEDED')),
  http_status integer,
  response_json jsonb,
  expires_at timestamptz NOT NULL,
  PRIMARY KEY (tenant_id,id),
  UNIQUE (tenant_id,principal_id,command,target,key_hash),
  CHECK (state<>'SUCCEEDED' OR (http_status BETWEEN 200 AND 299 AND response_json IS NOT NULL))
);

COMMENT ON TABLE med.idempotency_record IS '命令执行和响应快照';

ALTER TABLE med.idempotency_record ENABLE ROW LEVEL SECURITY;
ALTER TABLE med.idempotency_record FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON med.idempotency_record
  USING (tenant_id = med.current_tenant())
  WITH CHECK (tenant_id = med.current_tenant());

CREATE TABLE med.outbox_event (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid NOT NULL,
  aggregate_type varchar(60) NOT NULL,
  aggregate_id uuid NOT NULL,
  aggregate_version bigint NOT NULL CHECK (aggregate_version>0),
  event_type varchar(100) NOT NULL,
  schema_version integer NOT NULL CHECK (schema_version>0),
  occurred_at timestamptz NOT NULL,
  correlation_id uuid NOT NULL,
  causation_id uuid,
  payload jsonb NOT NULL,
  PRIMARY KEY (tenant_id,id)
);

COMMENT ON TABLE med.outbox_event IS '与业务同事务的不可变事件';

ALTER TABLE med.outbox_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE med.outbox_event FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON med.outbox_event
  USING (tenant_id = med.current_tenant())
  WITH CHECK (tenant_id = med.current_tenant());

CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE ON med.outbox_event
  FOR EACH ROW EXECUTE FUNCTION med.reject_fact_mutation();

CREATE TABLE med.outbox_delivery (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  updated_by uuid NOT NULL,
  version bigint NOT NULL DEFAULT 1 CHECK (version>0),
  event_id uuid NOT NULL,
  consumer varchar(100) NOT NULL,
  state varchar(24) NOT NULL CHECK (state IN ('READY','RUNNING','SUCCEEDED','RETRYABLE_FAILED','MANUAL_REQUIRED')),
  next_attempt_at timestamptz NOT NULL,
  attempt integer NOT NULL DEFAULT 0 CHECK (attempt>=0),
  lease_until timestamptz,
  worker_id varchar(100),
  fencing_token bigint NOT NULL DEFAULT 0,
  last_error_code varchar(100),
  PRIMARY KEY (tenant_id,id),
  UNIQUE (tenant_id,event_id,consumer),
  FOREIGN KEY (tenant_id,event_id) REFERENCES med.outbox_event(tenant_id,id)
);

COMMENT ON TABLE med.outbox_delivery IS '每消费者的发送与租约状态';

ALTER TABLE med.outbox_delivery ENABLE ROW LEVEL SECURITY;
ALTER TABLE med.outbox_delivery FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON med.outbox_delivery
  USING (tenant_id = med.current_tenant())
  WITH CHECK (tenant_id = med.current_tenant());

CREATE TABLE med.inbox_message (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  updated_by uuid NOT NULL,
  version bigint NOT NULL DEFAULT 1 CHECK (version>0),
  connection_id uuid NOT NULL,
  external_event_id varchar(200) NOT NULL,
  payload_hash char(64) NOT NULL,
  payload_ref text NOT NULL,
  state varchar(24) NOT NULL,
  received_at timestamptz NOT NULL,
  processed_at timestamptz,
  PRIMARY KEY (tenant_id,id),
  UNIQUE (tenant_id,connection_id,external_event_id)
);

COMMENT ON TABLE med.inbox_message IS '输入消息防重与处理状态';

ALTER TABLE med.inbox_message ENABLE ROW LEVEL SECURITY;
ALTER TABLE med.inbox_message FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON med.inbox_message
  USING (tenant_id = med.current_tenant())
  WITH CHECK (tenant_id = med.current_tenant());

CREATE TABLE med.trace_identity (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  updated_by uuid NOT NULL,
  version bigint NOT NULL DEFAULT 1 CHECK (version>0),
  scheme varchar(40) NOT NULL,
  issuer varchar(80) NOT NULL,
  canonical_hash char(64) NOT NULL,
  raw_bytes bytea NOT NULL,
  display_text text NOT NULL,
  parser_version varchar(40) NOT NULL,
  di text,
  pi_json jsonb,
  package_level varchar(40) NOT NULL,
  PRIMARY KEY (tenant_id,id),
  UNIQUE (tenant_id,scheme,issuer,canonical_hash)
);

COMMENT ON TABLE med.trace_identity IS '码身份 原始字节和规范化身份分离';

ALTER TABLE med.trace_identity ENABLE ROW LEVEL SECURITY;
ALTER TABLE med.trace_identity FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON med.trace_identity
  USING (tenant_id = med.current_tenant())
  WITH CHECK (tenant_id = med.current_tenant());

CREATE TABLE med.trace_holding (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  updated_by uuid NOT NULL,
  version bigint NOT NULL DEFAULT 1 CHECK (version>0),
  trace_id uuid NOT NULL,
  balance_id uuid,
  custody_line_id uuid,
  held_qty numeric(20,6) NOT NULL CHECK (held_qty>=0),
  state varchar(30) NOT NULL CHECK (state IN ('CUSTODY','IN_STOCK','SOLD','DISPOSED')),
  PRIMARY KEY (tenant_id,id),
  UNIQUE (tenant_id,trace_id),
  CHECK (NOT (balance_id IS NOT NULL AND custody_line_id IS NOT NULL)),
  FOREIGN KEY (tenant_id,trace_id) REFERENCES med.trace_identity(tenant_id,id),
  FOREIGN KEY (tenant_id,balance_id) REFERENCES med.stock_balance(tenant_id,id)
);

COMMENT ON TABLE med.trace_holding IS '唯一当前持有记录 退货可恢复数量';

ALTER TABLE med.trace_holding ENABLE ROW LEVEL SECURITY;
ALTER TABLE med.trace_holding FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON med.trace_holding
  USING (tenant_id = med.current_tenant())
  WITH CHECK (tenant_id = med.current_tenant());

CREATE TABLE med.trace_event (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid NOT NULL,
  trace_id uuid NOT NULL,
  posting_line_id uuid,
  source_line_id uuid NOT NULL,
  event_type varchar(50) NOT NULL,
  operation_id uuid NOT NULL,
  qty numeric(20,6) NOT NULL CHECK (qty>0),
  occurred_at timestamptz NOT NULL,
  previous_event_id uuid,
  PRIMARY KEY (tenant_id,id),
  UNIQUE (tenant_id,trace_id,source_line_id,event_type,operation_id),
  FOREIGN KEY (tenant_id,trace_id) REFERENCES med.trace_identity(tenant_id,id),
  FOREIGN KEY (tenant_id,posting_line_id) REFERENCES med.posting_line(tenant_id,id),
  FOREIGN KEY (tenant_id,previous_event_id) REFERENCES med.trace_event(tenant_id,id)
);

COMMENT ON TABLE med.trace_event IS '码流转追加历史';

ALTER TABLE med.trace_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE med.trace_event FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON med.trace_event
  USING (tenant_id = med.current_tenant())
  WITH CHECK (tenant_id = med.current_tenant());

CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE ON med.trace_event
  FOR EACH ROW EXECUTE FUNCTION med.reject_fact_mutation();

CREATE TABLE med.audit_event (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid NOT NULL,
  actor_subject varchar(200) NOT NULL,
  action varchar(100) NOT NULL,
  resource_type varchar(60) NOT NULL,
  resource_id uuid NOT NULL,
  request_id uuid NOT NULL,
  reason text,
  before_hash char(64),
  after_hash char(64),
  evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
  PRIMARY KEY (tenant_id,id)
);

COMMENT ON TABLE med.audit_event IS '关键业务审计追加记录';

ALTER TABLE med.audit_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE med.audit_event FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_scope ON med.audit_event
  USING (tenant_id = med.current_tenant())
  WITH CHECK (tenant_id = med.current_tenant());

CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE ON med.audit_event
  FOR EACH ROW EXECUTE FUNCTION med.reject_fact_mutation();

CREATE INDEX stock_lookup ON med.stock_balance(tenant_id,entity_id,warehouse_id,sku_id,quality_status);

CREATE INDEX ledger_balance_time ON med.stock_ledger(tenant_id,balance_id,posted_at,id);

CREATE INDEX active_reservations ON med.reservation(tenant_id,expires_at,id) WHERE state='ACTIVE';

CREATE INDEX ready_deliveries ON med.outbox_delivery(tenant_id,state,next_attempt_at,id);

CREATE INDEX trace_source ON med.trace_event(tenant_id,source_line_id,occurred_at);

CREATE INDEX lot_expiry ON med.lot(tenant_id,sku_id,expiry_exclusive,id);

COMMIT;

-- Grant template: execute separately with pre-created NON-OWNER roles.
-- med_owner must own schema/tables; runtime must not inherit med_owner.
-- GRANT USAGE ON SCHEMA med TO med_runtime;
-- GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA med TO med_runtime;
-- REVOKE UPDATE ON med.posting_batch,med.posting_line,med.stock_ledger,
--   med.cost_ledger,med.outbox_event,med.trace_event,med.audit_event FROM med_runtime;
-- No DELETE/TRUNCATE/DDL grants. Use a separate approved retention job.
-- REVOKE CREATE ON SCHEMA public FROM PUBLIC;
-- Check pg_roles.rolsuper=false and rolbypassrls=false for every runtime role.
-- Every API/worker DB transaction must set trusted tenant context:
-- SELECT set_config('app.tenant_id', :tenant_id, true);
-- RLS is defense in depth and does not protect from a stolen runtime credential.
