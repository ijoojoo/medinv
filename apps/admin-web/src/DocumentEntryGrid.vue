<script setup lang="ts">
import {
  computed,
  nextTick,
  ref,
  watch,
  onMounted,
  onUnmounted,
  toRef,
} from "vue";
import { appearance } from "./appearance";
import type { EntryResume } from "./entryResume";
import DocumentColumnSettings from "./DocumentColumnSettings.vue";
import {
  documentCell,
  entryDocumentValues,
  useDocumentColumns,
  reloadDocumentColumns,
} from "./documentColumns";
import EntryNumberInput from "./EntryNumberInput.vue";
import EntryProductPicker from "./EntryProductPicker.vue";
import EntryTradeHint from "./EntryTradeHint.vue";
import { request, session } from "./api";
import PackagingSelect from "./PackagingSelect.vue";
import EntryPaste from "./EntryPaste.vue";
import EntryFileImport from "./EntryFileImport.vue";
import EntryFileExport from "./EntryFileExport.vue";
import { entryLinesSignature, type EntryImportMode } from "./entryFiles";
import EntryTaxInput from "./EntryTaxInput.vue";
import {
  blankEntryLine,
  centsText,
  columnLabel,
  formatEntryNumber,
  entryColumnWidths,
  lineCents,
  lineIssue,
  newEntryLine,
  type EntryChannel,
  type EntryLine,
  type EntryProduct,
  type EntryPackages,
} from "./documentEntry";
const props = defineProps<{
  entity: string;
  channel: EntryChannel;
  disabled?: boolean;
  active?: boolean;
}>();
const emit = defineEmits<{ failure: [error: unknown] }>();
const model = defineModel<EntryLine[]>({ required: true });
const root = ref<HTMLElement>();
const activeKey = ref(""),
  activeColumn = ref("product"),
  error = ref("");
const issueAnchor = ref<{ key: string; message: string }>();
const pending = ref<Record<string, boolean>>({});
const changed = ref<Record<string, boolean>>({});
const changedUnits = ref<Record<string, boolean>>({});
const removed = ref<{ line: EntryLine; index: number }[]>([]);
const packages = new Map<string, Promise<EntryPackages>>();
// Restored documents often repeat a product. Share only in-flight reads within
// this editor; a later restore must fetch fresh permissions and master values.
const productLoads = new Map<string, Promise<EntryProduct | undefined>>();
const hydrationCount = ref(0);
let productController = new AbortController();
function hydrateProduct(sku: string): Promise<EntryProduct | undefined> {
  const existing = productLoads.get(sku);
  if (existing) return existing;
  hydrationCount.value++;
  const promise = request<{ items: EntryProduct[] }>(
    `/api/v1/catalog/entry-products?${new URLSearchParams({
      entity_id: props.entity,
      channel: props.channel,
      sku_id: sku,
    })}`,
    { signal: productController.signal },
  )
    .then((result) => result.items.find((p) => p.id === sku))
    .finally(() => {
      if (productLoads.get(sku) === promise) {
        productLoads.delete(sku);
        hydrationCount.value--;
      }
    });
  productLoads.set(sku, promise);
  return promise;
}
function resetProductLoads() {
  productController.abort();
  productController = new AbortController();
  productLoads.clear();
  hydrationCount.value = 0;
}
const packageRevision = ref<Record<string, number>>({});
const packageChecks = ref<
  Record<
    string,
    {
      request: number;
      data?: EntryPackages;
      failed?: boolean;
    }
  >
