import { goPage, setReportRoute } from "./workspace";
import { activeEntity } from "./entityScope";
export type ReportKind =
  "purchase" | "receipt" | "inspection" | "inbound" | "sale" | "outbound";
export const reportKinds: Record<ReportKind, string> = {
  purchase: "采购订单",
  receipt: "采购收货",
  inspection: "采购验收",
  inbound: "采购入库",
  sale: "销售订单",
  outbound: "销售出库",
};
export function openReport(kind: ReportKind) {
  const page = ["sale", "outbound"].includes(kind)
    ? "sales-reports"
    : "purchase-reports";
  goPage(page);
  setReportRoute({
    page,
    entity: activeEntity.value,
    kind,
    tab: "summary",
    filters: {},
  });
}
export interface ReportField {
  key: string;
  label: string;
  width: number;
  visible: boolean;
  required: boolean;
}
export interface ReportRow {
  id: string;
  document_id: string;
  number: string;
  [key: string]: string | number | boolean | null;
}
export interface ReportData {
  items: ReportRow[];
  total: number;
  has_more: boolean;
  fields: ReportField[];
  totals: Record<string, string | number>;
  units: {
    base_unit: string;
    qty: string;
    qualified_qty: string | null;
    rejected_qty: string | null;
  }[];
  chart: {
    items: { label: string; identity: string; value: string }[];
    metric: string;
    unit?: string;
    note: string;
  };
  money_visible: boolean;
  generated_at: string;
  amount_basis: string;
  number_basis: string;
  states: Record<string, string>;
  export_limit: number;
}
export const blankReportFilters = () => ({
  search: "",
  party: "",
  product: "",
  warehouse: "",
  lot_no: "",
  state: "",
  date_from: "",
  date_to: "",
  document_id: "",
  order_id: "",
});
