import { parseDate } from "./dates";
export const OPENING_COLUMNS = [
  "SKU编码",
  "库位编码",
  "批号",
  "生产日期",
  "有效期至",
  "期初数量",
  "期初成本金额",
] as const;
export const MAX_IMPORT_BYTES = 512 * 1024;
export const MAX_IMPORT_ROWS = 200;
export interface ImportSku {
  id: string;
  code: string;
  name: string;
  base_unit: string;
  qty_scale: number;
}
export interface ImportLocation {
  id: string;
  code: string;
  warehouse_id: string;
}
export interface OpeningInput {
  sku_id: string;
  location_id: string;
  lot_no: string;
  mfg_date: string;
  expiry_date: string;
  qty: string;
  cost_amount: string;
}
export interface ImportRow {
  row: number;
  sku_code: string;
  location_code: string;
  name: string;
  base_unit: string;
  input: OpeningInput;
  errors: string[];
}
export interface ImportPreview {
  rows: ImportRow[];
  errorCount: number;
  costTotal: string;
}
export const openingTemplate = () =>
  "\uFEFF" + OPENING_COLUMNS.join(",") + "\r\n";

// Bounded RFC-style CSV parsing: preserve identifiers as text, including zeros,
// commas and escaped quotes. Row numbers refer to physical file lines.
function csvRecords(source: string): { row: number; cells: string[] }[] {
  const text = source.replace(/^\uFEFF/, "");
  const records: { row: number; cells: string[] }[] = [];
  let cells: string[] = [],
    field = "",
    quoted = false,
    closed = false;
  let line = 1,
    row = 1;
  const pushField = () => {
    cells.push(field.trim());
    field = "";
    closed = false;
  };
  const pushRow = () => {
    pushField();
    if (cells.some((cell) => cell !== "")) records.push({ row, cells });
    if (records.length > MAX_IMPORT_ROWS + 1)
      throw new Error("每个仓库最多导入 200 行，请先核对完整期初清单。");
    cells = [];
  };
  for (let i = 0; i < text.length; i++) {
    const char = text[i]!;
    if (quoted) {
      if (char === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          quoted = false;
          closed = true;
        }
      } else {
        field += char;
        if (char === "\n" || (char === "\r" && text[i + 1] !== "\n")) line++;
      }
    } else if (char === ",") pushField();
    else if (char === "\r" || char === "\n") {
      pushRow();
      if (char === "\r" && text[i + 1] === "\n") i++;
      row = ++line;
    } else if (char === '"' && !field && !closed) quoted = true;
    else if (char === '"' || (closed && char.trim()))
      throw new Error(
        `第 ${line} 行的引号格式不正确，请使用 CSV 模板重新保存。`,
      );
    else if (!closed) field += char;
    if (field.length > 200 || cells.length > OPENING_COLUMNS.length)
      throw new Error(`第 ${row} 行的列数或字段长度超出模板范围。`);
  }
  if (quoted) throw new Error(`第 ${row} 行的引号未闭合。`);
  if (field || cells.length || closed) pushRow();
  return records;
}
function validDate(value: string): boolean {
  const parsed = parseDate(value, { required: true });
  return !parsed.error && parsed.value === value;
}
function decimalParts(value: string, integerDigits: number, scale: number) {
  if (!/^(0|[1-9][0-9]*)(\.[0-9]+)?$/.test(value)) return null;
  const [whole, fraction = ""] = value.split(".");
  if (whole!.length > integerDigits || fraction.length > scale) return null;
  return { whole: whole!, fraction };
}
export function previewOpening(
  source: string,
  skus: ImportSku[],
  locations: ImportLocation[],
  warehouse: string,
  today: string,
): ImportPreview {
  if (new TextEncoder().encode(source).byteLength > MAX_IMPORT_BYTES)
    throw new Error("文件超过 512 KB，请使用最多 200 行的期初 CSV 清单。");
  if (!warehouse) throw new Error("请先选择启用仓库。");
  const records = csvRecords(source);
  const header = records.shift()?.cells || [];
  if (
    header.length !== OPENING_COLUMNS.length ||
    new Set(header).size !== header.length ||
    OPENING_COLUMNS.some((name) => !header.includes(name))
  )
    throw new Error("表头与模板不一致，请保留模板的全部七列，不增加其他列。");
  if (!records.length)
    throw new Error("文件只有表头或空行，请填写期初库存明细。");
  const indexes = OPENING_COLUMNS.map((name) => header.indexOf(name));
  const byCode = new Map(skus.map((sku) => [sku.code, sku]));
  const byLocation = new Map(
    locations
      .filter((l) => l.warehouse_id === warehouse)
      .map((l) => [l.code, l]),
  );
  const seen = new Map<string, ImportRow>();
  const lots = new Map<string, ImportRow>();
  let total = 0n;
  const rows = records.map(({ row, cells }): ImportRow => {
    const [skuCode, locationCode, lot, mfg, expiry, qty, cost] = indexes.map(
      (i) => cells[i] || "",
    ) as [string, string, string, string, string, string, string];
    const sku = byCode.get(skuCode),
      location = byLocation.get(locationCode);
    const errors: string[] = [];
    const input = {
      sku_id: sku?.id || "",
      location_id: location?.id || "",
      lot_no: lot,
      mfg_date: mfg,
      expiry_date: expiry,
      qty,
      cost_amount: cost,
    };
    const result = {
      row,
      sku_code: skuCode,
      location_code: locationCode,
      name: sku?.name || "",
      base_unit: sku?.base_unit || "",
      input,
      errors,
    };
    if (cells.length !== header.length) errors.push("列数与表头不一致");
    if (!sku) errors.push("SKU编码不存在或商品当前不可用于期初");
    if (!location) errors.push("库位编码不属于当前仓库");
    if (!lot || lot.length > 100 || /[\u0000-\u001f\u007f]/.test(lot))
      errors.push("批号必填，最多100字且不可包含换行或控制字符");
    const mfgValid = validDate(mfg),
      expiryValid = validDate(expiry) && expiry <= "9999-12-30";
    if (!mfgValid) errors.push("生产日期须为有效的 YYYY-MM-DD 日期");
    else if (mfg > today) errors.push("生产日期不能晚于今天");
    if (!expiryValid)
      errors.push("有效期须为有效的 YYYY-MM-DD 日期，最晚9999-12-30");
    else if (expiry < today) errors.push("已过期库存不能按合格期初导入");
    if (mfgValid && expiryValid && expiry < mfg)
      errors.push("有效期不能早于生产日期");
    const amount = decimalParts(qty, 14, 6);
    if (!amount || !/[1-9]/.test(qty))
      errors.push("数量须大于0，最多14位整数和6位小数，不支持科学计数法");
    else if (sku && amount.fraction.replace(/0+$/, "").length > sku.qty_scale)
      errors.push(`数量不符合商品精度（最多${sku.qty_scale}位小数）`);
    const money = decimalParts(cost, 16, 2);
    if (!money)
      errors.push("成本须为非负金额，最多16位整数和2位小数，不支持科学计数法");
    else {
      input.cost_amount = money.whole + "." + money.fraction.padEnd(2, "0");
      total +=
        BigInt(money.whole) * 100n + BigInt(money.fraction.padEnd(2, "0"));
    }
    if (sku && location && lot) {
      const key = JSON.stringify([sku.id, location.id, lot]);
      const prior = seen.get(key);
      if (prior) {
        errors.push(`与第${prior.row}行商品、批号、库位重复，请核对后合并`);
        prior.errors.push(`与第${row}行重复`);
      } else seen.set(key, result);
      const lotKey = JSON.stringify([sku.id, lot]),
        earlier = lots.get(lotKey);
      if (
        earlier &&
        (earlier.input.mfg_date !== mfg || earlier.input.expiry_date !== expiry)
      ) {
        errors.push(`与第${earlier.row}行同批号的日期不一致`);
        earlier.errors.push(`与第${row}行同批号的日期不一致`);
      } else if (!earlier) lots.set(lotKey, result);
    }
    return result;
  });
  return {
    rows,
    errorCount: rows.filter((row) => row.errors.length).length,
    costTotal: `${total / 100n}.${(total % 100n).toString().padStart(2, "0")}`,
  };
}
