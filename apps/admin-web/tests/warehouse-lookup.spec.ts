import { test, expect, type Page, type BrowserContext } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { mkdir } from "node:fs/promises";
import {
  command,
  read,
  wholesale,
  nav,
  chooseWarehouse,
} from "./business-helpers";

const editor = (page: Page) => page.locator(".document-editor:visible");
const popup = (page: Page) => page.locator(".warehouse-lookup-popover:visible");
async function saveDraft(page: Page, context: BrowserContext) {
  await editor(page)
    .getByRole("button", { name: "保存草稿", exact: true })
    .click();
  await expect(editor(page).locator(".editor-actions")).toContainText(
    "草稿已保存",
  );
  return read(
    context,
    `/api/v1/document-drafts/${new URL(page.url()).pathname.split("/").pop()}`,
  );
}
async function openEditor(page: Page, sale = false) {
  await page.goto("/");
  await nav(page, sale ? "销售出库" : "采购入库");
  await page
    .getByRole("button", {
      name: sale ? "新建销售单" : "新建采购订单",
      exact: true,
    })
    .click();
  return editor(page).getByRole("combobox", {
    name: sale ? "发货仓库" : "收货仓库",
    exact: true,
  });
}

test("purchase warehouse lookup pages same names, searches codes, confirms explicitly and restores saved identity", async ({
  page,
  context,
}) => {
  test.setTimeout(90000);
  const owner = await wholesale(context, "demo.owner"),
    suffix = Date.now().toString(36),
    name = `检索验收仓-${suffix}`;
  const warehouses: { id: string; code: string }[] = [];
  for (let n = 0; n < 22; n++) {
    const code = `WL-${suffix}-${String(n).padStart(2, "0")}`;
    const created = await command(context, owner, "/api/v1/warehouses", {
      client_operation_id: randomUUID(),
      entity_id: owner.entities[0].id,
      code,
      name,
      location_code: "DEFAULT",
    });
    warehouses.push({ id: created.id, code });
  }
  const faults: string[] = [],
    optionLoads: string[] = [];
  let trackOptions = false;
  page.on("pageerror", (e) => faults.push(e.message));
  page.on("request", (r) => {
    if (
      trackOptions &&
      /\/(purchasing|selling)\/options\?/.test(r.url()) &&
      r.url().includes("include_parties=false")
    )
      optionLoads.push(r.url());
  });
  await page.goto("/");
  await nav(page, "采购入库");
  // The list's own warehouse filter still loads its choices. Count only
  // additional requests caused by opening and editing the document.
  await expect(
    page
      .locator('select[aria-label="仓库"]')
      .locator(`option[value="${warehouses[21]!.id}"]`),
  ).toHaveCount(1);
  trackOptions = true;
  await page.getByRole("button", { name: "新建采购订单", exact: true }).click();
  const input = editor(page).getByRole("combobox", {
    name: "收货仓库",
    exact: true,
  });
  await input.fill(name);
  await expect(popup(page).getByRole("option")).toHaveCount(20);
  await expect(popup(page).getByRole("option").first()).toContainText(
    warehouses[0]!.code,
  );
  await popup(page)
    .getByRole("button", { name: "下一页", exact: true })
    .click();
  await expect(popup(page).getByRole("option")).toHaveCount(2);
  await expect(popup(page).getByRole("option").last()).toContainText(
    warehouses[21]!.code,
  );
  await popup(page).getByRole("option").last().click();
  expect((await saveDraft(page, context)).content.warehouse_id).toBe(
    warehouses[21]!.id,
  );
  // The selected warehouse is outside page one. Enter after refocusing must
  // preserve it rather than silently replace it with the first candidate.
  await input.focus();
  await expect(popup(page).getByRole("option")).toHaveCount(20);
  await input.press("Enter");
  await expect(popup(page)).toHaveCount(0);
  expect((await saveDraft(page, context)).content.warehouse_id).toBe(
    warehouses[21]!.id,
  );
  await input.fill(warehouses[20]!.code.toLowerCase());
  await input.press("Enter"); // Flushes the debounce before choosing the first result.
  await expect(input).toHaveValue(name);
  await expect(popup(page)).toHaveCount(0);
  await input.press("Enter");
  await expect(
    editor(page).getByLabel("草稿备注", { exact: true }),
  ).toBeFocused();
  expect((await saveDraft(page, context)).content.warehouse_id).toBe(
    warehouses[20]!.id,
  );
  expect(optionLoads).toEqual([]);
  trackOptions = false;
  await page.reload();
  await expect(input).toHaveValue(name);
  expect(
    await input.evaluate((el: HTMLInputElement) => el.checkValidity()),
  ).toBe(true);
  await input.fill("不存在的仓库");
  await expect(popup(page)).toContainText("未找到匹配仓库");
  await input.press("Escape");
  await input.press("Enter");
  await expect(input).toBeFocused();
  expect((await saveDraft(page, context)).content.warehouse_id).toBe("");
  await chooseWarehouse(input, warehouses[0]!.id);
  await input.press("Shift+Enter");
  await expect(
    editor(page).getByRole("combobox", { name: "供应商", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Escape");
  await input.fill(name);
  await expect(popup(page).getByRole("option")).toHaveCount(20);
  await mkdir("../../outputs/warehouse-lookup", { recursive: true });
  await page.screenshot({
    path: "../../outputs/warehouse-lookup/采购仓库检索.png",
    animations: "disabled",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  const bounds = await popup(page).boundingBox();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "../../outputs/warehouse-lookup/采购仓库检索-窄屏.png",
    animations: "disabled",
  });
  expect(optionLoads).toEqual([]);
  expect(faults).toEqual([]);
});

test("sale warehouse lookup preserves input on failure, protects IME and ignores a cancelled delayed confirmation", async ({
  page,
  context,
}) => {
  const owner = await wholesale(context, "demo.owner");
  const warehouse = (
    await read(
      context,
      `/api/v1/warehouses/lookup?entity_id=${owner.entities[0].id}&channel=sale`,
    )
  ).items[0];
  const input = await openEditor(page, true);
  await chooseWarehouse(input, warehouse.id);
  let fail = true,
    release = () => {},
    reached = () => {},
    handled = () => {};
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  const arrived = new Promise<void>((resolve) => {
    reached = resolve;
  });
  const completed = new Promise<void>((resolve) => {
    handled = resolve;
  });
  await page.route("**/api/v1/warehouses/lookup?**", async (route) => {
    const query = new URL(route.request().url()).searchParams.get("search");
    if (query === "取消") {
      reached();
      await pending;
      await route.fulfill({
        json: { data: { items: [warehouse], has_more: false } },
      });
      handled();
      return;
    }
    if (query === warehouse.code && fail) {
      fail = false;
      return route.fulfill({
        status: 503,
        json: { error: { code: "TEST_FAILURE", message: "暂时不可用" } },
      });
    }
    await route.continue();
  });
  try {
    await input.fill(warehouse.code);
    await expect(popup(page).getByRole("alert")).toContainText("输入已保留");
    await expect(input).toHaveValue(warehouse.code);
    await popup(page)
      .getByRole("button", { name: "重试", exact: true })
      .click();
    await expect(popup(page).getByRole("option")).toHaveCount(1);
    await input.focus();
    await input.dispatchEvent("keydown", { key: "Enter", isComposing: true });
    await expect(input).toHaveValue(warehouse.code);
    await expect(popup(page)).toBeVisible();
    await input.press("Enter");
    await expect(input).toHaveValue(warehouse.name);
    await input.fill("取消");
    await input.press("Enter");
    await arrived;
    await input.press("Escape");
    release();
    await completed;
    await expect(input).toHaveValue("取消");
    await expect(popup(page)).toHaveCount(0);
    expect((await saveDraft(page, context)).content.warehouse_id).toBe("");
    await chooseWarehouse(input, warehouse.id);
    await input.press("Tab");
    await expect(popup(page)).toHaveCount(0);
    expect((await saveDraft(page, context)).content.warehouse_id).toBe(
      warehouse.id,
    );
  } finally {
    release();
  }
});

for (const state of ["archived", "missing", "failed"]) {
  test(`restoring a ${state} warehouse blocks entry without destroying the saved identity`, async ({
    page,
    context,
  }) => {
    const owner = await wholesale(context, "demo.owner");
    const warehouse = (
      await read(
        context,
        `/api/v1/warehouses/lookup?entity_id=${owner.entities[0].id}&channel=purchase`,
      )
    ).items[0];
    const input = await openEditor(page);
    await chooseWarehouse(input, warehouse.id);
    const saved = await saveDraft(page, context);
    await page.route("**/api/v1/warehouses/lookup?**", async (route) => {
      if (!new URL(route.request().url()).searchParams.has("warehouse_id"))
        return route.continue();
      if (state === "failed") return route.fulfill({ status: 503, json: {} });
      return route.fulfill({
        json: {
          data: {
            items:
              state === "missing" ? [] : [{ ...warehouse, eligible: false }],
            has_more: false,
          },
        },
      });
    });
    await page.reload();
    await expect(input).toHaveValue(
      state === "archived"
        ? warehouse.name
        : state === "missing"
          ? "所选仓库不可用，请重新选择"
          : "所选仓库读取失败，请重试",
    );
    expect(
      await input.evaluate((el: HTMLInputElement) => el.checkValidity()),
    ).toBe(false);
    const current = await read(context, `/api/v1/document-drafts/${saved.id}`);
    expect(current.content.warehouse_id).toBe(warehouse.id);
    expect(current.version).toBe(saved.version);
    await page.unroute("**/api/v1/warehouses/lookup?**");
    await chooseWarehouse(input, warehouse.id);
    expect(
      await input.evaluate((el: HTMLInputElement) => el.checkValidity()),
    ).toBe(true);
  });
}
