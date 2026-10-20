import { test, expect, type Locator } from "@playwright/test";
import { normalizeListColumns, partyListColumns } from "../src/catalogColumns";
import { nav, read, wholesale } from "./business-helpers";

async function dragColumn(dialog: Locator, from: string, to: string) {
  const handle = dialog.locator(`[data-list-column="${from}"] .column-drag`);
  const target = dialog.locator(`[data-list-column="${to}"] .column-name`);
  // Begin the native drag before scrolling the long settings list to its target.
  await handle.scrollIntoViewIfNeeded();
  const start = (await handle.boundingBox())!;
  const mouse = dialog.page().mouse;
  await mouse.move(start.x + start.width / 2, start.y + start.height / 2);
  await mouse.down();
  await mouse.move(
    start.x + start.width / 2 + 12,
    start.y + start.height / 2 + 6,
    { steps: 4 },
  );
  await target.scrollIntoViewIfNeeded();
  const end = (await target.boundingBox())!;
  await mouse.move(end.x + 20, end.y + end.height / 2, { steps: 10 });
  await mouse.move(end.x + 22, end.y + end.height / 2);
  await mouse.up();
}

test("column preferences recover invalid values and cannot hide identity or inject unknown fields", () => {
  const columns = normalizeListColumns(
    [
      { key: "delivery_address", visible: true, width: 320, wrap: true },
      {
        key: "identity",
        visible: false,
        width: -1,
        required: false,
        label: "replacement",
      },
      { key: "identity", width: 500 },
      { key: "state", visible: false, width: 9999 },
      { key: "unrecognized_field", visible: true, width: 100 },
      null,
      false,
      "invalid",
    ],
    partyListColumns,
  );
  expect(columns).toHaveLength(partyListColumns.length);
  expect(columns[0]).toMatchObject({
    key: "delivery_address",
    visible: true,
    width: 320,
    wrap: true,
  });
  expect(columns[1]).toMatchObject({
    key: "identity",
    label: "名称 / 编码",
    visible: true,
    width: 250,
    required: true,
  });
  expect(columns.find((c) => c.key === "state")).toMatchObject({
    visible: true,
    width: 110,
  });
  expect(columns.some((c) => c.key === "unrecognized_field")).toBe(false);
  expect(
    normalizeListColumns({}, partyListColumns).filter((c) => c.visible),
  ).toHaveLength(5);
});

