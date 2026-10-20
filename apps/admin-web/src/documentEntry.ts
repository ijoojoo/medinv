export type EntryChannel = "purchase" | "sale";
export interface EntryProduct {
  document_values?: Record<string, unknown>;
  document_status?: Record<string, string>;
  id: string;
  code: string;
  name: string;
  base_unit: string;
  qty_scale: number;
  specification: string;
  manufacturer_name: string;
  eligible: boolean;
  barcode_match?: {
    barcode: string;
    packaging_id: string | null;
    unit_name: string;
    numerator: string;
    denominator: string;
    available: boolean;
  } | null;
}
export interface EntryLine {
  client_key: string;
  sku_id: string;
  packaging_id: string;
  qty: string;
  unit_price: string;
  tax_rate: string;
  product?: EntryProduct;
  search_text?: string;
}
export interface EntryPackages {
  base_unit: string;
  items: {
    id: string;
    unit_name: string;
    numerator: string;
    denominator: string;
  }[];
}
export const newEntryLine = (): EntryLine => ({
  client_key: crypto.randomUUID(),
  sku_id: "",
  packaging_id: "",
  qty: "",
  unit_price: "",
  tax_rate: "0.13",
});
export const blankEntryLine = (r: EntryLine) =>
  !r.sku_id &&
  !r.search_text &&
  !r.packaging_id &&
  !r.qty.trim() &&
  !r.unit_price.trim();
export function decimalMicros(raw: string): bigint | null {
  if (!/^(0|[1-9][0-9]{0,13})(\.[0-9]{1,6})?$/.test(raw)) return null;
  const [whole, fraction = ""] = raw.split(".");
  return BigInt(whole!) * 1000000n + BigInt(fraction.padEnd(6, "0"));
}
// Quantities must be representable at the SKU's base-unit precision. Unlike
// reference prices, packaging quantities must never be rounded.
export function entryQuantity(
  raw: string,
  scale = 6,
  baseUnit = "基本单位",
  pack?: EntryPackages["items"][number],
): { micros: bigint; message?: never } | { message: string; micros?: never } {
  const qty = decimalMicros(raw.trim());
  if (qty === null || qty <= 0n)
    return { message: "数量须大于零，最多6位小数，不使用千分位或科学计数法" };
  if (!Number.isInteger(scale) || scale < 0 || scale > 6)
    return { message: "商品数量精度信息异常，请重新读取商品资料" };
  if (
    pack &&
    ![pack.numerator, pack.denominator].every((v) =>
      /^[1-9][0-9]{0,8}$/.test(v),
    )
  )
    return { message: "包装换算关系异常，请重新读取包装资料" };
  const numerator = qty * BigInt(pack?.numerator || "1");
  const denominator = BigInt(pack?.denominator || "1");
  if (numerator > 99999999999999999999n * denominator)
    return {
      message: "换算后的基本数量超出上限（整数部分最多14位），请减少交易数量",
    };
  if (numerator % (denominator * 10n ** BigInt(6 - scale)) !== 0n)
    return {
      message: `${pack ? "按此包装换算后的基本数量" : "基本单位数量"}${scale === 0 ? "必须为整数" : `最多保留 ${scale} 位小数`}（${baseUnit}）；请调整数量，不会自动四舍五入`,
    };
  return { micros: numerator / denominator };
}
export function lineIssue(
  r: EntryLine,
  packages?: EntryPackages,
): { column: string; message: string } | null {
  if (!r.sku_id || r.search_text)
    return { column: "product", message: "请选择商品，不能只填写检索文字" };
  if (r.product && !r.product.eligible)
    return {
      column: "product",
      message: "商品当前不可选，请核对资质或重新选择",
    };
  const pack = packages?.items.find((p) => p.id === r.packaging_id);
  if (r.packaging_id && packages && !pack)
    return {
      column: "pack",
      message: "原包装已不可用，请重新选择交易单位并核对数量与单价",
    };
  const qty = entryQuantity(
    r.qty,
    !r.packaging_id || pack ? r.product?.qty_scale : 6,
    r.product?.base_unit,
    pack,
  );
  if (qty.message) return { column: "qty", message: qty.message };
  if (decimalMicros(r.unit_price.trim()) === null)
    return { column: "price", message: "含税单价须为非负数字，最多6位小数" };
  const tax = decimalMicros(r.tax_rate.trim());
  if (tax === null || tax > 1000000n)
    return { column: "tax", message: "税率须为0到1的小数，如13%填写0.13" };
  return null;
}
export function lineCents(r: EntryLine): bigint | null {
  const qty = decimalMicros(r.qty.trim()),
    price = decimalMicros(r.unit_price.trim());
  return qty === null || qty <= 0n || price === null
    ? null
    : (qty * price + 5000000000n) / 10000000000n;
}
export function centsText(n: bigint) {
  return `${n / 100n}.${String(n % 100n).padStart(2, "0")}`;
}
export interface EntryColumn {
  key: string;
  label: string;
  width: number;
  width_mode: "px" | "weight";
  required?: boolean;
  editable?: boolean;
  visible: boolean;
  tab_stop: boolean;
  decimals: number;
  grouping: boolean;
  wrap: boolean;
  tax_display: "decimal" | "percent";
}
export const defaultEntryColumns = (): EntryColumn[] =>
  [
    {
      key: "product",
      label: "商品",
      width: 270,
      required: true,
      editable: true,
      visible: true,
    },
    { key: "specification", label: "规格型号", width: 150, visible: false },
    { key: "manufacturer_name", label: "生产企业", width: 180, visible: false },
    {
      key: "pack",
      label: "交易单位",
      width: 180,
      editable: true,
      visible: true,
    },
    {
      key: "qty",
      label: "数量",
      width: 110,
      required: true,
      editable: true,
      visible: true,
    },
    {
      key: "price",
      label: "含税单价",
      width: 130,
      required: true,
      editable: true,
      visible: true,
    },
    {
      key: "tax",
      label: "税率（小数）",
      width: 110,
      editable: true,
      visible: true,
    },
    { key: "amount", label: "含税金额", width: 120, visible: true },
  ].map((c) => ({
    ...c,
    width_mode: "px",
    tab_stop: ["product", "qty", "price"].includes(c.key),
    decimals: -1,
    grouping: false,
    wrap: false,
    tax_display: "decimal",
  }));
