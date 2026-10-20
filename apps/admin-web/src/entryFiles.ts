import {
  blankEntryLine,
  centsText,
  columnLabel,
  decimalMicros,
  entryQuantity,
  formatEntryNumber,
  lineCents,
  lineIssue,
  newEntryLine,
  type EntryColumn,
  type EntryLine,
  type EntryPackages,
  type EntryProduct,
} from "./documentEntry";
import { microsText } from "./entryTrade";

export const ENTRY_FILE_BYTES = 256 * 1024;
export const ENTRY_FILE_HEADERS = [
  "模板版本",
  "SKU编码",
  "交易单位",
  "包装标识",
  "数量",
  "含税单价",
  "税率（小数）",
  "商品名称",
  "规格型号",
  "生产企业",
  "校验结果",
];
export type EntryMatchBy = "code" | "barcode";
const BARCODE_HEADER = "商品条码";
export interface EntryFileRow {
  sourceRow: number;
  code: string;
  unit: string;
  packageId: string;
  line: EntryLine;
  errors: string[];
  matchBy?: EntryMatchBy;
  sourceErrors?: string[];
  needsCheck?: boolean;
  matchedUnit?: string;
  conversion?: string;
  baseQuantity?: string;
}
export type EntryImportMode = "append" | "replace";