>({});
let packageRequest = 0;
function acceptPackages(sku: string, data: EntryPackages) {
  const previous = packageChecks.value[sku]?.data;
  packageChecks.value[sku] = { request: ++packageRequest, data };
  packages.set(sku, Promise.resolve(data));
  if (previous && JSON.stringify(previous) !== JSON.stringify(data))
    packageRevision.value[sku] = (packageRevision.value[sku] || 0) + 1;
}
async function checkPackages(sku: string) {
  if (packageChecks.value[sku]) return;
  const current = ++packageRequest;
  packageChecks.value[sku] = { request: current };
  let loading = packages.get(sku);
  if (!loading) {
    loading = request<EntryPackages>(
      `/api/v1/catalog/skus/${sku}/packaging?approved_only=true`,
    );
    packages.set(sku, loading);
  }
  try {
    const data = await loading;
    if (packageChecks.value[sku]?.request === current)
      acceptPackages(sku, data);
  } catch {
    if (packages.get(sku) === loading) packages.delete(sku);
    if (packageChecks.value[sku]?.request === current)
      packageChecks.value[sku] = { request: current, failed: true };
  }
}
function resetPackages() {
  resetProductLoads();
  packages.clear();
  packageChecks.value = {};
  for (const row of model.value)
    packageRevision.value[row.sku_id] =
      (packageRevision.value[row.sku_id] || 0) + 1;
}
watch(
  () => [
    session.value?.tenant_id,
    session.value?.principal_id,
    session.value?.context_generation,
    session.value?.permission_version,
    props.entity,
    props.channel,
  ],
  resetPackages,
  { flush: "sync" },
);
// Validation must also load units for restored/imported rows when the user has
// hidden the unit column. Requests are shared with the visible unit selectors.
watch(
  () => [
    props.active,
    packageChecks.value,
    model.value.map((r) => [
      r.sku_id,
      r.packaging_id,
      packageRevision.value[r.sku_id],
    ]),
  ],
  () => {
    if (props.active === false) return;
    for (const row of model.value)
      if (row.sku_id && row.packaging_id) void checkPackages(row.sku_id);
  },
  { immediate: true },
);
onUnmounted(() => {
  resetProductLoads();
  packageChecks.value = {};
});
const failedPackages = computed(() => [
  ...new Set(
    model.value
      .filter((r) => r.packaging_id && packageChecks.value[r.sku_id]?.failed)
      .map((r) => r.sku_id),
  ),
]);
function retryPackages() {
  for (const sku of failedPackages.value) {
    delete packageChecks.value[sku];
    packages.delete(sku);
    packageRevision.value[sku] = (packageRevision.value[sku] || 0) + 1;
    void checkPackages(sku);
  }
}
function rowIssue(row: EntryLine) {
  const state = packageChecks.value[row.sku_id];
  const issue = lineIssue(row, state?.data);
  if (issue?.column === "product" || issue?.column === "qty") return issue;
  if (row.sku_id && !row.product)
    return {
      column: "product",
      message: "商品资料尚未读取完成，请稍后重试或重新选择商品",
    };
  if (row.packaging_id && !state?.data)
    return {
      column: "qty",
      message: state?.failed
        ? "包装资料读取失败，请点击「重新校验包装」后再继续，已录入内容保留"
        : "正在校验包装换算，请稍后再继续",
    };
  return issue;
}
function quantityIssue(row: EntryLine) {
  if (!row.qty.trim() || !row.product || pending.value[row.client_key])
    return "";
  const issue = lineIssue(row, packageChecks.value[row.sku_id]?.data);
  return issue?.column === "qty" ? issue.message : "";
}
watch(
  () => {
    const row = model.value.find(
      (r) => r.client_key === issueAnchor.value?.key,
    );
    return row && rowIssue(row)?.message;
  },
  (message, previous) => {
    if (
      issueAnchor.value &&
      previous === issueAnchor.value.message &&
      message !== previous
    ) {
      error.value = "";
      issueAnchor.value = undefined;
    }
  },
);
const purchaseTemplate = useDocumentColumns(
  toRef(props, "entity"),
  computed(() => (props.channel === "sale" ? "sales_orders" : "orders")),
  ref("editor"),
);
const template = purchaseTemplate;
function reloadTemplate() {
  return reloadDocumentColumns(purchaseTemplate.value);
}
const columns = computed(() => template.value.columns);
const revealed = ref<string[]>([]);
const configuring = ref(false),
  pasting = ref(false),
  importing = ref(false),
  exporting = ref(false);
