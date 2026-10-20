import { chooseParty } from "./business-helpers";
import {
  test,
  expect,
  type Download,
  type Locator,
  type Page,
} from "@playwright/test";
import {
  wholesale,
  stockedCustomer,
  read,
  command,
  nav,
  chooseEntrySku,
} from "./business-helpers";
import {
  defaultEntryColumns,
  newEntryLine,
  type EntryProduct,
  type EntryPackages,
} from "../src/documentEntry";
import {
  entryCsv,
  ENTRY_FILE_HEADERS,
  parseEntryFile,
  validateEntryFileRow,
  exportEntryFile,
} from "../src/entryFiles";

const product: EntryProduct = {
  id: "00000000-0000-0000-0000-000000000001",
  code: "00123",
  name: '=test,"quoted"\nsecond line',
  base_unit: "个",
  qty_scale: 0,
  specification: "10个",
  manufacturer_name: "测试厂家",
  eligible: true,
};
const packId = "00000000-0000-0000-0000-000000000002";
const packs: EntryPackages = {
  base_unit: "个",
  items: [{ id: packId, unit_name: "箱", numerator: "3", denominator: "1" }],
};
const simple = (rows: string[][]) =>
  entryCsv(["SKU编码", "数量", "含税单价", "税率（小数）", "交易单位"], rows);

test("CSV round trips identifiers, decimal strings, escaped text and package versions", () => {
  const line = {
    ...newEntryLine(),
    sku_id: product.id,
    product,
    packaging_id: packId,
    qty: "2.000000",
    unit_price: "99999999999999.123456",
    tax_rate: "0.060000",
  };
  const csv = exportEntryFile(
    [line, { ...line, client_key: crypto.randomUUID() }],
    new Map([[product.id, packs]]),
  );
  expect(csv).toContain('"\'00123"');
  expect(csv).toContain('"\'=test,""quoted""');
  const rows = parseEntryFile(csv);
  expect(rows).toHaveLength(2);
  expect(rows[0]!.sourceRow).toBe(2);
  expect(rows[1]!.sourceRow).toBe(4);
  for (const row of rows) {
    validateEntryFileRow(row, product, packs);
    expect(row.errors).toEqual([]);
    expect(row.line).toMatchObject({
      qty: line.qty,
      unit_price: line.unit_price,
      tax_rate: line.tax_rate,
      packaging_id: packId,
    });
    expect(row.code).toBe("00123");
  }
  const apostrophe = parseEntryFile(
    simple([["'CODE", "1", "2", "0.13", ""]]),
  )[0]!;
  expect(apostrophe.code).toBe("'CODE");
  const columns = defaultEntryColumns();
  columns.find((c) => c.key === "price")!.label = "采购价";
  columns.find((c) => c.key === "price")!.decimals = 2;
  const view = exportEntryFile([line], new Map([[product.id, packs]]), columns);
  expect(view).toContain("采购价（含税）");
  expect(() => parseEntryFile(view)).toThrow("表头");
  expect(line.unit_price).toBe("99999999999999.123456");
});

test("CSV validation is bounded and never silently guesses units or drops bad rows", () => {
  const fifty = Array.from({ length: 50 }, () => ["00123", "1", "2", "", ""]);
  expect(parseEntryFile(simple(fifty))).toHaveLength(50);
  expect(() => parseEntryFile(simple([...fifty, fifty[0]!]))).toThrow("50");
  expect(() =>
    parseEntryFile('SKU编码,数量,含税单价\n"unterminated,1,2'),
  ).toThrow("引号未闭合");
  expect(() => parseEntryFile("SKU编码,数量,数量,含税单价\n001,1,2,3")).toThrow(
    "表头",
  );
  expect(() =>
    parseEntryFile("SKU编码,数量,含税单价\n" + "x".repeat(300000)),
  ).toThrow("256");
  const rows = parseEntryFile(
    simple([
      ["00123", "0.5", "2", "0.13", "箱"],
      ["00123", "1", "2", "13%", ""],
      ["00123", "1", "2", "0.13", "袋"],
      ["00123", "1", "=1+1", "0.13", ""],
    ]),
  );
  rows.forEach((r) => validateEntryFileRow(r, product, packs));
  expect(rows[0]!.errors.join()).toContain("基本数量");
  expect(rows[1]!.errors.join()).toContain("税率");
  expect(rows[2]!.errors.join()).toContain("交易单位");
  expect(rows[3]!.errors.join()).toContain("含税单价");
  const shape = parseEntryFile("SKU编码,数量,含税单价\n00123,1")[0]!;
  expect(shape.errors).toContain("列数与表头不一致");
  const version = parseEntryFile(
    entryCsv(ENTRY_FILE_HEADERS, [
      ["2", "00123", "", "", "1", "2", "0.13", "", "", "", ""],
    ]),
  )[0]!;
  expect(version.errors.join()).toContain("版本");
});

