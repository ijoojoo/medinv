import { openReport } from "./documentReports";
import { shallowRef, watch } from "vue";
import { session } from "./api";
import { goPage, showRecord } from "./workspace";
export type PurchaseStage = "orders" | "receiving" | "inspection" | "posting";
export type PurchaseView = "pending" | "history";
export const purchaseStages: { key: PurchaseStage; name: string }[] = [
  { key: "orders", name: "采购订单" },
  { key: "receiving", name: "采购收货" },
  { key: "inspection", name: "采购验收" },
  { key: "posting", name: "采购入库" },
];
export interface PurchaseWorkItem {
  id: string;
  number: string;
  order_id: string;
  order_number: string;
  receipt_id: string | null;
  receipt_number: string;
  inspection_id: string | null;
  segment_id: string | null;
  supplier_name: string;
  approval_state: string;
  fulfillment_state: string;
  warehouse_name: string;
  created_at: string;
  state: string;
  quantities: ({ unit: string } & Record<string, string>)[];
  action: "" | "receive" | "inspect" | "post";
  action_note: string;
}
let sequence = 0;
export const purchaseViewRequest = shallowRef<{
  stage: PurchaseStage;
  view: PurchaseView;
  drafts: boolean;
  token: number;
} | null>(null);
export const purchaseTask = shallowRef<{
  item: PurchaseWorkItem;
  entity: string;
  action: PurchaseWorkItem["action"];
  token: number;
} | null>(null);
export const purchaseWorkflowChanged = shallowRef(0);
watch(
  () =>
    `${session.value?.tenant_id}:${session.value?.principal_id}:${session.value?.context_generation}`,
  () => {
    purchaseViewRequest.value = null;
    purchaseTask.value = null;
  },
);
export function purchaseChanged() {
  purchaseWorkflowChanged.value++;
}
export function openPurchaseView(
  stage: PurchaseStage,
  view: PurchaseView = "pending",
  drafts = false,
) {
  if (view === "history") {
    openReport(
      (
        {
          orders: "purchase",
          receiving: "receipt",
          inspection: "inspection",
          posting: "inbound",
        } as const
      )[stage],
    );
    return;
  }
  purchaseViewRequest.value = { stage, view, drafts, token: ++sequence };
  goPage("purchasing");
}
export function openPurchaseTask(
  item: PurchaseWorkItem,
  entity: string,
  perform = false,
) {
  purchaseTask.value = {
    item,
    entity,
    action: perform ? item.action : "",
    token: ++sequence,
  };
  showRecord("purchase", item.order_id, entity, item.order_number);
}
export function workflowStates(
  stage: PurchaseStage,
  view: PurchaseView,
): Record<string, string> {
  if (stage === "orders")
    return {
      DRAFT: "待审核",
      OPEN: "待收齐",
      RECEIVED: "已收齐",
      CLOSED: "余量已关闭",
      CANCELLED: "已取消",
    };
  if (stage === "receiving" && view === "pending")
    return { WAITING: "待收货", PARTIAL: "部分收货" };
  if (stage === "inspection" && view === "history")
    return { RECORDED: "已记录" };
  if (stage === "posting")
    return view === "pending"
      ? { APPROVED: "合格待入库" }
      : { POSTED: "已入库" };
  return {
    WAITING: "待验收",
    PARTIAL: "部分验收",
    ...(view === "history" ? { DONE: "验收完成" } : {}),
  };
}
export function workflowDateLabel(stage: PurchaseStage, view: PurchaseView) {
  if (stage === "orders" || (stage === "receiving" && view === "pending"))
    return "订单日期";
  if (stage === "receiving" || (stage === "inspection" && view === "pending"))
    return "收货日期";
  return stage === "posting" && view === "history" ? "入库日期" : "验收日期";
}
