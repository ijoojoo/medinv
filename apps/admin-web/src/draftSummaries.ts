import { computed } from "vue";
import { activeEntity } from "./entityScope";
import { can } from "./workbench";
import {
  editors,
  isDirty,
  openedEditors,
  type DraftRecord,
  type EditorState,
} from "./workspace";

export type DraftSummary = Omit<
  DraftRecord,
  "content" | "converted_order_id"
> & {
  party_name?: string;
  warehouse_name?: string;
  source_number?: string;
  line_count?: number;
};
export interface DraftList {
  items: DraftSummary[];
  total: number;
  has_more: boolean;
}
export const canReadDrafts = computed(
  () => can("purchase.write") || can("sales.write"),
);
export const currentDrafts = computed(() =>
  openedEditors.value
    .map((id) => editors[id])
    .filter(
      (s): s is EditorState =>
        !!s &&
        s.record.entity_id === activeEntity.value &&
        s.record.state === "EDITING" &&
        can(s.record.channel === "purchase" ? "purchase.write" : "sales.write"),
    ),
);
export const needsDraftAttention = (s: EditorState) =>
  s.conflict ||
  !!s.error ||
  !!s.pending ||
  s.saving ||
  isDirty(s) ||
  s.record.version === "0";
export const pendingDrafts = computed(() =>
  currentDrafts.value.filter(needsDraftAttention),
);
export function draftStatus(s?: EditorState) {
  if (!s) return "已保存";
  return s.conflict
    ? "版本冲突，请核对"
    : s.error
      ? "保存失败，当前输入已保留"
      : s.saving
        ? "正在保存"
        : s.pending
          ? "保存结果待核对，当前输入已保留"
          : s.record.version === "0"
            ? "草稿尚未保存"
            : isDirty(s)
              ? "有未保存修改"
              : "已保存";
}
export function draftName(d: DraftSummary) {
  return (
    d.title ||
    `${d.channel === "purchase" ? "采购" : "销售"}${d.source_order_id ? "改单" : "新单"}草稿`
  );
}
export function summarizeDraft(s: EditorState): DraftSummary {
  return {
    ...s.record,
    line_count: s.content.lines.filter((l) => !!l.sku_id).length,
  };
}
