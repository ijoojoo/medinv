import { test, expect } from "@playwright/test";
import { mkdir, readFile } from "node:fs/promises";
import { formatEntryNumber } from "../src/documentEntry";
import {
  wholesale,
  read,
  command,
  nav,
  stockedCustomer,
  chooseEntrySku,
  chooseParty,
} from "./business-helpers";

const payload = (reply: any, columns = reply.columns) => ({
  expected_version: reply.version,
  columns: columns.map(({ required, editable, ...c }: any) => c),
});

test("MDM negative temperatures and custom decimals follow display precision without losing identifier zeros", () => {
  expect(
    formatEntryNumber("-20.125000", { decimals: 2, grouping: false }),
  ).toBe("-20.13");
  expect(formatEntryNumber("-1234.5", { decimals: 2, grouping: true })).toBe(
    "-1,234.50",
  );
  expect(formatEntryNumber("-0.0001", { decimals: 2, grouping: false })).toBe(
    "0.00",
  );
  expect(formatEntryNumber("001234", { decimals: -1, grouping: false })).toBe(
    "001234",
  );
});

test("all MDM tabs feed sales editor and immutable history, independent of purchase settings", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(180000);
  const owner = await wholesale(context, "demo.owner");
  const entity = owner.entities[0].id;
  const editorPath = `/api/v1/document-columns/sales_orders/editor?entity_id=${entity}`;
  const viewPath = `/api/v1/document-columns/sales_orders/view?entity_id=${entity}`;
  const originalEditor = await read(context, editorPath);
  const originalView = await read(context, viewPath);
  const purchasePath = `/api/v1/document-columns/orders/editor?entity_id=${entity}`;
  const purchaseBefore = await read(context, purchasePath);
  const reviewer = await browser.newContext();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  let savedId = "";
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    const f = await stockedCustomer(context, owner, reviewer, checker);
    const pricesPath = `/api/v1/catalog/skus/${f.product.sku_id}/prices`;
    const price = await read(context, pricesPath);
    await command(context, owner, pricesPath, {
      expected_version: price.version,
      purchase_price: "0",
      wholesale_price: "18.5",
      retail_price: "23",
      reason: "全字段页面验收",
    });
    const settingsPath = `/api/v1/catalog/master/products/${f.product.id}/entity-settings`;
    const ops = await read(context, settingsPath + `?entity_id=${entity}`);
    await command(context, owner, settingsPath, {
      entity_id: entity,
      expected_version: ops.version,
      ...ops.values,
      business_note: "仅当前主体的经营备注",
      reason: "全字段页面验收",
    });
    const allKeys = new Set(originalEditor.fields.map((v: any) => v.key));
    for (const key of [
      "retail_price",
      "quality_note",
      "operation_business_note",
      "code_meituan",
      "brand",
    ])
      expect(allKeys.has(key), key).toBe(true);
    await command(
      context,
      owner,
      editorPath,
      payload(
        originalEditor,
        originalEditor.fields.map((v: any) => v.default),
      ),
    );
    await page.goto("/");
    await nav(page, "批发销售");
    await page.getByRole("button", { name: "新建销售单", exact: true }).click();
    const editor = page.getByRole("region", {
      name: "新建销售单",
      exact: true,
    });
    await chooseParty(
      editor.getByRole("combobox", { name: "客户", exact: true }),
      { label: f.customer },
    );
    await editor.getByLabel("收货地址").fill("全字段验收收货地址");
    await chooseEntrySku(
      editor.locator(".entry-row").first(),
      f.product.sku_id,
    );
    await editor.getByLabel("销售数量", { exact: true }).fill("3");
    await editor.getByLabel("含税单价（元）", { exact: true }).fill("20");
    await editor.getByRole("button", { name: "列设置", exact: true }).click();
    const settings = page.getByRole("dialog", {
      name: "配置单据明细列",
      exact: true,
    });
    const group = settings.getByRole("combobox", {
      name: "字段来源",
      exact: true,
    });
    for (const tab of [
      "基本信息",
      "价格信息",
      "质量管控",
      "运营管控",
      "扩展属性",
      "第三方对接",
      "自定义属性",
    ])
      await expect(
        group.getByRole("option", { name: tab, exact: true }),
      ).toHaveCount(1);
    for (const [tab, key] of [
      ["price", "retail_price"],
      ["quality", "quality_note"],
      ["operations", "operation_business_note"],
      ["integration", "code_meituan"],
    ]) {
      await group.selectOption(tab);
      const field = originalEditor.fields.find((v: any) => v.key === key);
      await settings
        .getByRole("checkbox", { name: field.label, exact: true })
        .check();
    }
    await group.selectOption("all");
    await mkdir("../../outputs/document-field-catalog", { recursive: true });
    await page.screenshot({
      path: "../../outputs/document-field-catalog/全部页签字段设置.png",
      fullPage: true,
    });
    await settings
      .getByRole("button", { name: "保存配置", exact: true })
      .click();
    await expect(settings).not.toBeVisible();
    const row = editor.locator(".entry-row").first();
    await expect(row.locator('[data-col="retail_price"]')).toContainText("23");
    await expect(
      row.locator('[data-col="operation_business_note"]'),
    ).toContainText("仅当前主体的经营备注");
    await expect(row.locator('[data-col="quality_note"]')).toContainText(
      "未维护",
    );
    await expect(row.locator('[data-col="code_meituan"]')).toContainText(
      "未维护",
    );
    expect((await read(context, purchasePath)).columns).toEqual(
      purchaseBefore.columns,
    );
    const response = page.waitForResponse(
      (r) =>
        r.request().method() === "POST" &&
        new URL(r.url()).pathname === "/api/v1/sales-orders",
    );
    await editor
      .getByRole("button", { name: "保存销售单", exact: true })
      .click();
    const written = await response;
    expect(written.status()).toBe(200);
    savedId = (await written.json()).data.id;
    await expect(editor).not.toBeVisible();
    const currentPrice = await read(context, pricesPath);
    await command(context, owner, pricesPath, {
      expected_version: currentPrice.version,
      purchase_price: "0",
      wholesale_price: "18.5",
      retail_price: "99",
      reason: "验证历史价格保留",
    });
    await page.getByRole("button", { name: "刷新销售详情" }).click();
    const grid = page.getByRole("region", {
      name: "销售订单明细",
      exact: true,
    });
    await grid
      .getByRole("button", { name: "配置单据明细列", exact: true })
      .click();
    await settings
      .getByRole("button", { name: "从录入配置复制", exact: true })
      .click();
    await settings
      .getByRole("button", { name: "保存配置", exact: true })
      .click();
    await expect(settings).not.toBeVisible();
    await expect(grid.locator('[data-grid-cell="retail_price"]')).toContainText(
      "23",
    );
    await expect(grid.locator('[data-grid-cell="amount"]')).toHaveText("60.00");
    await expect(
      grid.locator('[data-grid-cell="operation_business_note"]'),
    ).toContainText("仅当前主体的经营备注");
    const download = page.waitForEvent("download");
    await grid
      .getByRole("button", { name: "导出当前视图", exact: true })
      .click();
    const csv = await readFile((await (await download).path())!, "utf8");
    expect(csv).toContain("参考零售价");
    expect(csv).toContain("23.000000");
    expect(csv).toContain("仅当前主体的经营备注");
    await page.screenshot({
      path: "../../outputs/document-field-catalog/销售订单全字段.png",
      fullPage: true,
    });
    await page.reload();
    await expect(grid.locator('[data-grid-cell="retail_price"]')).toContainText(
      "23",
    );
    expect(errors).toEqual([]);
  } finally {
    if (savedId) {
      const doc = await read(context, `/api/v1/sales-orders/${savedId}`);
      await command(context, owner, `/api/v1/sales-orders/${savedId}/close`, {
        expected_version: doc.order.version,
        reason: "软件验收完成，关闭未履约测试订单",
      });
    }
    for (const [path, original] of [
      [editorPath, originalEditor],
      [viewPath, originalView],
    ]) {
      const latest = await read(context, path);
      await command(context, owner, path, payload(latest, original.columns));
    }
    await reviewer.close();
  }
});