// Apostrophe protection preserves identifiers/decimal precision and prevents spreadsheet formulas.
// A literal leading apostrophe is doubled so exporting and reimporting is lossless.
function csvCell(value: string) {
  const protectedValue = /^[\s'0-9=+\-@＝＋－＠]/u.test(value)
    ? "'" + value
    : value;
  return '"' + protectedValue.replace(/"/g, '""') + '"';
}
export function entryCsv(headers: string[], rows: string[][]) {
  return (
    "\uFEFF" +
    [headers, ...rows].map((row) => row.map(csvCell).join(",")).join("\r\n") +
    "\r\n"
  );
}
export const entryFileTemplate = (matchBy: EntryMatchBy = "code") =>
  entryCsv(
    ENTRY_FILE_HEADERS.map((h) =>
      matchBy === "barcode" && h === "SKU编码" ? BARCODE_HEADER : h,
    ),
    [],
  );
export function downloadEntryCsv(content: string, filename: string) {
  const url = URL.createObjectURL(
    new Blob([content], { type: "text/csv;charset=utf-8" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function records(source: string): { row: number; cells: string[] }[] {
  if (new TextEncoder().encode(source).byteLength > ENTRY_FILE_BYTES)
    throw new Error("文件超过256 KB，请按每单最多50行拆分。");
  const text = source.replace(/^\uFEFF/, "");
  if (/\0/.test(text))
    throw new Error("文件包含无效字符，请另存为 CSV UTF-8。");
  const result: { row: number; cells: string[] }[] = [];
  let field = "",
    cells: string[] = [],
    quoted = false,
    closed = false,
    line = 1,
    row = 1;
  const pushField = () => {
    cells.push(field.startsWith("'") ? field.slice(1) : field);
    field = "";
    closed = false;
  };
  const pushRow = () => {
    pushField();
    if (cells.some((c) => c.trim())) result.push({ row, cells });
    cells = [];
    if (result.length > 51) throw new Error("每张单据最多导入50行商品。");
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
      throw new Error(`第 ${line} 行引号格式错误，请用 CSV 模板重新保存。`);
    else if (!closed) field += char;
    if (field.length > 2000 || cells.length > ENTRY_FILE_HEADERS.length + 1)
      throw new Error(`第 ${row} 行字段过长或列数超出模板范围。`);
  }
  if (quoted) throw new Error(`第 ${row} 行引号未闭合。`);
  if (field || cells.length || closed) pushRow();
  return result;
}
export function parseEntryFile(source: string): EntryFileRow[] {
  const parsed = records(source),
    header = parsed.shift()?.cells.map((c) => c.trim()) || [];
  if (
    !header.length ||
    new Set(header).size !== header.length ||
    header.some((h) => ![...ENTRY_FILE_HEADERS, BARCODE_HEADER].includes(h)) ||
    !["SKU编码", BARCODE_HEADER].some((h) => header.includes(h)) ||
    ["数量", "含税单价"].some((h) => !header.includes(h))
  )
    throw new Error(
      "表头不符合导入模板：至少保留SKU编码或商品条码、数量、含税单价；列可调序，不能使用自定义显示名称或重复列。",
    );
  if (!parsed.length)
    throw new Error("文件中没有商品明细，请在模板表头下填写数据。");
  return parsed.map(({ row, cells }) => {
    const get = (name: string) => (cells[header.indexOf(name)] || "").trim();
    const sourceErrors: string[] = [];
    if (cells.length !== header.length) sourceErrors.push("列数与表头不一致");
    if (get("模板版本") && get("模板版本") !== "1")
      sourceErrors.push("模板版本不支持，请下载当前模板");
    if (get("SKU编码") && get(BARCODE_HEADER))
      sourceErrors.push(
        "同一行只能填写SKU编码或商品条码，请修正文件后重新选择",
      );
    const matchBy: EntryMatchBy =
      get(BARCODE_HEADER) || !header.includes("SKU编码") ? "barcode" : "code";
    const code = get(matchBy === "barcode" ? BARCODE_HEADER : "SKU编码"),
      unit = get("交易单位"),
      packageId = get("包装标识");
    const line = {
      ...newEntryLine(),
      qty: get("数量"),
      unit_price: get("含税单价"),
      tax_rate: get("税率（小数）") || "0.13",
    };
    const result: EntryFileRow = {
      sourceRow: row,
      code,
      matchBy,
      unit,
      packageId,
      line,
      sourceErrors,
      errors: [],
    };
    prepareEntryFileRow(result);
    return result;
  });
}
// Always invalidate previous lookup results before a retry, including inferred units.
// Source structure errors require a new file; editable field errors are recalculated.
export function prepareEntryFileRow(row: EntryFileRow) {
  row.errors = [...(row.sourceErrors || [])];
  row.code = row.code.trim();
  row.unit = row.unit.trim();
  row.packageId = row.packageId.trim();
  row.line.sku_id = "";
  row.line.packaging_id = "";
  delete row.line.product;
  row.matchedUnit = row.conversion = row.baseQuantity = "";
  row.needsCheck = true;
  const barcode = row.matchBy === "barcode",
    max = barcode ? 128 : 100;
  if (!row.code || row.code.length > max || /[\r\n\t\0]/.test(row.code))
    row.errors.push(
      `请填写有效${barcode ? "商品条码" : "SKU编码"}（最多${max}字）`,
    );
  if (row.unit.length > 80) row.errors.push("交易单位名称过长");
  if (
    row.packageId &&
    !/^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$/.test(
      row.packageId,
    )
  )
    row.errors.push("包装标识无效，可清空后按交易单位匹配");
  if (
    [row.line.qty, row.line.unit_price, row.line.tax_rate].some(
      (v) => v.length > 21,
    )
  )
    row.errors.push("数字字段过长，请保留原始小数格式");
}
export function validateEntryFileRow(
  row: EntryFileRow,
  product: EntryProduct | undefined,
  packages?: EntryPackages,
) {
  row.needsCheck = false;
  if (row.errors.length) return;
  const barcode = row.matchBy === "barcode",
    match = product?.barcode_match;
  if (barcode && (!match || match.barcode !== row.code)) {
    row.errors.push(
      "商品条码未唯一匹配到启用的条码，请核对条码资料；不会按商品编码猜测",
    );
    return;
  }
  if (barcode && !match!.available) {
    row.errors.push("条码对应包装已不可用，请核对包装及审核状态");
    return;
  }
  if (!product || !product.eligible) {
    row.errors.push(
      barcode
        ? "条码对应商品当前不可选，请核对商品及资质"
        : "SKU编码未唯一匹配到可选商品，请核对编码及资质",
    );
    return;
  }
  const { barcode_match, ...catalogProduct } = product;
  row.line.product = catalogProduct;
  row.line.sku_id = product.id;
  let pack: EntryPackages["items"][number] | undefined;
  if (barcode) {
    if (match!.packaging_id) {
      pack = packages?.items.find((p) => p.id === match!.packaging_id);
      if (
        !pack ||
        pack.unit_name !== match!.unit_name ||
        pack.numerator !== match!.numerator ||
        pack.denominator !== match!.denominator
      )
        row.errors.push("条码对应包装已变更或不可用，请重新校验或核对包装资料");
    }
    if (
      (row.packageId &&
        row.packageId.toLowerCase() !== match!.packaging_id?.toLowerCase()) ||
      (row.unit && row.unit !== match!.unit_name)
    )
      row.errors.push(
        "填写的单位或包装与条码对应包装不一致，请核对；数量和单价不会自动换算",
      );
  } else if (row.packageId) {
    pack = packages?.items.find(
      (p) => p.id.toLowerCase() === row.packageId.toLowerCase(),
    );
    if (!pack || (row.unit && row.unit !== pack.unit_name))
      row.errors.push("包装标识已不可用、不属于该商品或与单位名称不符");
  } else if (row.unit && row.unit !== product.base_unit) {
    const matches =
      packages?.items.filter((p) => p.unit_name === row.unit) || [];
    if (matches.length !== 1)
      row.errors.push(
        "交易单位未唯一匹配到已审核包装；请核对单位或提供包装标识",
      );
    else pack = matches[0];
  }
  if (pack) row.line.packaging_id = pack.id;
  if (!row.errors.length) {
    row.matchedUnit = pack?.unit_name || product.base_unit;
    row.conversion = pack
      ? `${pack.denominator} ${pack.unit_name} ＝ ${pack.numerator} ${product.base_unit}`
      : `基本单位：${product.base_unit}`;
    const quantity = entryQuantity(
      row.line.qty,
      product.qty_scale,
      product.base_unit,
      pack,
    );
    if (quantity.micros !== undefined)
      row.baseQuantity = `${microsText(quantity.micros)} ${product.base_unit}`;
  }
  if (!row.errors.length) {
    const issue = lineIssue(row.line, packages);
    if (issue) row.errors.push(issue.message);
  }
}
function fullCells(row: EntryFileRow) {
  return [
    "1",
    row.matchBy === "barcode" ? "" : row.code,
    row.unit,
    row.packageId,
    row.line.qty,
    row.line.unit_price,
    row.line.tax_rate,
    row.line.product?.name || "",
    row.line.product?.specification || "",
    row.line.product?.manufacturer_name || "",
    row.errors.join("；"),
  ];
}
export function entryErrorCsv(rows: EntryFileRow[]) {
  const barcodes = rows.some((r) => r.matchBy === "barcode");
  return entryCsv(
    barcodes ? [...ENTRY_FILE_HEADERS, BARCODE_HEADER] : ENTRY_FILE_HEADERS,
    rows.map((r) => [
      ...fullCells(r),
      ...(barcodes ? [r.matchBy === "barcode" ? r.code : ""] : []),
    ]),
  );
}
export function exportEntryFile(
  lines: EntryLine[],
  packages: Map<string, EntryPackages>,
  columns?: EntryColumn[],
) {
  const rows = lines.filter((r) => !blankEntryLine(r));
  if (!rows.length) throw new Error("没有可导出的商品明细。");
  for (const [i, row] of rows.entries()) {
    if (row.search_text || !row.sku_id || !row.product)
      throw new Error(`第 ${i + 1} 行商品尚未确认或资料未加载，请确认后导出。`);
    if (
      row.packaging_id &&
      !packages.get(row.sku_id)?.items.some((p) => p.id === row.packaging_id)
    )
      throw new Error(`第 ${i + 1} 行包装已不可用，请先核对交易单位。`);
  }
  const unit = (row: EntryLine) =>
    row.packaging_id
      ? packages.get(row.sku_id)!.items.find((p) => p.id === row.packaging_id)!
          .unit_name
      : row.product!.base_unit;
  if (!columns)
    return entryErrorCsv(
      rows.map((line, i) => ({
        sourceRow: i + 1,
        code: line.product!.code,
        unit: unit(line),
        packageId: line.packaging_id,
        line,
        errors: lineIssue(line) ? [lineIssue(line)!.message] : [],
      })),
    );
  const visible = columns.filter((c) => c.visible);
  return entryCsv(
    ["行", ...visible.map(columnLabel)],
    rows.map((row, i) => [
      String(i + 1),
      ...visible.map((c) => {
        switch (c.key) {
          case "product":
            return `${row.product!.name} · ${row.product!.code}`;
          case "pack":
            return unit(row);
          case "qty":
            return formatEntryNumber(row.qty, c);
          case "price":
            return formatEntryNumber(row.unit_price, c);
          case "tax": {
            const n = decimalMicros(row.tax_rate);
            if (c.tax_display !== "percent" || n === null) return row.tax_rate;
            const fraction = String(n % 10000n)
              .padStart(4, "0")
              .replace(/0+$/, "");
            return `${n / 10000n}${fraction ? "." + fraction : ""}%`;
          }
          case "amount":
            return lineCents(row) === null
              ? ""
              : formatEntryNumber(centsText(lineCents(row)!), c);
          case "specification":
            return row.product!.specification;
          case "manufacturer_name":
            return row.product!.manufacturer_name;
          default:
            return documentCell(
              entryDocumentValues(row, packages.get(row.sku_id)),
              c,
            );
        }
      }),
    ]),
  );
}
export function entryLinesSignature(lines: EntryLine[]) {
  return JSON.stringify(
    lines.map((r) => [
      r.client_key,
      r.sku_id,
      r.packaging_id,
      r.qty,
      r.unit_price,
      r.tax_rate,
      r.search_text || "",
    ]),
  );
}
import { documentCell, entryDocumentValues } from "./documentColumns";
