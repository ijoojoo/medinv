import { can } from "./business";

export function canApplyReturn(kind: string) {
  const permission: Record<string, string> = {
    PURCHASE: "returns.purchase.write",
    SALES: "returns.sales.write",
    RETAIL: "returns.retail.write",
  };
  return can("returns.write") || (!!permission[kind] && can(permission[kind]!));
}
export const canHandleReturnCustody = () =>
  can("returns.write") || can("returns.custody");

export interface ReturnSource {
  posting_id: string;
  entity_id: string;
  kind: string;
  party_name: string;
  source_number: string;
  posted_at: string;
  gross_amount: string;
  returned_amount: string;
  return_number: string | null;
  return_state: string | null;
}
export interface ReturnSourceDetail {
  source: ReturnSource;
  lines: ReturnLine[];
  partial_supported: boolean;
  active_return: { id: string; number: string; state: string } | null;
  history: { items: ReturnDocument[]; has_more: boolean };
}
export interface ReturnDocument {
  id: string;
  number: string;
  entity_id: string;
  kind: string;
  party_name: string;
  source_number: string;
  source_posted_at: string;
  gross_amount: string;
  net_amount: string;
  state: string;
  version: string;
  reason: string;
  review_note: string | null;
  workflow_version: number;
}
export interface ReturnLine {
  id: string;
  name: string;
  base_qty: string;
  base_unit: string;
  lot_no: string;
  remaining_qty: string;
  returned_qty: string;
  gross_amount: string | null;
  net_amount: string | null;
}
export const returnKinds: Record<string, string> = {
  SALES: "销售退货",
  RETAIL: "零售退货",
  PURCHASE: "采购退货",
};
export const returnStates: Record<string, string> = {
  DRAFT: "待处理",
  RECEIVED: "隔离待验",
  REJECTED: "拒收待交还",
  APPROVED: "已复核",
  POSTED: "已退货",
  CANCELLED: "已取消",
};
