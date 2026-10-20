export const countStates: Record<string, string> = {
  COUNTING: "待实盘",
  SUBMITTED: "待审核",
  APPROVED: "已审核",
  POSTED: "已过账",
  CANCELLED: "已取消",
};
export interface CountDocument {
  id: string;
  number: string;
  version: string;
  state: string;
  warehouse_name: string;
  reason: string;
  created_at: string;
  created_by: string;
  submitted_by: string | null;
  submit_note: string | null;
  review_note: string | null;
  cancel_note: string | null;
}
export interface CountLine {
  id: string;
  name: string;
  base_unit: string;
  lot_no: string;
  location_code: string;
  book_qty: string;
  reserved_qty: string;
  blocked_qty: string;
  pool_qty: string;
  pool_amount: string;
  actual_qty: string | null;
  qty_delta: string | null;
  cost_delta: string | null;
  count_note: string | null;
  gain_unit_cost: string | null;
}
export interface CountDetailData {
  document: CountDocument;
  lines: CountLine[];
  result: {
    posting_id: string;
    changed_lines: number;
    gain_cost: string;
    loss_cost: string;
    cost_delta: string;
  } | null;
}