const importUndo = ref<{ before: EntryLine[]; after: string }>();
const copyUndo = ref<{ key: string; sourceKey: string; signature: string }>();
const canUndoCopy = computed(() => {
  const item = copyUndo.value;
  const row = item && model.value.find((r) => r.client_key === item.key);
  return !!(
    row &&
    !pending.value[row.client_key] &&
    entryLinesSignature([row]) === item!.signature
  );
});
const canUndoImport = computed(
  () =>
    !!importUndo.value &&
    entryLinesSignature(model.value) === importUndo.value.after,
);
watch(columns, () => {
  revealed.value = [];
});
const shown = computed(() =>
  columns.value.filter((c) => c.visible || revealed.value.includes(c.key)),
);
const editable = computed(() =>
  shown.value.filter((c) => c.editable && c.tab_stop).map((c) => c.key),
);
const scroll = ref<HTMLElement>();
const available = ref(0);
let observer: ResizeObserver | undefined;
let visibilityFrame = 0;
function keepActiveVisible() {
  cancelAnimationFrame(visibilityFrame);
  visibilityFrame = requestAnimationFrame(() => {
    const control = document.activeElement;
    const viewport = scroll.value;
    if (
      !(control instanceof HTMLElement) ||
      !viewport?.contains(control) ||
      !viewport.clientHeight
    )
      return;
    // First reveal any outer scrolling region (small screens / expanded stock panel).
    // Then account for sticky headers and footers, which scrollIntoView ignores.
    control.scrollIntoView({
      block: "nearest",
      inline: "nearest",
      behavior: "instant",
    });
    const bounds = viewport.getBoundingClientRect();
    const headerHeight =
      viewport.querySelector("thead")?.getBoundingClientRect().height || 0;
    const chrome = document
      .querySelector(".workspace-chrome")
      ?.getBoundingClientRect();
    const footer = root.value
      ?.closest(".document-editor")
      ?.querySelector(".editor-actions")
      ?.getBoundingClientRect();
    const top =
      Math.max(
        bounds.top + viewport.clientTop + headerHeight,
        chrome?.bottom || 0,
      ) + 8;
    const bottom =
      Math.min(
        bounds.top + viewport.clientTop + viewport.clientHeight,
        window.innerHeight,
        footer && footer.bottom > 0 ? footer.top : window.innerHeight,
      ) - 8;
    const field = control.getBoundingClientRect();
    const row = control.closest("tr")?.getBoundingClientRect();
    // Keep a normal row's product details visible too; oversized rows prioritize the input.
    const target = row && row.height <= bottom - top ? row : field;
    if (target.top < top) viewport.scrollTop += target.top - top;
    else if (target.bottom > bottom)
      viewport.scrollTop += target.bottom - bottom;
    // A short table cannot scroll further. On narrow screens, move the page as
    // well so a sticky save bar cannot cover the focused first/last input.
    const visible = control.getBoundingClientRect();
    const page = root.value?.closest<HTMLElement>(".main-shell");
    const pageBounds = page?.getBoundingClientRect();
    const pageTop = Math.max(0, chrome?.bottom || 0, pageBounds?.top || 0) + 8;
    const pageBottom =
      Math.min(
        window.innerHeight,
        pageBounds?.bottom ?? window.innerHeight,
        footer && footer.bottom > 0 ? footer.top : window.innerHeight,
      ) - 8;
    // The application scrolls inside main-shell. Scrolling window alone does
    // nothing here and can leave an input underneath the sticky save buttons.
    const scrollPage = (top: number) => {
      if (page) page.scrollBy({ top, behavior: "instant" });
      else window.scrollBy({ top, behavior: "instant" });
    };
    if (visible.bottom > pageBottom) scrollPage(visible.bottom - pageBottom);
    else if (visible.top < pageTop) scrollPage(visible.top - pageTop);
  });
}
onMounted(() => {
  observer = new ResizeObserver((entries) => {
    const resized = entries.find((entry) => entry.target === scroll.value);
    if (resized) available.value = resized.contentRect.width;
    keepActiveVisible();
  });
  if (scroll.value) observer.observe(scroll.value);
  const table = scroll.value?.querySelector("table");
  if (table) observer.observe(table);
});
onUnmounted(() => {
  observer?.disconnect();
  cancelAnimationFrame(visibilityFrame);
});
const widths = computed(() => entryColumnWidths(shown.value, available.value));
const tableWidth = computed(() => widths.value.reduce((s, w) => s + w, 124));
function hiddenDetails(row: EntryLine) {
  const parts: string[] = [];
  if (!shown.value.some((c) => c.key === "pack") && row.packaging_id)
    parts.push("使用包装单位（展开单位列核对）");
  if (!shown.value.some((c) => c.key === "tax") && row.tax_rate !== "0.13")
    parts.push(`税率 ${row.tax_rate}（小数）`);
  return parts.join(" · ");
}
const filled = computed(() => model.value.filter((r) => !blankEntryLine(r)));
const remaining = computed(() => 50 - filled.value.length);
const amount = computed(() =>
  centsText(filled.value.reduce((sum, r) => sum + (lineCents(r) ?? 0n), 0n)),
);
const incomplete = computed(() =>
  filled.value.some((r) => !!rowIssue(r) || pending.value[r.client_key]),
);
function duplicate(index: number) {
  const row = model.value[index];
  if (!row?.sku_id) return "";
  const other = model.value.flatMap((r, i) =>
    i !== index && r.sku_id === row.sku_id ? [i + 1] : [],
  );
  return other.length
    ? `与第 ${other.slice(0, 3).join("、")}${other.length > 3 ? ` 等 ${other.length}` : ""} 行同商品；请核对单位和价格`
    : "";
}
function rowNotes(index: number) {
  const row = model.value[index];
  if (!row) return "";
  return [
    quantityIssue(row),
    row.product && !row.product.eligible ? "商品当前不可选，请核对资质" : "",
    duplicate(index),
    changed.value[row.client_key]
      ? "商品已更换，请重新填写数量、单价并核对单位。"
      : "",
    changedUnits.value[row.client_key]
      ? "交易单位已切换，原数量和单价已保留，请按新单位核对。"
      : "",
    hiddenDetails(row),
  ]
    .filter(Boolean)
    .join("；");
}
const currentIndex = computed(() =>
  Math.max(
    0,
    model.value.findIndex((r) => r.client_key === activeKey.value),
  ),
);
const currentDetail = computed(() => {
  const row = model.value[currentIndex.value];
  if (!row?.product) return "选择商品后，在这里核对规格、厂家与行提示。";
  return [
    `第 ${currentIndex.value + 1} 行`,
    row.product.name,
    row.product.specification,
    row.product.manufacturer_name,
  ]
    .filter(Boolean)
    .join(" · ");
});
async function focusCell(key: string, column: string) {
  await nextTick();
  if (props.active === false || props.disabled) return false;
  const cell = root.value?.querySelector<HTMLElement>(
    `[data-row-key="${key}"] [data-col="${column}"]`,
  );
  const control = cell?.querySelector<HTMLInputElement | HTMLSelectElement>(
    "input:not(:disabled),select:not(:disabled)",
  );
  if (!control) return false;
  control.focus({ preventScroll: true });
  await nextTick();
  if (control instanceof HTMLInputElement && document.activeElement === control)
    control.select();
  keepActiveVisible();
  return true;
}
function track(event: FocusEvent) {
  const target = event.target as HTMLElement;
  const row = target.closest<HTMLElement>("[data-row-key]");
  if (row) {
    activeKey.value = row.dataset.rowKey || "";
    activeColumn.value =
      target.closest<HTMLElement>("[data-col]")?.dataset.col || "product";
    keepActiveVisible();
  }
}
function pick(row: EntryLine, product: EntryProduct) {
  if (row.sku_id && row.sku_id !== product.id) {
    row.packaging_id = "";
    row.qty = "";
    row.unit_price = "";
    changed.value[row.client_key] = true;
    delete changedUnits.value[row.client_key];
  }
  row.sku_id = product.id;
  const { barcode_match, ...catalogProduct } = product;
  row.product = catalogProduct;
  if (barcode_match) {
    changeUnit(row, barcode_match.packaging_id || "");
    // A barcode may name a package approved since this editor was opened.
    // Remount the selector so its SKU watcher cannot clear the identified unit.
    packages.delete(product.id);
    delete packageChecks.value[product.id];
    packageRevision.value[product.id] =
      (packageRevision.value[product.id] || 0) + 1;
  }
  pending.value[row.client_key] = false;
  error.value = "";
  void navigate(model.value.indexOf(row), "product", 1);
}
function changeUnit(row: EntryLine, value: string) {
  if (row.packaging_id !== value && (row.qty.trim() || row.unit_price.trim()))
    changedUnits.value[row.client_key] = true;
  row.packaging_id = value;
}
function adoptPrice(key: string, value: string) {
  const row = model.value.find((r) => r.client_key === key);
  if (
    !row ||
    row.unit_price.trim() ||
    pending.value[key] ||
    row.search_text ||
    props.disabled ||
    props.active === false
  )
    return;
  row.unit_price = value;
  void focusCell(key, "price");
}
function add(after = model.value.length - 1) {
  if (props.disabled) return;
  const existing = model.value[after + 1];
  if (
    existing &&
    blankEntryLine(existing) &&
    !pending.value[existing.client_key]
  ) {
    void focusCell(existing.client_key, "product");
    return;
  }
  const last = model.value.at(-1);
  if (
    after === model.value.length - 1 &&
    last &&
    blankEntryLine(last) &&
    !pending.value[last.client_key]
  ) {
    void focusCell(last.client_key, "product");
    return;
  }
  if (model.value.length >= 50) {
    error.value = "每张单据最多50行，请先核对已有行";
    return;
  }
  const row = newEntryLine();
  model.value.splice(after + 1, 0, row);
  void focusCell(row.client_key, "product");
}
function remove(index: number) {
  if (props.disabled) return;
  const row = model.value[index];
  if (!row) return;
  removed.value.push({ line: { ...row }, index });
  if (removed.value.length > 10) removed.value.shift();
  delete pending.value[row.client_key];
  model.value.splice(index, 1);
  if (!model.value.length) model.value.push(newEntryLine());
  void focusCell(
    model.value[Math.min(index, model.value.length - 1)]!.client_key,
    "product",
  );
}
function copy(index: number) {
  if (props.disabled) return;
  const source = model.value[index];
  if (!source) return;
  if (
    !source.sku_id ||
    source.search_text?.trim() ||
    pending.value[source.client_key]
  ) {
    complain(
      index,
      "product",
      "请先确认商品，再复制当前行；按 Esc 可恢复原商品",
    );
    return;
  }
  if (source.product && !source.product.eligible) {
    complain(index, "product", "商品当前不可选，请重新核对后再复制");
    return;
  }
  if (model.value.length >= 50) {
    error.value = "每张单据最多50行，请先核对已有行";
    return;
  }
  // Only copy editable business values; draft row identity must always be new.
  const row: EntryLine = {
    ...newEntryLine(),
    sku_id: source.sku_id,
    packaging_id: source.packaging_id,
    qty: source.qty,
    unit_price: source.unit_price,
    tax_rate: source.tax_rate,
    product: source.product ? { ...source.product } : undefined,
  };
  copyUndo.value = {
    key: row.client_key,
    sourceKey: source.client_key,
    signature: entryLinesSignature([row]),
  };
  if (changed.value[source.client_key]) changed.value[row.client_key] = true;
  model.value.splice(index + 1, 0, row);
  error.value = "";
  void focusCell(row.client_key, "qty");
}
function undoCopy() {
  if (props.disabled || !canUndoCopy.value || !copyUndo.value) return;
  const { key, sourceKey } = copyUndo.value;
  const index = model.value.findIndex((r) => r.client_key === key);
  model.value.splice(index, 1);
  delete pending.value[key];
  delete changed.value[key];
  copyUndo.value = undefined;
  error.value = "";
  if (!model.value.length) model.value.push(newEntryLine());
  const target =
    model.value.find((r) => r.client_key === sourceKey) ||
    model.value[Math.min(index, model.value.length - 1)]!;
  void focusCell(target.client_key, target.sku_id ? "qty" : "product");
}
function undo() {
  if (props.disabled || model.value.length >= 50) return;
  const item = removed.value.pop();
  if (!item) return;
  if (
    model.value.length === 1 &&
    blankEntryLine(model.value[0]!) &&
    !pending.value[model.value[0]!.client_key]
  )
    model.value.splice(0, 1);
  model.value.splice(Math.min(item.index, model.value.length), 0, item.line);
  void focusCell(item.line.client_key, "product");
}
function complain(index: number, column: string, message: string) {
  error.value = `第 ${index + 1} 行：${message}`;
  issueAnchor.value = { key: model.value[index]!.client_key, message };
  if (!shown.value.some((c) => c.key === column)) revealed.value.push(column);
  void focusCell(model.value[index]!.client_key, column);
}
function prepare(): EntryLine[] | null {
  error.value = "";
  for (const [i, row] of model.value.entries()) {
    if (pending.value[row.client_key]) {
      complain(i, "product", "检索文字尚未确认，请选择候选商品或按 Esc 恢复");
      return null;
    }
    if (blankEntryLine(row)) continue;
    const issue = rowIssue(row);
    if (issue) {
      complain(i, issue.column, issue.message);
      return null;
    }
  }
  if (!filled.value.length) {
    complain(0, "product", "至少填写一行商品");
    return null;
  }
  return filled.value.map((r) => ({
    ...r,
    qty: r.qty.trim(),
    unit_price: r.unit_price.trim(),
    tax_rate: r.tax_rate.trim(),
  }));
}
async function keys(event: KeyboardEvent) {
  if (
    props.disabled ||
    event.isComposing ||
    event.keyCode === 229 ||
    event.defaultPrevented
  )
    return;
  const target = event.target as HTMLElement;
  if (target.closest(".el-dialog") !== root.value?.closest(".el-dialog"))
    return;
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    if (event.shiftKey) return; // The workspace owns Save and New.
    event.preventDefault();
    root.value?.closest("form")?.requestSubmit();
    return;
  }
  const element = target.closest<HTMLElement>("[data-row-key]"),
    column = target.closest<HTMLElement>("[data-col]")?.dataset.col;
  const index = model.value.findIndex(
    (r) => r.client_key === element?.dataset.rowKey,
  );
  if (
    index < 0 ||
    !column ||
    !shown.value.some((c) => c.key === column && c.editable)
  )
    return;
  if (event.altKey && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
    event.preventDefault();
    const next = model.value[index + (event.key === "ArrowDown" ? 1 : -1)];
    if (next) await focusCell(next.client_key, column);
    return;
  }
  if (event.altKey && event.key === "Insert") {
    event.preventDefault();
    if (event.shiftKey && !event.ctrlKey && !event.metaKey) copy(index);
    else add(index);
    return;
  }
  if (event.altKey && event.key === "Delete") {
    event.preventDefault();
    remove(index);
    return;
  }
  if (
    event.ctrlKey ||
    event.metaKey ||
    event.altKey ||
    !["Enter", "Tab"].includes(event.key)
  )
    return;
  const direction = event.shiftKey ? -1 : 1;
  const first = editable.value[0];
  if (direction < 0 && index === 0 && column === first) {
    if (event.key === "Enter") event.preventDefault();
    return;
  }
  event.preventDefault();
  if (
    direction > 0 &&
    target instanceof HTMLInputElement &&
    !target.checkValidity()
  ) {
    target.reportValidity();
    return;
  }
  await navigate(index, column, direction);
}
async function navigate(index: number, column: string, direction: number) {
  const row = model.value[index];
  if (!row) return;
  const position = shown.value.findIndex((c) => c.key === column);
  const candidates = shown.value.filter(
    (c, i) =>
      c.editable && c.tab_stop && (direction > 0 ? i > position : i < position),
  );
  if (direction < 0) candidates.reverse();
  const issue = rowIssue(row);
  if (
    direction > 0 &&
    issue &&
    (issue.column === column || !candidates.length)
  ) {
    complain(index, issue.column, issue.message);
    return;
  }
  for (const c of candidates)
    if (await focusCell(row.client_key, c.key)) return;
  // Unavailable/disabled stops must not trap the user at a row boundary.
  if (direction > 0 && issue) {
    complain(index, issue.column, issue.message);
    return;
  }
  const next = model.value[index + direction];
  if (next) {
    const stops =
      direction > 0 ? editable.value : [...editable.value].reverse();
    for (const key of stops) if (await focusCell(next.client_key, key)) return;
  } else if (direction > 0) {
    add(index);
    const added = model.value[index + 1];
    if (added)
      await focusCell(added.client_key, editable.value[0] || "product");
  }
}