export function normalizeEntryColumns(stored: unknown): EntryColumn[] {
  const defaults = defaultEntryColumns();
  if (!Array.isArray(stored)) return defaults;
  const used = new Set<string>(),
    result: EntryColumn[] = [];
  for (const item of stored) {
    if (!item || typeof item !== "object" || used.has(item.key)) continue;
    const base = defaults.find((c) => c.key === item.key);
    if (!base) continue;
    used.add(base.key);
    const mode = item.width_mode === "weight" ? "weight" : "px";
    result.push({
      ...base,
      label:
        typeof item.label === "string" && item.label.trim()
          ? item.label.trim().slice(0, 30)
          : base.label,
      visible:
        !!base.required ||
        (typeof item.visible === "boolean" ? item.visible : base.visible),
      tab_stop:
        ["product", "qty"].includes(base.key) ||
        (!!base.editable &&
          (typeof item.tab_stop === "boolean" ? item.tab_stop : base.tab_stop)),
      width_mode: mode,
      width: Number.isInteger(item.width)
        ? Math.max(
            mode === "weight" ? 1 : base.key === "product" ? 220 : 90,
            Math.min(mode === "weight" ? 20 : 600, item.width),
          )
        : mode === "weight"
          ? 1
          : base.width,
      decimals:
        ["qty", "price", "amount"].includes(base.key) &&
        Number.isInteger(item.decimals)
          ? Math.max(-1, Math.min(6, item.decimals))
          : -1,
      grouping:
        ["qty", "price", "amount"].includes(base.key) && item.grouping === true,
      wrap:
        ["product", "specification", "manufacturer_name"].includes(base.key) &&
        item.wrap === true,
      tax_display:
        base.key === "tax" && item.tax_display === "percent"
          ? "percent"
          : "decimal",
    });
  }
  return [...result, ...defaults.filter((c) => !used.has(c.key))];
}
export function loadEntryColumns(key: string): EntryColumn[] {
  try {
    return normalizeEntryColumns(
      JSON.parse(localStorage.getItem(key) || "null"),
    );
  } catch {
    return defaultEntryColumns();
  }
}
export function columnLabel(c: EntryColumn) {
  if (["price", "amount"].includes(c.key) && !c.label.includes("含税"))
    return c.label + "（含税）";
  if (c.key === "tax")
    return (
      c.label.replace(/（小数）|（%）/g, "") +
      (c.tax_display === "percent" ? "（%）" : "（小数）")
    );
  return c.label;
}
// Formatting operates on integer decimal strings, never Number or the stored value.
export function formatEntryNumber(
  raw: string,
  c: Pick<EntryColumn, "decimals" | "grouping">,
) {
  if (!/^-?(0|[1-9][0-9]*)(\.[0-9]{1,6})?$/.test(raw.trim())) return raw;
  let sign = raw.trim().startsWith("-") ? "-" : "";
  const absolute = raw.trim().replace(/^-/, "");
  const [integer, fractionRaw = ""] = absolute.split(".");
  const n = BigInt(integer!) * 1000000n + BigInt(fractionRaw.padEnd(6, "0"));
  let result = absolute;
  if (c.decimals >= 0) {
    const factor = 10n ** BigInt(6 - c.decimals);
    const rounded = (n + factor / 2n) / factor;
    if (rounded === 0n) sign = "";
    const digits = String(rounded).padStart(c.decimals + 1, "0");
    result = c.decimals
      ? digits.slice(0, -c.decimals) + "." + digits.slice(-c.decimals)
      : digits;
  }
  const [whole, fraction] = result.split(".");
  return (
    sign +
    (c.grouping ? whole!.replace(/\B(?=(\d{3})+(?!\d))/g, ",") : whole) +
    (fraction === undefined ? "" : "." + fraction)
  );
}
export function entryColumnWidths(
  columns: EntryColumn[],
  available: number,
): number[] {
  const min = (c: EntryColumn) => (c.key === "product" ? 220 : 90);
  const fixed = columns.reduce(
    (s, c) => s + (c.width_mode === "px" ? c.width : min(c)),
    124,
  );
  const extra = Math.max(0, available - fixed),
    weights = columns.reduce(
      (s, c) => s + (c.width_mode === "weight" ? c.width : 0),
      0,
    );
  return columns.map((c) =>
    c.width_mode === "px" ? c.width : min(c) + (extra * c.width) / weights,
  );
}
export interface PasteLine {
  row: number;
  code: string;
  line: EntryLine;
  error: string;
}
export function parseEntryPaste(
  text: string,
  identifierLabel = "SKU编码",
): PasteLine[] {
  if (text.length > 30000) throw new Error("粘贴内容过长，最多50行");
  const rows = text
    .replace(/^\uFEFF/, "")
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .map((line, index) => ({
      cells: line.split("\t").map((s) => s.trim()),
      row: index + 1,
    }))
    .filter((r) => r.cells.some(Boolean));
  if (!rows.length || rows.length > 50) throw new Error("请粘贴1到50行数据");
  return rows.map(({ cells, row }) => {
    const line = {
      ...newEntryLine(),
      qty: cells[1] || "",
      unit_price: cells[2] || "",
      tax_rate: cells[3] || "0.13",
    };
    return {
      row,
      code: cells[0] || "",
      line,
      error:
        cells.length < 3 || cells.length > 4
          ? `需要3或4列：${identifierLabel}、数量、含税单价、税率（可选）`
          : "",
    };
  });
}
