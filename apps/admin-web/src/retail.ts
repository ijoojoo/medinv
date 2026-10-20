import type { PackageSnapshot } from "./business";
export const retailStates: Record<string, string> = {
  RESERVED: "待收款",
  PAID_PENDING_POST: "已收款待出库",
  COMPLETED: "已完成",
  CANCELLED: "已取消",
  REFUNDED: "已退款取消",
};
export interface RetailSale {
  id: string;
  number: string;
  state: string;
  version: string;
  entity_id: string;
  posting_id: string | null;
  gross_amount: string;
  net_amount: string;
  warehouse_name?: string;
  created_at: string;
  auto_released_at: string | null;
}
export interface RetailLine {
  trade_qty: string | null;
  id: string;
  sku_id: string;
  qty: string;
  unit_price: string;
  gross_amount: string;
  product_snapshot: { name: string; specification: string };
  package_snapshot: PackageSnapshot;
}
export interface RetailAllocation {
  id: string;
  source_line_id: string;
  allocated_qty: string;
  lot_no: string;
  expiry_exclusive: string;
}
export interface RetailDetailData {
  sale: RetailSale;
  returns_summary: {
    returned_amount: string;
    refunded_amount: string;
    pending_refund_amount: string;
  };
  lines: RetailLine[];
  allocations: RetailAllocation[];
  reservation: {
    expires_at: string;
    state: string;
    auto_released_at: string | null;
  };
  payments: {
    id: string;
    direction: string;
    amount: string;
    tendered: string;
    change_amount: string;
    created_at: string;
    note: string;
  }[];
}
