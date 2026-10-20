import { test, expect } from "@playwright/test";
import {
  previewOpening,
  openingTemplate,
  OPENING_COLUMNS,
  MAX_IMPORT_BYTES,
} from "../src/openingImport";
const skus = [
  { id: "sku1", code: "000123", name: "器械", base_unit: "个", qty_scale: 0 },
];
const locations = [
  { id: "loc1", code: "A01", warehouse_id: "wh1" },
  { id: "loc2", code: "B01", warehouse_id: "wh2" },
];
const line = [
  "000123",
  "A01",
  "LOT01",
  "2026-01-01",
  "2027-12-31",
  "2",
  "40.20",
];
const csv = (values: string[]) =>
  values.map((v) => '"' + v.replaceAll('"', '""') + '"').join(",");
const parse = (source: string, catalog = skus) =>
  previewOpening(source, catalog, locations, "wh1", "2026-10-16");
test("opening import preserves identifiers and quoted text with exact cost totals", () => {
  const second = [...line];
  second[2] = '批号,含"引号"';
  second[6] = "0.10";
  const result = parse(
    openingTemplate() + csv(line) + "\r\n\r\n" + csv(second),
  );
  expect(result.errorCount).toBe(0);
  expect(result.costTotal).toBe("40.30");
  expect(result.rows.map((r) => r.row)).toEqual([2, 4]);
  expect(result.rows[0]!.sku_code).toBe("000123");
  expect(result.rows[1]!.input.lot_no).toBe(second[2]);
});
test("opening import accepts reordered exact headers and representable trailing zeros", () => {
  const input = [...line];
  input[5] = "2.000000";
  input[6] = "0";
  const result = parse(
    [...OPENING_COLUMNS].reverse().join(",") + "\n" + csv(input.reverse()),
  );
  expect(result.errorCount).toBe(0);
  expect(result.rows[0]!.input.cost_amount).toBe("0.00");
});
test("opening import calculates costs beyond JavaScript number precision without rounding", () => {
  const a = [...line],
    b = [...line];
  a[6] = "900719925474099.99";
  b[2] = "B2";
  b[6] = "0.01";
  expect(parse(openingTemplate() + csv(a) + "\n" + csv(b)).costTotal).toBe(
    "900719925474100.00",
  );
});
for (const [name, column, value, message] of [
  ["unknown SKU", 0, "unknown", "SKU编码不存在"],
  ["wrong warehouse location", 1, "B01", "不属于当前仓库"],
  ["empty lot", 2, "", "批号必填"],
  ["multiline lot", 2, "A\nB", "控制字符"],
  ["invalid calendar date", 3, "2026-02-30", "有效的 YYYY-MM-DD"],
  ["future manufacture", 3, "2026-10-17", "晚于今天"],
  ["expired", 4, "2026-10-15", "已过期"],
  ["expiry overflow", 4, "9999-12-31", "最晚9999-12-30"],
  ["excel date serial", 3, "46000", "有效的 YYYY-MM-DD"],
  ["zero quantity", 5, "0", "数量须大于0"],
  ["negative quantity", 5, "-1", "数量须大于0"],
  ["scientific quantity", 5, "1e3", "科学计数法"],
  ["quantity overflow", 5, "100000000000000", "14位整数"],
  ["SKU precision", 5, "0.5", "商品精度"],
  ["quantity syntax precision", 5, "2.0000000", "6位小数"],
  ["negative cost", 6, "-1", "非负金额"],
  ["overprecise cost", 6, "1.001", "2位小数"],
  ["cost overflow", 6, "10000000000000000", "16位整数"],
  ["formatted money", 6, "1,000", "非负金额"],
] as const)
  test(`opening import rejects ${name} with a row error`, () => {
    const values = [...line];
    values[column] = value;
    const result = parse(openingTemplate() + csv(values));
    expect(result.errorCount).toBe(1);
    expect(result.rows[0]!.errors.join(" ")).toContain(message);
  });
test("opening import validates fractional quantity using the chosen SKU precision", () => {
  const values = [...line];
  values[5] = "0.125000";
  expect(
    parse(openingTemplate() + csv(values), [{ ...skus[0]!, qty_scale: 3 }])
      .errorCount,
  ).toBe(0);
});
test("opening import flags both duplicates and conflicting batch dates", () => {
  const duplicate = parse(openingTemplate() + csv(line) + "\n" + csv(line));
  expect(duplicate.errorCount).toBe(2);
  const second = [...line];
  second[1] = "A02";
  second[4] = "2028-01-01";
  const result = previewOpening(
    openingTemplate() + csv(line) + "\n" + csv(second),
    skus,
    [...locations, { id: "loc3", code: "A02", warehouse_id: "wh1" }],
    "wh1",
    "2026-10-16",
  );
  expect(result.errorCount).toBe(2);
  expect(result.rows[1]!.errors.join(" ")).toContain("日期不一致");
});
test("opening import accepts 200 rows but never truncates a larger file", () => {
  const rows = Array.from({ length: 200 }, (_, i) => {
    const v = [...line];
    v[2] = `LOT${i}`;
    return csv(v);
  });
  expect(parse(openingTemplate() + rows.join("\n")).rows).toHaveLength(200);
  expect(() =>
    parse(openingTemplate() + [...rows, csv(line)].join("\n")),
  ).toThrow("最多导入 200 行");
  expect(() => parse(" ".repeat(MAX_IMPORT_BYTES + 1))).toThrow("512 KB");
});
test("opening import rejects missing, duplicate and extra headers, malformed CSV and empty files", () => {
  for (const header of [
    "",
    OPENING_COLUMNS.slice(1).join(","),
    [...OPENING_COLUMNS, "备注"].join(","),
    [...OPENING_COLUMNS.slice(0, 6), "期初数量"].join(","),
  ]) {
    expect(() => parse(header + "\n" + csv(line))).toThrow("表头与模板不一致");
  }
  expect(() => parse(openingTemplate())).toThrow("只有表头或空行");
  expect(() => parse(openingTemplate() + '"unclosed')).toThrow("引号未闭合");
  expect(() => parse(openingTemplate() + '"closed"junk')).toThrow(
    "引号格式不正确",
  );
  expect(
    parse(openingTemplate() + csv(line.slice(0, 6))).rows[0]!.errors,
  ).toContain("列数与表头不一致");
});
