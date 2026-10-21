import { computed, ref, watch } from "vue";
import { session } from "./api";
import { activeEntity } from "./entityScope";
import { can } from "./workbench";
import { navigationGroups, itemForPage } from "./navigation";
import type { EntryChannel } from "./documentEntry";

export interface WorkbenchShortcut {
  id: string;
  page: string;
  name: string;
  icon: string;
  permission: string;
  channel?: EntryChannel;
}
const definitions: WorkbenchShortcut[] = [
  {
    id: "purchase-new",
    page: "purchasing",
    name: "采购开单",
    icon: "＋",
    permission: "purchase.write",
    channel: "purchase",
  },
  {
    id: "sale-new",
    page: "selling",
    name: "销售开单",
    icon: "↗",
    permission: "sales.write",
    channel: "sale",
  },
  ...navigationGroups
    .flatMap((g) => g.items)
    .filter((i) => i.page !== "appearance")
    .map((i) => ({
      ...i,
      id: i.page,
      name: i.name,
    })),
];
export const availableShortcuts = computed(() =>
  definitions.filter(
    (i) => can(i.permission) && can(itemForPage(i.page)?.permission || ""),
  ),
);
const defaults = [
  "purchase-new",
  "sale-new",
  "inventory",
  "products",
  "parties",
  "finance",
];
export const defaultShortcuts = computed(() =>
  defaults
    .map((id) => availableShortcuts.value.find((s) => s.id === id))
    .filter((s): s is WorkbenchShortcut => !!s),
);
const saved = ref<string[]>();
const key = computed(
  () =>
    `medinv.home.shortcuts.v1:${session.value?.tenant_id}:${session.value?.principal_id}:${activeEntity.value}`,
);
watch(
  key,
  () => {
    saved.value = undefined;
    try {
      const value: unknown = JSON.parse(
        localStorage.getItem(key.value) || "null",
      );
      if (Array.isArray(value))
        saved.value = [
          ...new Set(
            value.filter(
              (id): id is string =>
                typeof id === "string" && definitions.some((d) => d.id === id),
            ),
          ),
        ].slice(0, 9);
    } catch {
      /* Optional preference. */
    }
  },
  { immediate: true, flush: "sync" },
);
export const selectedShortcuts = computed(() => {
  const ids = saved.value ?? defaults;
  return ids
    .map((id) => availableShortcuts.value.find((s) => s.id === id))
    .filter((s): s is WorkbenchShortcut => !!s);
});
export function saveShortcuts(ids: string[]) {
  saved.value = [...new Set(ids)]
    .filter((id) => availableShortcuts.value.some((s) => s.id === id))
    .slice(0, 9);
  try {
    localStorage.setItem(key.value, JSON.stringify(saved.value));
  } catch {
    /* Keep session preference. */
  }
}