function pasted(rows: EntryLine[]) {
  if (props.disabled || rows.length > remaining.value) return;
  if (Object.values(pending.value).some(Boolean)) {
    error.value = "请先确认正在检索的商品，再填入粘贴数据";
    return;
  }
  model.value = [...filled.value, ...rows];
  error.value = "";
  pasting.value = false;
  void focusCell(rows[0]!.client_key, "qty");
}
function imported(rows: EntryLine[], mode: EntryImportMode) {
  if (
    props.disabled ||
    !rows.length ||
    rows.length > (mode === "append" ? remaining.value : 50)
  )
    return;
  if (mode === "append" && Object.values(pending.value).some(Boolean)) {
    error.value = "请先确认正在检索的商品，再追加导入";
    importing.value = false;
    return;
  }
  const before = model.value.map((r) => ({
    ...r,
    product: r.product ? { ...r.product } : undefined,
  }));
  const after = mode === "append" ? [...filled.value, ...rows] : rows;
  importUndo.value = { before, after: entryLinesSignature(after) };
  resetPackages();
  model.value = after;
  pending.value = {};
  removed.value = [];
  copyUndo.value = undefined;
  changed.value = {};
  importing.value = false;
  error.value = "";
  void focusCell(rows[0]!.client_key, "qty");
}
function undoImport() {
  if (props.disabled || !canUndoImport.value || !importUndo.value) return;
  model.value = importUndo.value.before;
  importUndo.value = undefined;
  pending.value = {};
  removed.value = [];
  copyUndo.value = undefined;
  changed.value = {};
  error.value = "";
  if (model.value[0]) void focusCell(model.value[0].client_key, "product");
}
function focusFirstProduct() {
  if (model.value[0]) void focusCell(model.value[0].client_key, "product");
}
async function resumePosition(
  position: Extract<EntryResume, { area: "line" }>,
) {
  const row =
    model.value.find((r) => r.client_key === position.key) ||
    model.value[Math.min(position.index, model.value.length - 1)];
  if (!row) return false;
  // A template may have hidden the old column, or packaging may still be loading.
  // Restore the row without revealing hidden fields or waiting to steal focus later.
  for (const column of new Set([position.column, ...editable.value]))
    if (await focusCell(row.client_key, column)) return true;
  return false;
}
const ready = computed(() => template.value.ready || !!template.value.error);
const hydrating = computed(() => hydrationCount.value > 0);
defineExpose({ prepare, focusFirstProduct, resumePosition, ready, hydrating });
</script>
<template>
  <section
    ref="root"
    class="document-entry"
    :data-density="appearance.density"
    :data-channel="channel"
    :data-entity="entity"
    aria-label="商品明细录入"
    @keydown="keys"
    @focusin="track"
    @input="error = ''"
  >
    <div class="entry-toolbar">
      <div class="entry-toolbar-title">
        <b
          >商品明细 <span>{{ filled.length }} / 50 行</span></b
        >
        <select
          v-model="appearance.density"
          aria-label="录入密度"
          class="entry-density"
          title="录入密度，本浏览器记住选择"
        >
          <option value="compact">紧凑</option>
          <option value="comfortable">舒适</option>
        </select>
      </div>
      <div>
        <el-button
          aria-label="添加商品行"
          :disabled="disabled || model.length >= 50"
          @click="add()"
          >＋ 添加行</el-button
        ><el-button
          aria-label="在当前行后插入"
          :disabled="disabled || !activeKey || model.length >= 50"
          @click="add(model.findIndex((r) => r.client_key === activeKey))"
          >插入行</el-button
        ><el-button
          aria-label="复制当前商品行"
          title="复制当前行，Alt + Shift + Insert"
          :disabled="
            disabled || !model[currentIndex]?.sku_id || model.length >= 50
          "
          @click="copy(currentIndex)"
          >复制行</el-button
        ><el-button
          :disabled="disabled || !removed.length || model.length >= 50"
          @click="undo"
          >撤销删除</el-button
        ><el-button
          :disabled="disabled || remaining <= 0"
          @click="pasting = true"
          >粘贴多行</el-button
        ><el-button
          aria-label="导入文件"
          :disabled="disabled"
          @click="importing = true"
          >导入</el-button
        ><el-button
          aria-label="导出明细"
          :disabled="disabled || !filled.length"
          @click="exporting = true"
          >导出</el-button
        ><el-button
          v-if="importUndo"
          :disabled="disabled || !canUndoImport"
          :title="
            canUndoImport
              ? '恢复导入前明细'
              : '导入后已继续编辑，不能覆盖当前内容'
          "
          @click="undoImport"
          >撤销导入</el-button
        ><el-button
          aria-label="列设置"
          :disabled="disabled || !template.ready || template.loading"
          @click="configuring = true"
          >字段设置</el-button
        >
      </div>
    </div>
    <div class="entry-hint entry-key-hint">
      <span>Enter / Tab 跳格 · 行末新增 · Shift 反向 · Alt + ↑↓ 同列换行</span>
      <el-button
        v-if="copyUndo"
        link
        :disabled="disabled || !canUndoCopy"
        :title="
          canUndoCopy
            ? '移除最近复制且未修改的新行'
            : '复制行已修改或移除，保留当前内容'
        "
        @click="undoCopy"
        >撤销复制</el-button
      >
      <details class="entry-key-help">
        <summary>全部快捷键</summary>
        <p>
          Enter / Tab 按列设置跳格，行末连续新增；Shift 反向 · Alt + ↑↓ 同列换行
          · Alt + Insert 插行 · Alt + Shift + Insert 复制行 · Alt + Delete 删行
          · Ctrl + Enter 保存单据 · Ctrl + S 保存草稿。中文输入选词时不跳格。
        </p>
      </details>
    </div>
    <p v-if="template.error" class="entry-error" role="alert">
      字段设置：{{ template.error }}
      <el-button link @click="reloadTemplate">重新读取</el-button>
    </p>
    <p v-if="revealed.length" class="entry-hint">
      为修正错误已临时展开字段，保存的列设置不变。
    </p>
    <p v-if="error" role="alert" class="entry-error">{{ error }}</p>
    <p v-if="failedPackages.length" role="alert" class="entry-error">
      部分包装资料读取失败，暂不能校验对应行的数量。
      <el-button link :disabled="disabled" @click="retryPackages"
        >重新校验包装</el-button
      >
    </p>
    <div ref="scroll" class="entry-scroll">
      <table
        class="entry-table"
        aria-label="商品明细表"
        :style="{ width: tableWidth + 'px' }"
      >
        <colgroup>
          <col style="width: 44px" />
          <col
            v-for="(c, i) in shown"
            :key="c.key"
            :style="{ width: widths[i] + 'px' }"
          />
          <col style="width: 80px" />
        </colgroup>
        <thead>
          <tr>
            <th scope="col">行</th>
            <th v-for="c in shown" :key="c.key" scope="col">
              {{ columnLabel(c) }}
            </th>
            <th scope="col">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(row, index) in model"
            :key="row.client_key"
            :data-row-key="row.client_key"
            class="line-editor entry-row"
            :class="{ 'entry-active': activeKey === row.client_key }"
          >
            <th scope="row">{{ index + 1 }}</th>
            <td
              v-for="c in shown"
              :key="c.key"
              :data-col="c.key"
              :class="{ 'entry-wrap': c.wrap, 'entry-truncate': !c.wrap }"
            >
              <template v-if="c.key === 'product'"
                ><div class="entry-product-cell">
                  <EntryProductPicker
                    :model-value="row.sku_id"
                    :product="row.product"
                    :entity="entity"
                    :channel="channel"
                    :hydrate="hydrateProduct"
                    :disabled="disabled"
                    @select="pick(row, $event)"
                    @loaded="row.product = $event"
                    :draft-text="row.search_text"
                    @draft-text="row.search_text = $event"
                    @pending="pending[row.client_key] = $event"
                  /><button
                    v-if="rowNotes(index)"
                    type="button"
                    tabindex="-1"
                    class="entry-note-badge"
                    :aria-label="
                      '第 ' + (index + 1) + ' 行提示：' + rowNotes(index)
                    "
                    :title="rowNotes(index)"
                    @click="
                      quantityIssue(row)
                        ? complain(index, 'qty', quantityIssue(row))
                        : focusCell(row.client_key, 'product')
                    "
                  >
                    !
                  </button>
                </div>
                <div class="entry-line-notes">
                  <small v-if="duplicate(index)" class="entry-warning">{{
                    duplicate(index)
                  }}</small
                  ><small v-if="changed[row.client_key]" class="entry-warning"
                    >商品已更换，请重新填写数量、单价并核对单位。</small
                  ><small v-if="hiddenDetails(row)" class="entry-warning">{{
                    hiddenDetails(row)
                  }}</small>
                </div></template
              >
              <PackagingSelect
                v-else-if="c.key === 'pack'"
                :key="row.sku_id + ':' + (packageRevision[row.sku_id] || 0)"
                :model-value="row.packaging_id"
                @update:model-value="changeUnit(row, $event)"
                :sku="row.sku_id"
                :disabled="disabled"
                :cache="packages"
                compact
                :tabindex="c.tab_stop ? 0 : -1"
                :label="'交易单位 ' + (index + 1)"
                @failure="emit('failure', $event)"
              />
              <EntryNumberInput
                v-else-if="c.key === 'qty'"
                v-model="row.qty"
                :column="c"
                :label="channel === 'purchase' ? '采购数量' : '销售数量'"
                :aria-invalid="quantityIssue(row) ? 'true' : undefined"
                :title="quantityIssue(row) || '原值：' + row.qty"
                :disabled="disabled"
              />
              <EntryNumberInput
                v-else-if="c.key === 'price'"
                v-model="row.unit_price"
                :column="c"
                label="含税单价（元）"
                :disabled="disabled"
              />
              <EntryTaxInput
                v-else-if="c.key === 'tax'"
                v-model="row.tax_rate"
                :tabindex="c.tab_stop ? 0 : -1"
                :percent="c.tax_display === 'percent'"
                :disabled="disabled"
              />
              <span v-else-if="c.key === 'amount'" class="entry-money">{{
                lineCents(row) === null
                  ? "—"
                  : formatEntryNumber(centsText(lineCents(row)!), c)
              }}</span>
              <span
                v-else
                class="entry-text"
                :title="
                  documentCell(
                    entryDocumentValues(row, packageChecks[row.sku_id]?.data),
                    c,
                  )
                "
                >{{
                  documentCell(
                    entryDocumentValues(row, packageChecks[row.sku_id]?.data),
                    c,
                  )
                }}</span
              >
            </td>
            <td>
              <el-button link :disabled="disabled" @click="remove(index)"
                >移除此行</el-button
              >
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="entry-summary">
      <div class="entry-current-info" aria-label="当前行信息">
        <span :title="currentDetail">{{ currentDetail }}</span>
        <EntryTradeHint
          :row="model[currentIndex]"
          :entity="entity"
          :channel="channel"
          :disabled="disabled"
          :active="active"
          :pending="pending[model[currentIndex]?.client_key || '']"
          :package-revision="
            packageRevision[model[currentIndex]?.sku_id || ''] || 0
          "
          @adopt="adoptPrice"
          @packages-loaded="acceptPackages"
        />
        <span
          v-if="rowNotes(currentIndex)"
          class="entry-warning"
          :title="rowNotes(currentIndex)"
          >{{ rowNotes(currentIndex) }}</span
        >
      </div>
      <strong
        >预估含税合计 ¥ {{ amount
        }}<small v-if="incomplete">（存在待补全行）</small></strong
      >
    </div>
    <DocumentColumnSettings
      v-if="configuring"
      :state="purchaseTemplate"
      @close="configuring = false"
    />
    <EntryFileImport
      v-if="importing"
      :entity="entity"
      :channel="channel"
      :remaining="remaining"
      :current-count="filled.length"
      :disabled="disabled"
      @close="importing = false"
      @apply="imported"
    />
    <EntryFileExport
      v-if="exporting"
      :entity="entity"
      :channel="channel"
      :lines="model"
      :columns="columns"
      :disabled="disabled"
      @close="exporting = false"
    />
    <EntryPaste
      v-if="pasting"
      :entity="entity"
      :channel="channel"
      :remaining="remaining"
      @close="pasting = false"
      @apply="pasted"
    />
  </section>
