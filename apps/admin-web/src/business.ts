import { onUnmounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { request, session } from "./api";
import { submitCommand } from "./commandRecovery";

export const can = (permission: string) =>
  session.value?.permissions.includes(permission) ?? false;
export const labels: Record<string, string> = {
  DRAFT: "待审核",
  APPROVED: "已审核",
  SUSPENDED: "已停用",
  CANCELLED: "已取消",
  OPEN: "待收齐",
  RECEIVED: "已收齐",
  CLOSED: "未收余量已关闭",
  POSTED: "已入库",
  SUPPLIER: "供应商",
  CUSTOMER: "客户",
  BOTH: "供应商及客户",
  DRUG: "药品",
  DEVICE: "医疗器械",
};
export function quantity(value: string) {
  return value.includes(".")
    ? value.replace(/0+$/, "").replace(/\.$/, "")
    : value;
}
export const positive = (value: string) => /[1-9]/.test(value);
export async function loadOptions<T>(path: string): Promise<T> {
  const context = () =>
    `${session.value?.tenant_id}:${session.value?.context_generation}`;
  const initial = context();
  const result: Record<string, { id: string }[]> = {};
  let offset = 0;
  for (;;) {
    if (context() !== initial)
      throw new Error("企业上下文已变化，请刷新资料选项");
    const page = await request<Record<string, unknown>>(
      `${path}${path.includes("?") ? "&" : "?"}offset=${offset}`,
    );
    if (context() !== initial)
      throw new Error("企业上下文已变化，请刷新资料选项");
    for (const [key, items] of Object.entries(page)) {
      if (!Array.isArray(items)) continue;
      // Catalog changes between pages may repeat a row; keep one entry per identity.
      result[key] = [
        ...new Map(
          [...(result[key] || []), ...items].map((item) => [item.id, item]),
        ).values(),
      ];
    }
    if (!page.has_more) return result as T;
    const next = page.next_offset;
    if (typeof next !== "number" || next <= offset || next > 100000)
      throw new Error("资料选项分页无效，请刷新后重试");
    offset = next;
  }
}
export function useCommand(failure: (error: unknown) => void) {
  const busy = ref(false);
  let active = true;
  onUnmounted(() => {
    active = false;
  });
  async function execute<T>(
    path: string,
    payload: unknown,
    done: (data: T) => void | Promise<void>,
  ) {
    if (busy.value) return;
    busy.value = true;
    const context = `${session.value?.tenant_id}:${session.value?.context_generation}`;
    try {
      const data = await submitCommand<T>(path, payload);
      if (
        active &&
        context ===
          `${session.value?.tenant_id}:${session.value?.context_generation}`
      ) {
        ElMessage.success("操作已保存");
        await done(data);
      }
    } catch (error) {
      if (
        active &&
        context ===
          `${session.value?.tenant_id}:${session.value?.context_generation}`
      )
        failure(error);
    } finally {
      busy.value = false;
    }
  }
  return { busy, execute };
}
export interface PageData<T> {
  items: T[];
  has_more: boolean;
}
export interface PurchaseOptions {
  suppliers: { id: string; name: string }[];
  skus: {
    id: string;
    name: string;
    code: string;
    base_unit: string;
    qty_scale: number;
  }[];
  warehouses: { id: string; name: string }[];
  locations: { id: string; code: string; name: string; warehouse_id: string }[];
}

export interface PackageSnapshot {
  base_unit: string;
  qty_scale: number;
  unit_name?: string;
  numerator: number;
  denominator: number;
  revision?: number;
  packaging_id?: string;
  trade_qty?: string;
  base_qty?: string;
}
