export const transferStates: Record<string, string> = {
  DRAFT: "待审核",
  APPROVED: "待发出",
  IN_TRANSIT: "在途待收货",
  PART_RECEIVED: "部分收货",
  RECEIVED: "已收齐",
  CLOSED_WITH_LOSS: "已结清（含损失）",
  CANCELLED: "已取消",
};
export interface TransferDocument {
  id: string;
  number: string;
  version: string;
  state: string;
  source_name: string;
  destination_name: string;
  destination_location: string;
  reason: string;
  created_by: string;
  created_at: string;
  review_note: string | null;
  cancel_note: string | null;
  dispatch_posting_id: string | null;
}
export interface TransferLine {
  id: string;
  name: string;
  base_unit: string;
  lot_no: string;
  source_location: string;
  qty: string;
  received_qty: string;
  lost_qty: string;
  remaining_qty: string;
}
export interface TransferDetailData {
  document: TransferDocument;
  lines: TransferLine[];
  has_more: boolean;
  receipts: {
    id: string;
    number: string;
    note: string;
    created_at: string;
    lines: { line_id: string; qty: string; quality_status: string }[];
  }[];
}
export interface TransferOptions {
  warehouses: { id: string; name: string; code: string }[];
  locations: { id: string; code: string; name: string; warehouse_id: string }[];
}

export const lossStates: Record<string, string> = {
  DRAFT: "待审核",
  APPROVED: "待核销",
  POSTED: "已核销",
  CANCELLED: "已取消",
};
export interface TransferLoss {
  id: string;
  transfer_id: string;
  line_id: string;
  number: string;
  line_no: number;
  qty: string;
  lot_no: string;
  reason: string;
  evidence: string;
  state: string;
  version: string;
  created_by: string;
  created_at: string;
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_note: string | null;
  cancel_note: string | null;
  name: string;
  base_unit: string;
  result: {
    posting_id: string;
    qty: string;
    loss_cost: string;
    transfer_state: string;
  } | null;
}
