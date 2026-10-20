import type { PackageSnapshot } from "./business";
import type { DocumentValues } from "./documentColumns";
export const salesLabels: Record<string, string> = {
  DRAFT: "待审核",
  APPROVED: "待发齐",
  CANCELLED: "已取消",
  CLOSED: "余量已关闭",
  FULFILLED: "已发齐",
  ALLOCATED: "待拣货",
  PICKED: "待复核",
  VERIFIED: "待出库",
  POSTED: "已出库",
};
export const localTime = (value: string) =>
  new Date(value).toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour12: false,
  });
export interface SalesOptions {
  customers: { id: string; name: string }[];
  warehouses: { id: string; name: string }[];
  skus: { id: string; name: string; code: string; base_unit: string }[];
}
export interface Credit {
  credit_limit: string;
  exposure: string;
  receivable: string;
  available: string;
  valid_until: string | null;
  version: string;
}
export interface SalesOrder {
  entity_id: string;
  customer_id: string;
  warehouse_id: string;
  revision_no: number;
  revision_editors: string[];
  can_revise: boolean;
  id: string;
  number: string;
  customer_name: string;
  warehouse_name: string;
  state: string;
  gross_amount: string;
  remaining_exposure: string;
  version: string;
  created_by: string;
  created_at: string;
  delivery_address: string;
  minimum_shelf_days: number;
  credit_days: number;
}
export interface SalesLine extends DocumentValues {
  sku_id: string;
  tax_rate: string;
  trade_qty: string | null;
  package_snapshot: PackageSnapshot | null;
  id: string;
  name: string;
  base_unit: string;
  qty: string;
  reserved_qty: string;
  fulfilled_qty: string;
  remaining_qty: string;
  unit_price: string;
  gross_amount: string;
}
export interface ShipmentLine {
  id: string;
  name: string;
  base_unit: string;
  qty: string;
  lot_no: string;
  raw_expiry: string;
  location_code: string;
  picked_qty: string | null;
}
export interface Shipment {
  posting_id: string | null;
  id: string;
  number: string;
  state: string;
  version: string;
  expired: boolean;
  expires_at: string;
  auto_released_at: string | null;
  picked_by: string | null;
  shipping_ref: string | null;
  lines: ShipmentLine[];
}
export interface SalesDetailData {
  order: SalesOrder;
  lines: SalesLine[];
  shipments: Shipment[];
}