</template>
<style>
.document-entry-fields {
  min-width: 0;
  margin: 0;
  padding: 0;
  border: 0;
}
.document-entry {
  margin: 20px 0;
  border: 1px solid var(--ui-border);
  border-radius: 6px;
  overflow: hidden;
  background: white;
}
.entry-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  background: #fff;
  flex-wrap: wrap;
}
.entry-toolbar b span {
  font-weight: 400;
  color: var(--ui-muted);
  font-size: 12px;
  margin-left: 8px;
}
.entry-toolbar > div {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  align-items: center;
}
.entry-toolbar b {
  font-size: 14px;
}
.entry-toolbar .el-button {
  height: 28px;
  padding: 5px 9px;
  font-size: 13px;
}
.entry-toolbar .entry-density {
  width: 68px;
  height: 28px;
  padding: 3px 6px;
  background: var(--ui-tint);
  border-color: var(--ui-border);
  font-size: 12px;
  cursor: pointer;
}
.entry-toolbar .el-button + .el-button {
  margin: 0;
}
.entry-hint {
  font-size: 12px;
  color: var(--ui-muted);
  padding: 4px 10px;
  margin: 0;
  line-height: 1.6;
}
.entry-key-hint {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  border-top: 1px solid var(--ui-border);
}
.entry-key-help {
  position: relative;
  flex: none;
}
.entry-key-help summary {
  cursor: pointer;
  color: var(--ui-accent);
}
.entry-key-help p {
  position: absolute;
  right: 0;
  z-index: 6;
  width: min(320px, 75vw);
  margin: 4px 0;
  padding: 12px;
  border: 1px solid var(--ui-border);
  border-radius: 5px;
  background: white;
  box-shadow: 0 4px 16px #22344a20;
}
.entry-error {
  color: #ac362c;
  background: #fff4ed;
  padding: 12px;
  margin: 0;
}
.entry-scroll {
  overflow: auto;
  max-height: 46vh;
  min-height: 150px;
  scroll-padding: 44px 8px 8px;
}
.entry-table {
  border-collapse: separate;
  border-spacing: 0;
  width: 100%;
  table-layout: fixed;
}
.entry-table thead th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--ui-soft);
  color: var(--ui-muted);
  text-align: left;
  padding: 8px;
  font-size: 13px;
  font-weight: 500;
}
.entry-table tbody tr.entry-row {
  display: table-row;
  padding: 0;
  margin: 0;
  border: 0;
  background: white;
}
.entry-table tbody tr.entry-active {
  background: var(--ui-soft);
}
.entry-table td,
.entry-table tbody th {
  vertical-align: top;
  padding: 9px 7px;
  border-bottom: 1px solid var(--ui-border);
  font-size: 13px;
}
.entry-table tbody th {
  padding-top: 20px;
  color: var(--ui-muted);
}
.entry-table input,
.entry-table select {
  box-sizing: border-box;
  width: 100%;
  min-width: 0;
  height: 36px;
  border: 1px solid var(--ui-border);
  border-radius: 5px;
  padding: 7px 8px;
  background: white;
  color: var(--ui-text);
  font-size: 13px;
}
.entry-table input:focus,
.entry-table select:focus {
  outline: 2px solid var(--ui-accent);
  outline-offset: 0;
}
.entry-table [data-col="qty"] input,
.entry-table [data-col="price"] input,
.entry-table [data-col="tax"] input {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.entry-table label {
  margin: 0 !important;
}
.entry-warning {
  display: block;
  color: #946521;
  line-height: 1.5;
  margin-top: 4px;
}
.entry-money {
  display: block;
  text-align: right;
  padding-top: 9px;
  font-variant-numeric: tabular-nums;
}
.entry-summary {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 7px 10px;
  gap: 8px;
  background: var(--ui-tint);
  border-top: 1px solid var(--ui-border);
}
.entry-summary > span,
.entry-summary small {
  color: var(--ui-muted);
  font-size: 12px;
}
.entry-text {
  display: block;
  padding-top: 9px;
}
.entry-truncate .entry-text {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.entry-wrap .entry-text {
  white-space: normal;
  overflow-wrap: anywhere;
}
.entry-truncate .entry-picker small {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.entry-summary > strong {
  flex: none;
  font-size: 16px;
  color: var(--ui-accent);
  font-variant-numeric: tabular-nums;
}
.entry-current-info {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  color: var(--ui-muted);
}
.entry-current-info > span {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  line-height: 20px;
}
.entry-current-info .entry-warning {
  margin-top: 0;
}
.entry-product-cell {
  display: flex;
  align-items: center;
  gap: 3px;
}
.entry-product-cell .entry-picker {
  flex: 1;
  min-width: 0;
}
.entry-note-badge {
  display: none;
}
.document-entry[data-density="compact"] .entry-product-meta,
.document-entry[data-density="compact"] .entry-line-notes {
  display: none;
}
.document-entry[data-density="compact"] .entry-note-badge {
  display: inline-grid;
  place-items: center;
  flex: none;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: 1px solid #d2a85f;
  background: #fff6df;
  color: #885714;
  padding: 0;
  font-size: 12px;
  font-weight: 600;
}
.document-entry[data-density="compact"] .entry-table td,
.document-entry[data-density="compact"] .entry-table tbody th {
  height: 40px;
  padding: 3px 7px;
  vertical-align: middle;
}
.document-entry[data-density="compact"] .entry-table input,
.document-entry[data-density="compact"] .entry-table select {
  height: 32px;
  padding: 4px 5px;
  background: transparent;
  border-color: transparent;
  border-radius: 4px;
}
.document-entry[data-density="compact"] .entry-table input:hover,
.document-entry[data-density="compact"] .entry-table select:hover {
  background: white;
  border-color: var(--ui-accent-line);
}
.document-entry[data-density="compact"] .entry-table input:focus,
.document-entry[data-density="compact"] .entry-table select:focus {
  outline: 2px solid var(--ui-accent);
  outline-offset: -1px;
  background: white;
}
.document-entry[data-density="compact"] .entry-tax-input input {
  padding-right: 25px;
}
.document-entry[data-density="compact"] .entry-tax-input button {
  background: transparent;
}
.document-entry[data-density="compact"] .entry-money,
.document-entry[data-density="compact"] .entry-text {
  padding-top: 0;
}
.document-entry .entry-table input[aria-invalid="true"] {
  border-color: #c94141;
  background: #fff6f5;
}
@media (max-width: 760px) {
  .entry-summary {
    flex-wrap: wrap;
  }
  .entry-current-info {
    flex-basis: 100%;
  }
  .entry-summary > strong {
    margin-left: auto;
  }
}
</style>
