export const qualityStates: Record<string, string> = {
  DRAFT: "待审核",
  APPROVED: "待过账",
  POSTED: "已过账",
  CANCELLED: "已取消",
};
export const dispositionKinds: Record<string, string> = {
  RELEASE: "合格放行",
  WRITE_OFF: "报损出库",
};
export const stockQuality: Record<string, string> = {
  QUARANTINE: "隔离待验",
  UNQUALIFIED: "不合格",
};
export interface Disposition {
  id: string;
  number: string;
  entity_id: string;
  kind: string;
  state: string;
  version: string;
  name: string;
  warehouse_name: string;
  location_code: string;
  lot_no: string;
  qty: string;
  base_unit: string;
  source_quality: string;
  reason: string;
  evidence: string;
  created_by: string;
  created_at: string;
  review_note: string | null;
  reviewed_at: string | null;
  cancel_note: string | null;
}
export interface DispositionDetailData {
  document: Disposition;
  result: {
    posting_id: string;
    qty: string;
    loss_cost: string;
    cost_delta: string;
  } | null;
}
