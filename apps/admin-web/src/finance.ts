export interface Account {
  id: string;
  code: string;
  name: string;
  method: string;
}
export interface SettlementItem {
  id: string;
  party_id: string;
  party_name: string;
  kind: string;
  gross_amount: string;
  settled_amount: string;
  returned_amount: string;
  outstanding_amount: string;
  version: string;
  posting_id: string;
  source_number: string | null;
  due_date: string | null;
  created_at: string;
}
export interface CashDocument {
  id: string;
  number: string;
  entity_id: string;
  party_id: string;
  party_name: string;
  account_name: string;
  direction: string;
  amount: string;
  allocated_amount: string;
  refunded_amount: string;
  remaining_amount: string;
  state: string;
  version: string;
  reference: string;
  occurred_on: string;
  note: string;
}
export interface Allocation {
  id: string;
  item_id: string;
  source_number: string | null;
  amount: string;
  created_at: string;
  reversal_id: string | null;
  reversal_reason: string | null;
  reversed_at: string | null;
}
export const directions: Record<string, string> = {
  RECEIPT: "收款",
  PAYMENT: "付款",
};