test("party list columns support drag, sizing, cancel, reload, account isolation and default reset without data writes", async ({
  page,
  context,
}) => {
  test.setTimeout(90000);
  const owner = await wholesale(context, "demo.owner");
  const initial = await read(
    context,
    `/api/v1/catalog/parties?entity_id=${owner.entities[0].id}`,
  );
  const record = initial.items[0];
  expect(record).toBeTruthy();
  const writes: string[] = [],
    errors: string[] = [];
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      request.url().includes("/api/v1/catalog/")
    )
      writes.push(request.url());
  });
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await nav(page, "往来单位");
  const list = page.getByRole("region", { name: "基础资料列表" });
  await expect(
    list.getByRole("button", { name: "列设置", exact: true }),
  ).toBeEnabled();
  await list.getByLabel("查询资料").fill(record.code);
  await list.getByLabel("查询资料").press("Enter");
  const recordButton = list.locator(
    `button[data-catalog-record="${record.id}"]`,
  );
  await expect(recordButton).toBeVisible();
  await list.getByRole("button", { name: "列设置", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "往来单位列表列设置" });
  await expect(
    dialog.getByLabel("名称 / 编码 显示", { exact: true }),
  ).toBeDisabled();
  await expect(dialog.getByLabel("状态 显示", { exact: true })).toBeChecked();
  await dialog.getByLabel("资质有效期 显示", { exact: true }).uncheck();
  await dialog.getByLabel("默认收发货地址 显示", { exact: true }).check();
  await dialog.getByLabel("默认收发货地址 换行", { exact: true }).check();
  await dialog.getByLabel("默认收发货地址 列宽", { exact: true }).fill("50");
  await dialog.getByRole("button", { name: "保存并应用" }).click();
  await expect(dialog.getByRole("alert")).toContainText("列宽须为 80–640");
  await dialog.getByLabel("默认收发货地址 列宽", { exact: true }).fill("320");
  await dragColumn(dialog, "delivery_address", "identity");
  await expect(dialog.locator("tbody tr").first()).toHaveAttribute(
    "data-list-column",
    "delivery_address",
  );
  await dialog
    .getByRole("button", { name: "默认收发货地址 后移", exact: true })
    .click();
  await expect(dialog.locator("tbody tr").nth(1)).toHaveAttribute(
    "data-list-column",
    "delivery_address",
  );
  await dialog
    .getByRole("button", { name: "默认收发货地址 前移", exact: true })
    .click();
  await dialog.getByRole("button", { name: "保存并应用" }).click();
  await expect(dialog).not.toBeVisible();
  await expect(list.getByRole("columnheader").first()).toHaveText(
    "默认收发货地址",
  );
  await expect(
    list.getByRole("columnheader", { name: "资质有效期", exact: true }),
  ).toHaveCount(0);
  const width = (await list
    .getByRole("columnheader", { name: "默认收发货地址", exact: true })
    .boundingBox())!.width;
  expect(width).toBeGreaterThanOrEqual(319);
  expect(width).toBeLessThanOrEqual(321);
  await expect(list.getByLabel("查询资料")).toHaveValue(record.code);
  await list.getByLabel("查询资料").press("ArrowDown");
  await expect(recordButton).toBeFocused();
  await recordButton.press("Enter");
  await page
    .getByRole("region", { name: "资料详情", exact: true })
    .getByRole("button", { name: "← 返回资料列表" })
    .click();
  await expect(recordButton).toBeFocused();
  await expect(list.getByRole("columnheader").first()).toHaveText(
    "默认收发货地址",
  );

  await list.getByRole("button", { name: "列设置", exact: true }).click();
  await dialog.getByLabel("默认收发货地址 列宽", { exact: true }).fill("400");
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await list.getByRole("button", { name: "列设置", exact: true }).click();
  await expect(
    dialog.getByLabel("默认收发货地址 列宽", { exact: true }),
  ).toHaveValue("320");
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await page.reload();
  await nav(page, "往来单位");
  await expect(list.getByRole("columnheader").first()).toHaveText(
    "默认收发货地址",
  );
  await context.clearCookies();
  const checker = await wholesale(context, "demo.quality");
  expect(checker.principal_id).not.toBe(owner.principal_id);
  await page.reload();
  await nav(page, "往来单位");
  await expect(list.getByRole("columnheader").first()).toHaveText(
    "名称 / 编码",
  );
  await expect(
    list.getByRole("columnheader", { name: "默认收发货地址", exact: true }),
  ).toHaveCount(0);
  await context.clearCookies();
  await wholesale(context, "demo.owner");
  await page.reload();
  await nav(page, "往来单位");
  await expect(list.getByRole("columnheader").first()).toHaveText(
    "默认收发货地址",
  );
  await list.getByLabel("业务身份").selectOption("CUSTOMER");
  await expect(list.getByRole("columnheader").first()).toHaveText(
    "默认收发货地址",
  );
  await list.getByRole("button", { name: "列设置", exact: true }).click();
  await dialog.getByRole("button", { name: "恢复默认列" }).click();
  await dialog.getByRole("button", { name: "保存并应用" }).click();
  await expect(list.getByRole("columnheader").first()).toHaveText(
    "名称 / 编码",
  );
  await expect(
    list.getByRole("columnheader", { name: "资质有效期", exact: true }),
  ).toBeVisible();
  expect(writes).toEqual([]);
  expect(errors).toEqual([]);
});