async function downloadText(download: Download) {
  const stream = await download.createReadStream();
  if (!stream) throw new Error("download missing");
  const parts: Buffer[] = [];
  for await (const part of stream) parts.push(Buffer.from(part));
  return Buffer.concat(parts).toString("utf8");
}
async function upload(dialog: Locator, source: string, name = "商品明细.csv") {
  await dialog.getByLabel("商品明细CSV文件", { exact: true }).setInputFiles({
    name,
    mimeType: "text/csv",
    buffer: Buffer.from(source, "utf8"),
  });
}
async function getExport(page: Page, editor: Locator, display = false) {
  await editor.getByRole("button", { name: "导出明细", exact: true }).click();
  const dialog = page.getByRole("dialog", {
    name: "导出商品明细",
    exact: true,
  });
  if (display)
    await dialog.getByRole("radio", { name: /按当前显示列/ }).check();
  const pending = page.waitForEvent("download");
  await dialog.getByRole("button", { name: "下载CSV", exact: true }).click();
  const download = await pending;
  expect(download.suggestedFilename()).toContain("编辑草稿");
  const text = await downloadText(download);
  await expect(dialog).not.toBeVisible();
  return text;
}

for (const purchase of [true, false]) {
  test(`${purchase ? "purchase" : "sales"} file preview, atomic append/replace, undo, export and refresh`, async ({
    page,
    context,
    browser,
  }) => {
    test.setTimeout(150000);
    const owner = await wholesale(context, "demo.owner"),
      reviewer = await browser.newContext();
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    try {
      const checker = await wholesale(reviewer, "demo.quality"),
        f = await stockedCustomer(context, owner, reviewer, checker);
      const channel = purchase ? "purchase" : "sale",
        action = purchase ? "新建采购单" : "新建销售单",
        qtyName = purchase ? "采购数量" : "销售数量";
      const lookup = await read(
          context,
          `/api/v1/catalog/entry-products?entity_id=${f.entity}&channel=${channel}&sku_id=${f.product.sku_id}`,
        ),
        code = lookup.items[0].code;
      const path = `/api/v1/catalog/skus/${f.product.sku_id}/packaging`,
        choices = await read(context, path);
      const pack = await command(context, owner, path, {
        expected_sku_version: choices.sku_version,
        unit_name: "箱",
        numerator: "3",
        denominator: "1",
        reason: "文件导入虚拟验收",
      });
      await command(reviewer, checker, `${path}/${pack.id}/review`, {
        expected_version: pack.version,
        decision: "APPROVE",
        evidence_ref: "FILE-PACK",
        reason: "独立核对包装",
      });
      await page.goto("/");
      await nav(page, purchase ? "采购入库" : "批发销售");
      await page.getByRole("button", { name: action === "新建采购单" ? "新建采购订单" : action, exact: true }).click();
      const editor = page.getByRole("region", { name: action, exact: true }),
        rows = editor.locator(".entry-row");
      await chooseEntrySku(rows.first(), f.product.sku_id);
      await rows.first().getByLabel(qtyName, { exact: true }).fill("4");
      await rows
        .first()
        .getByLabel("含税单价（元）", { exact: true })
        .fill("0.123456");
      const key = await rows.first().getAttribute("data-row-key");
      await editor
        .getByRole("button", { name: "导入文件", exact: true })
        .click();
      const dialog = page.getByRole("dialog", {
        name: "导入商品明细",
        exact: true,
      });
      const templateDownload = page.waitForEvent("download");
      await dialog
        .getByRole("button", { name: "下载导入模板", exact: true })
        .click();
      expect(await downloadText(await templateDownload)).toContain("SKU编码");
      await upload(
        dialog,
        simple([
          [code, "1", "2", "0.13", ""],
          ["NO-SUCH-SKU", "2", "3", "0.13", ""],
        ]),
      );
      await expect(dialog).toContainText("1 行需修正");
      await expect(rows).toHaveCount(1);
      await expect(
        dialog.getByRole("button", { name: "确认追加 2 行", exact: true }),
      ).toBeDisabled();
      const failedDownload = page.waitForEvent("download");
      await dialog
        .getByRole("button", { name: "下载待修正清单", exact: true })
        .click();
      expect(await downloadText(await failedDownload)).toContain("未唯一匹配");
      await upload(
        dialog,
        simple([
          [code, "2", "0.335001", "0.06", "箱"],
          [code, "3", "1.005001", "0.13", ""],
        ]),
      );
      await expect(dialog).toContainText("0 行需修正");
      await dialog
        .getByRole("button", { name: "确认追加 2 行", exact: true })
        .click();
      await expect(rows).toHaveCount(3);
      await expect(rows.first()).toHaveAttribute("data-row-key", key!);
      await expect(
        rows.nth(1).getByRole("combobox", { name: "交易单位 2", exact: true }),
      ).toHaveValue(pack.id);
      const full = await getExport(page, editor),
        parsed = parseEntryFile(full);
      expect(parsed).toHaveLength(3);
      expect(parsed[1]!.packageId).toBe(pack.id);
      expect(parsed[1]!.line.unit_price).toBe("0.335001");
      const display = await getExport(page, editor, true);
      expect(display).toContain("含税单价");
      expect(() => parseEntryFile(display)).toThrow("表头");
      await editor
        .getByRole("button", { name: "撤销导入", exact: true })
        .click();
      await expect(rows).toHaveCount(1);
      await expect(
        rows.first().getByLabel(qtyName, { exact: true }),
      ).toHaveValue("4");
      await editor
        .getByRole("button", { name: "导入文件", exact: true })
        .click();
      await upload(dialog, full);
      await expect(dialog).toContainText("0 行需修正");
      await dialog.getByRole("radio", { name: /替换当前明细/ }).check();
      await expect(
        dialog.getByRole("button", { name: "确认替换 3 行", exact: true }),
      ).toBeDisabled();
      await dialog.getByRole("checkbox", { name: /确认替换当前/ }).check();
      await dialog
        .getByRole("button", { name: "确认替换 3 行", exact: true })
        .click();
      await expect(rows).toHaveCount(3);
      await rows.first().getByLabel(qtyName, { exact: true }).fill("5");
      await expect(
        editor.getByRole("button", { name: "撤销导入", exact: true }),
      ).toBeDisabled();
      await editor
        .getByRole("button", { name: "保存草稿", exact: true })
        .click();
      await expect(editor.locator(".editor-actions")).toContainText(
        "草稿已保存",
      );
      await page.reload();
      await expect(rows).toHaveCount(3);
      await expect(
        rows.first().getByLabel(qtyName, { exact: true }),
      ).toHaveValue("5");
      await expect(
        rows.nth(1).getByRole("combobox", { name: "交易单位 2", exact: true }),
      ).toHaveValue(pack.id);
      await editor
        .getByRole("button", { name: "导入文件", exact: true })
        .click();
      const fifty = simple(
        Array.from({ length: 50 }, () => [code, "1", "1.234567", "0.13", ""]),
      );
      await upload(dialog, fifty);
      await expect(dialog).toContainText("共 50 行");
      await expect(
        dialog.getByRole("button", { name: "确认追加 50 行", exact: true }),
      ).toBeDisabled();
      await page.screenshot({
        path: `../../artifacts/entry-file-preview-${channel}.png`,
        animations: "disabled",
      });
      await dialog.getByRole("radio", { name: /替换当前明细/ }).check();
      await dialog.getByRole("checkbox", { name: /确认替换当前/ }).check();
      await dialog
        .getByRole("button", { name: "确认替换 50 行", exact: true })
        .click();
      await expect(rows).toHaveCount(50);
      await rows.last().getByLabel(qtyName, { exact: true }).focus();
      await rows.last().getByLabel(qtyName, { exact: true }).press("Enter");
      await expect(
        rows.last().getByLabel("含税单价（元）", { exact: true }),
      ).toBeFocused();
      await editor
        .getByRole("button", { name: "保存草稿", exact: true })
        .click();
      await expect(editor.locator(".editor-actions")).toContainText(
        "草稿已保存",
      );
      const draftId = new URL(page.url()).pathname.split("/").pop();
      expect(
        (await read(context, `/api/v1/document-drafts/${draftId}`)).content
          .lines,
      ).toHaveLength(50);
      await editor
        .getByRole("button", { name: "撤销导入", exact: true })
        .click();
      await expect(rows).toHaveCount(3);
      await chooseParty(
        editor.getByRole("combobox", {
          name: purchase ? "供应商" : "客户",
          exact: true,
        }),
        f.party.id,
      );
      if (!purchase)
        await editor
          .getByLabel("收货地址", { exact: true })
          .fill("文件导入验收地址");
      const endpoint = purchase
        ? "/api/v1/purchase-orders"
        : "/api/v1/sales-orders";
      const saved = page.waitForResponse(
        (r) => r.url().endsWith(endpoint) && r.request().method() === "POST",
      );
      await editor
        .getByRole("button", {
          name: purchase ? "保存采购单" : "保存销售单",
          exact: true,
        })
        .click();
      const response = await saved;
      expect(response.status(), await response.text()).toBe(200);
      expect(response.request().postDataJSON().lines[1]).toMatchObject({
        packaging_id: pack.id,
        qty: "2",
        unit_price: "0.335001",
        tax_rate: "0.06",
      });
      const created = (await response.json()).data,
        doc = await read(context, `${endpoint}/${created.id}`);
      expect(doc.lines).toHaveLength(3);
      expect(doc.lines[1].qty).toBe("6.000000");
      expect(doc.lines[1].package_snapshot.packaging_id).toBe(pack.id);
      await expect(editor).not.toBeVisible();
      expect(errors).toEqual([]);
    } finally {
      await reviewer.close();
    }
  });
}

