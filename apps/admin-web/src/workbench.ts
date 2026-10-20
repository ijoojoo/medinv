import { computed, onUnmounted, ref, watch, type Ref } from "vue";
import { request, session } from "./api";
import { activeEntity } from "./entityScope";
import type { WorkQueueItem } from "./workQueue";

export const homeViews = [
  {
    value: "insights",
    name: "经营洞察台",
    title: "经营总览",
    note: "经营趋势、往来账款与库存结构",
  },
  {
    value: "risk",
    name: "风险指挥台",
    title: "风险总览",
    note: "当前风险分布、重点范围与原单处理",
  },
  {
    value: "operations",
    name: "高效业务台",
    title: "业务工作台",
    note: "待办办理、常用操作与草稿接续",
  },
] as const;
export type HomeView = (typeof homeViews)[number]["value"];
export const can = (permission: string) =>
  !!session.value?.permissions.includes(permission);
export const allowedHomeViews = computed(() =>
  homeViews.filter(
    (v) =>
      v.value === "operations" ||
      (v.value === "insights"
        ? can("finance.read")
        : [
            "quality.read",
            "purchase.read",
            "returns.read",
            "transfers.read",
          ].some(can)),
  ),
);
const context = computed(
  () =>
    `${session.value?.tenant_id}:${session.value?.principal_id}:${activeEntity.value}`,
);
const preference = ref<HomeView>();
watch(
  context,
  () => {
    preference.value = undefined;
    try {
      const saved = localStorage.getItem("medinv.home.v1:" + context.value);
      if (homeViews.some((v) => v.value === saved))
        preference.value = saved as HomeView;
    } catch {
      /* Browser preferences are optional. */
    }
  },
  { immediate: true, flush: "sync" },
);
export const homeView = computed<HomeView>(
  () =>
    allowedHomeViews.value.find((v) => v.value === preference.value)?.value ||
    (can("finance.read") ? "insights" : "operations"),
);
export function selectHomeView(value: HomeView) {
  if (!allowedHomeViews.value.some((v) => v.value === value)) return;
  preference.value = value;
  try {
    localStorage.setItem("medinv.home.v1:" + context.value, value);
  } catch {
    /* Keep in memory. */
  }
}
export function useWorkbenchData<T>(url: () => string, active: () => boolean) {
  const data = ref<T>() as Ref<T | undefined>;
  const loading = ref(false),
    error = ref("");
  let epoch = 0,
    controller: AbortController | undefined,
    previous = "";
  async function load() {
    const current = ++epoch;
    controller?.abort();
    if (!active() || !activeEntity.value || !session.value) {
      loading.value = false;
      return;
    }
    loading.value = true;
    error.value = "";
    controller = new AbortController();
    try {
      const result = await request<T>(url(), { signal: controller.signal });
      if (current === epoch) data.value = result;
    } catch (e) {
      if (current === epoch)
        error.value = e instanceof Error ? e.message : "读取失败，请重试";
    } finally {
      if (current === epoch) loading.value = false;
    }
  }
  watch(
    () => [
      context.value,
      session.value?.context_generation,
      session.value?.permission_version,
      url(),
      active(),
    ],
    () => {
      const key = `${context.value}:${session.value?.context_generation}:${session.value?.permission_version}:${url()}`;
      if (key !== previous) {
        data.value = undefined;
        error.value = "";
        previous = key;
      }
      void load();
    },
    { immediate: true },
  );
  onUnmounted(() => {
    epoch++;
    controller?.abort();
  });
  return { data, loading, error, load };
}
// Display calculations stay in integer cents. Conversion to Number is only for
// chart coordinates; tooltips and all business values keep their exact strings.
export function cents(value: string): bigint {
  const negative = value.startsWith("-");
  const [whole = "0", decimal = ""] = value.replace(/^-/, "").split(".");
  if (!/^\d+$/.test(whole) || !/^\d*$/.test(decimal)) return 0n;
  const fraction = decimal.padEnd(3, "0");
  const n =
    BigInt(whole) * 100n +
    BigInt(fraction.slice(0, 2)) +
    (Number(fraction[2]) >= 5 ? 1n : 0n);
  return negative ? -n : n;
}
export function money(value: string | null | undefined): string {
  if (value == null) return "—";
  const n = cents(value),
    abs = n < 0 ? -n : n;
  return `${n < 0 ? "−" : ""}${String(abs / 100n).replace(/\B(?=(\d{3})+(?!\d))/g, ",")}.${String(abs % 100n).padStart(2, "0")}`;
}
export function amount(n: bigint): string {
  const abs = n < 0 ? -n : n;
  return `${n < 0 ? "-" : ""}${abs / 100n}.${String(abs % 100n).padStart(2, "0")}`;
}
export function change(
  current: string | null,
  previous: string | null,
): string {
  if (current == null || previous == null) return "金额信息不完整，暂不比较";
  if (cents(previous) <= 0n) return "前期无正值，暂不比较";
  const bps = ((cents(current) - cents(previous)) * 10000n) / cents(previous);
  return `${bps >= 0 ? "+" : "−"}${amount(bps < 0 ? -bps : bps)}%`;
}
export interface QueueData {
  items: WorkQueueItem[];
  has_more: boolean;
  total: number;
  as_of: string;
  priority_count: number;
  counts: Record<string, number>;
  kinds: { code: string; name: string }[];
}
export interface RiskData extends QueueData {
  total_open: number;
  groups: { code: string; name: string; count: number; kinds: string[] }[];
}