test("product columns render additional fields, preserve permission states and recover failed browser storage", async ({
  page,
  context,
}) => {
  await wholesale(context, "demo.owner");
  const initial = await read(
    context,
    "/api/v1/catalog/master/products?limit=1",
  );
  const original = initial.items[0];
  expect(original).toBeTruthy();
  await page.route("**/api/v1/catalog/master/products?*", async (route) => {
    await route.fulfill({
      json: {
        data: {
          ...initial,
          total: 1,
          has_more: false,
          items: [
            {
              ...original,
              values: {
                ...original.values,
                name: "长名称列宽验收商品长名称列宽验收商品",
                generic_name: "通用名列展示验收",
                manufacturer: "不应展示的受限内容",
              },
              states: {
                ...original.states,
                generic_name: "VALUE",
                manufacturer: "NO_PERMISSION",
              },
            },
          ],
        },
      },
    });
  });
  await page.goto("/");
  await nav(page, "商品档案");
  const list = page.locator(".master-browser");
  await expect(
    list.getByRole("button", { name: "列设置", exact: true }),
  ).toBeEnabled();
  const table = list.locator(".master-list-table");
  await expect(
    table.locator('tbody td[data-list-column="manufacturer"]'),
  ).toHaveText("无查看权限");
  await expect(table).not.toContainText("不应展示的受限内容");
  await list.getByRole("button", { name: "列设置", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "商品列表列设置" });
  const nameField = dialog.locator('[data-list-column="name"]');
  await expect(
    nameField.getByRole("checkbox", { name: "规范名称 显示", exact: true }),
  ).toBeDisabled();
  await nameField.getByLabel("规范名称 列宽", { exact: true }).fill("210");
  await nameField.getByLabel("规范名称 换行", { exact: true }).check();
  await dialog.getByLabel("生产企业 显示", { exact: true }).uncheck();
  await dialog.getByLabel("查找列表字段").fill("通用");
  const extra = dialog.locator('[data-list-column="generic_name"]');
  await expect(extra).toBeVisible();
  await extra
    .getByRole("checkbox", { name: "通用名称 显示", exact: true })
    .check();
  await dialog.getByLabel("查找列表字段").fill("");
  await dragColumn(dialog, "generic_name", "specification");
  await expect(dialog.locator("tbody tr").nth(3)).toHaveAttribute(
    "data-list-column",
    "generic_name",
  );
  await dialog.getByRole("button", { name: "保存并应用" }).click();
  await expect(dialog).not.toBeVisible();
  await expect(
    table.locator('tbody td[data-list-column="generic_name"]'),
  ).toHaveText("通用名列展示验收");
  await expect(
    table.locator('thead th[data-list-column="manufacturer"]'),
  ).toHaveCount(0);
  const nameCell = table.locator('tbody td[data-list-column="name"]');
  expect((await nameCell.boundingBox())!.width).toBeCloseTo(210, 0);
  await expect(nameCell).toHaveClass(/catalog-cell-wrap/);
  await list.getByRole("button", { name: "列设置", exact: true }).click();
  await dialog.getByLabel("规范名称 列宽", { exact: true }).fill("300");
  await page.evaluate(() => {
    const originalSet = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (key.startsWith("medinv.catalog-columns.v1:"))
        throw new DOMException("Test quota", "QuotaExceededError");
      return originalSet.call(this, key, value);
    };
  });
  await dialog.getByRole("button", { name: "保存并应用" }).click();
  await expect(dialog.getByRole("alert")).toContainText("当前列表未改变");
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  expect((await nameCell.boundingBox())!.width).toBeCloseTo(210, 0);
  await page.reload();
  await nav(page, "商品档案");
  await expect(nameCell).toHaveClass(/catalog-cell-wrap/);
  expect((await nameCell.boundingBox())!.width).toBeCloseTo(210, 0);
  await page.screenshot({
    path: "../../outputs/catalog-columns/商品列表.png",
    fullPage: true,
  });
  await list.getByRole("button", { name: "列设置", exact: true }).click();
  await page.screenshot({
    path: "../../outputs/catalog-columns/商品列设置.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    dialog.getByRole("button", { name: "保存并应用" }),
  ).toBeInViewport();
  const saveBox = (await dialog
    .getByRole("button", { name: "保存并应用" })
    .boundingBox())!;
  expect(saveBox.y + saveBox.height).toBeLessThanOrEqual(844);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "../../outputs/catalog-columns/窄屏列设置.png",
    fullPage: true,
  });
});