test("unsupported files and cancelled lookups never apply stale import rows", async ({
  page,
  context,
}) => {
  await wholesale(context, "demo.owner");
  await page.goto("/");
  await nav(page, "采购入库");
  await page.getByRole("button", { name: "新建采购订单", exact: true }).click();
  const editor = page.getByRole("region", { name: "新建采购单", exact: true });
  await editor.getByRole("button", { name: "导入文件", exact: true }).click();
  const dialog = page.getByRole("dialog", {
    name: "导入商品明细",
    exact: true,
  });
  await upload(dialog, "ignored", "old.xlsx");
  await expect(dialog.getByRole("alert")).toContainText("CSV UTF-8");
  await dialog.getByLabel("商品明细CSV文件", { exact: true }).setInputFiles({
    name: "bad.csv",
    mimeType: "text/csv",
    buffer: Buffer.from([0xff, 0xfe, 0xff]),
  });
  await expect(dialog.getByRole("alert")).toContainText("UTF-8编码");
  let finished = false;
  let release: () => void = () => {};
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/v1/catalog/entry-products?**", async (route) => {
    await pending;
    await route
      .fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { items: [product], has_more: false } }),
      })
      .catch(() => {});
    finished = true;
  });
  await upload(dialog, simple([["00123", "1", "1", "0.13", ""]]));
  await expect(dialog.getByRole("status")).toBeVisible();
  await upload(dialog, "invalid", "wrong.xlsx");
  await expect(dialog.getByRole("alert")).toContainText("CSV UTF-8");
  release();
  await expect.poll(() => finished).toBe(true);
  await expect(
    dialog.getByRole("button", { name: "确认追加 0 行", exact: true }),
  ).toBeDisabled();
  await expect(editor.locator(".entry-row")).toHaveCount(1);
  await dialog.getByRole("button", { name: "返回", exact: true }).click();
  await expect(dialog).not.toBeVisible();
});
