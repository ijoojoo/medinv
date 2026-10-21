import { computed, reactive, watch, type Ref } from "vue";
import { ApiError, request, session } from "./api";
import {
  defaultEntryColumns,
  normalizeEntryColumns,
  formatEntryNumber,
  decimalMicros,
  entryQuantity,
  type EntryColumn,
  type EntryLine,
  type EntryPackages,
} from "./documentEntry";
export type DocumentStage =
  "orders" | "receiving" | "inspection" | "posting" | "sales_orders";
export type DocumentMode = "editor" | "view";
export interface DocumentField {
  key: string;
  label: string;
  description: string;
  source: "master" | "common" | "stage" | "derived";
  group?: string;
  group_label?: string;
  type: "text" | "number" | "tax" | "date";
  editable: boolean;
  required: boolean;
  stop_required?: boolean;
  default: EntryColumn;
}
export interface DocumentValues {
  qualification_notices?: string[];
  row_key?: string;
  document_values?: Record<string, unknown>;
  document_status?: Record<string, string>;
}
export interface DocumentColumnState {
  scope: string;
  entity: string;
  stage: DocumentStage;
  mode: DocumentMode;
  columns: EntryColumn[];
  fields: DocumentField[];
  notices: string[];
  version: string;
  ready: boolean;
  loading: boolean;
  saving: boolean;
  error: string;
  pending?: { key: string; body: string };
}
const states = new Map<string, DocumentColumnState>();
const scope = () =>
  `${session.value?.tenant_id}:${session.value?.principal_id}:${session.value?.context_generation}:${session.value?.permission_version}`;
watch(scope, () => states.clear());
const path = (s: DocumentColumnState) =>
  `/api/v1/document-columns/${s.stage}/${s.mode}?entity_id=${s.entity}`;
export function useDocumentColumns(
  entity: Ref<string>,
  stage: Ref<DocumentStage>,
  mode: Ref<DocumentMode>,
) {
  return computed(() => {
    const key = `${scope()}:${entity.value}:${stage.value}:${mode.value}`;
    let state = states.get(key);
    if (!state) {
      state = reactive({
        scope: scope(),
        entity: entity.value,
        stage: stage.value,
        mode: mode.value,
        columns: ["orders", "sales_orders"].includes(stage.value)
          ? defaultEntryColumns()
          : [],
        fields: [],
        notices: [],
        version: "0",
        ready: false,
        loading: false,
        saving: false,
        error: "",
      });
      states.set(key, state);
      if (entity.value) void reloadDocumentColumns(state);
    }
    return state;
  });
}
export async function reloadDocumentColumns(s: DocumentColumnState) {
  if (s.loading || s.saving || s.scope !== scope()) return;
  s.loading = true;
  s.error = "";
  try {
    const result = await request<
      Pick<
        DocumentColumnState,
        "columns" | "fields" | "notices" | "version"
      > & { source: string }
    >(path(s));
    if (s.scope !== scope()) return;
    if (
      result.source === "system" &&
      ["orders", "sales_orders"].includes(s.stage) &&
      s.mode === "editor"
    ) {
      try {
        const legacy = JSON.parse(
          localStorage.getItem(
            `medinv.entry-columns.v1:${session.value?.tenant_id}:${session.value?.principal_id}:${s.stage === "sales_orders" ? "sale" : "purchase"}`,
          ) || "null",
        );
        if (Array.isArray(legacy) && legacy.length) {
          result.columns = copyDocumentColumns(
            normalizeEntryColumns(legacy),
            result.fields,
          );
          result.notices.push(
            "已读取本浏览器的旧列设置；保存后在当前主体跨浏览器使用。",
          );
        }
      } catch {
        /* An unreadable old preference must not block entering a document. */
      }
    }
    Object.assign(s, result, { ready: true, pending: undefined });
  } catch (e) {
    s.error = e instanceof Error ? e.message : "列配置读取失败";
  } finally {
    s.loading = false;
  }
}
export function defaultDocumentColumns(fields: DocumentField[]): EntryColumn[] {
  return fields.map((f) => ({
    ...f.default,
    required: f.required,
    editable: f.editable,
  }));
}
export function copyDocumentColumns(
  columns: EntryColumn[],
  fields: DocumentField[],
) {
  const defaults = defaultDocumentColumns(fields),
    result: EntryColumn[] = [];
  for (const c of columns) {
    const target = defaults.find((d) => d.key === c.key);
    if (target)
      result.push({
        ...target,
        ...c,
        required: target.required,
        editable: target.editable,
        visible: target.required || c.visible,
        tab_stop:
          !!target.editable &&
          (!!fields.find((f) => f.key === c.key)?.stop_required || c.tab_stop),
      });
  }
  return [
    ...result,
    ...defaults.filter((d) => !result.some((c) => c.key === d.key)),
  ];
}
export async function saveDocumentColumns(
  s: DocumentColumnState,
  columns: EntryColumn[],
  version: string,
) {
  if (!s.ready || s.saving || s.scope !== scope()) return false;
  s.saving = true;
  s.error = "";
  s.pending ??= {
    key: crypto.randomUUID(),
    body: JSON.stringify({
      expected_version: version,
      schema_version: 1,
      columns: columns.map(({ required, editable, ...c }) => c),
    }),
  };
  try {
    const result = await request<{ version: string; columns: EntryColumn[] }>(
      path(s),
      {
        method: "POST",
        headers: { "Idempotency-Key": s.pending.key },
        body: s.pending.body,
      },
    );
    if (s.scope !== scope()) return false;
    Object.assign(s, result, { pending: undefined });
    return true;
  } catch (e) {
    s.error = e instanceof Error ? e.message : "列配置保存失败";
    if (e instanceof ApiError && e.status >= 400 && e.status < 500)
      s.pending = undefined;
    return false;
  } finally {
    s.saving = false;
  }
}
export function documentCell(row: DocumentValues, column: EntryColumn) {
  if (row.document_status?.[column.key])
    return row.document_status[column.key]!;
  const value = row.document_values?.[column.key];
  if (value === null || value === undefined || value === "") return "—";
  const raw = String(value);
  if (column.key === "tax" && column.tax_display === "percent") {
    const n = decimalMicros(raw);
    if (n !== null) {
      const fraction = String(n % 10000n)
        .padStart(4, "0")
        .replace(/0+$/, "");
      return `${n / 10000n}${fraction ? "." + fraction : ""}%`;
    }
  }
  return formatEntryNumber(raw, column);
}
export function entryDocumentValues(
  row: EntryLine,
  packages?: EntryPackages,
): DocumentValues {
  const product = row.product;
  if (!product) return {};
  const pack = packages?.items.find((p) => p.id === row.packaging_id);
  const qty = entryQuantity(
    row.qty,
    product.qty_scale,
    product.base_unit,
    pack,
  ).micros;
  const base =
    qty === undefined || (row.packaging_id && !pack)
      ? undefined
      : `${qty / 1000000n}.${String(qty % 1000000n).padStart(6, "0")}`;
  return {
    document_status: product.document_status,
    document_values: {
      ...product.document_values,
      base_qty: base,
      conversion: pack
        ? `${pack.denominator} ${pack.unit_name} = ${pack.numerator} ${product.base_unit}`
        : row.packaging_id
          ? "包装待核对"
          : "基本单位交易",
    },
  };
}
