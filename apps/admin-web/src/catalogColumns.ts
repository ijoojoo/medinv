import { computed, ref, watch, type Ref } from "vue";
import { session } from "./api";

export interface ListColumnDefinition {
  key: string;
  label: string;
  width: number;
  visible?: boolean;
  required?: boolean;
}
export interface ListColumn extends ListColumnDefinition {
  visible: boolean;
  wrap: boolean;
}
export function normalizeListColumns(
  raw: unknown,
  definitions: ListColumnDefinition[],
): ListColumn[] {
  const defaults = new Map(definitions.map((d) => [d.key, d]));
  const seen = new Set<string>();
  const result: ListColumn[] = [];
  for (const item of Array.isArray(raw) ? raw : []) {
    if (
      !item ||
      typeof item !== "object" ||
      typeof item.key !== "string" ||
      seen.has(item.key)
    )
      continue;
    const definition = defaults.get(item.key);
    if (!definition) continue;
    seen.add(item.key);
    result.push({
      ...definition,
      visible:
        !!definition.required ||
        (typeof item.visible === "boolean"
          ? item.visible
          : definition.visible !== false),
      width:
        Number.isInteger(item.width) && item.width >= 80 && item.width <= 640
          ? item.width
          : definition.width,
      wrap: item.wrap === true,
    });
  }
  for (const definition of definitions) {
    if (!seen.has(definition.key))
      result.push({
        ...definition,
        visible: !!definition.required || definition.visible !== false,
        wrap: false,
      });
  }
  return result;
}
export function useCatalogColumns(
  scope: Ref<string>,
  definitions: Ref<ListColumnDefinition[]>,
) {
  const storageKey = computed(() =>
    session.value
      ? `medinv.catalog-columns.v1:${session.value.tenant_id}:${session.value.principal_id}:${scope.value}`
      : "",
  );
  const saved = ref<unknown>(null);
  watch(
    storageKey,
    (key) => {
      saved.value = null;
      if (!key) return;
      try {
        const value = JSON.parse(localStorage.getItem(key) || "null");
        if (value?.version === 1) saved.value = value.columns;
      } catch {
        /* Unavailable or invalid personal preferences fall back to defaults. */
      }
    },
    { immediate: true },
  );
  const columns = computed(() =>
    normalizeListColumns(saved.value, definitions.value),
  );
  const visible = computed(() => columns.value.filter((c) => c.visible));
  function save(value: ListColumn[]): string | null {
    if (!storageKey.value) return "请登录后保存列设置。";
    const clean = normalizeListColumns(value, definitions.value).map(
      ({ key, visible, width, wrap }) => ({ key, visible, width, wrap }),
    );
    try {
      localStorage.setItem(
        storageKey.value,
        JSON.stringify({ version: 1, columns: clean }),
      );
      saved.value = clean;
      return null;
    } catch {
      return "浏览器未能保存设置，请检查是否允许本网站存储后重试。当前列表未改变。";
    }
  }
  return { columns, visible, save, storageKey };
}

export const partyListColumns: ListColumnDefinition[] = [
  { key: "identity", label: "名称 / 编码", width: 250, required: true },
  { key: "kind", label: "单位类型", width: 145 },
  { key: "contact", label: "联系 / 信用代码", width: 250 },
  { key: "validity", label: "资质有效期", width: 195 },
  { key: "state", label: "状态", width: 110, required: true },
  { key: "short_name", label: "单位简称", width: 180, visible: false },
  {
    key: "search_alias",
    label: "检索别名 / 助记码",
    width: 180,
    visible: false,
  },
  { key: "organization_type", label: "机构性质", width: 145, visible: false },
  {
    key: "license_no",
    label: "经营许可 / 备案编号",
    width: 220,
    visible: false,
  },
  {
    key: "delivery_address",
    label: "默认收发货地址",
    width: 280,
    visible: false,
  },
  { key: "registered_address", label: "注册地址", width: 280, visible: false },
  { key: "business_address", label: "经营地址", width: 280, visible: false },
  {
    key: "recommendation_level",
    label: "推荐级别",
    width: 130,
    visible: false,
  },
  { key: "abc_class", label: "ABC 分类 / 第一维", width: 145, visible: false },
  {
    key: "abc_secondary_class",
    label: "双 ABC / 第二维",
    width: 145,
    visible: false,
  },
];
