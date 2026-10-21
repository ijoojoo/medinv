import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { headers } from "./helpers";
import {
  wholesale,
  stockedCustomer,
  read,
  nav,
  origin,
} from "./business-helpers";

test("warehouse management creates a receipt location and renames it without moving posted stock", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(120000);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext();
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    const suffix = Date.now().toString(36),
      warehouseName = "多库位验收仓-" + suffix;
    await page.goto("/");
    await nav(page, "仓库与库位");
    await page
      .getByRole("button", { name: "新增实体仓库", exact: true })
      .click();
    let dialog = page.getByRole("dialog");
    await dialog.getByLabel("仓库编码", { exact: true }).fill("WL-" + suffix);
    await dialog.getByLabel("仓库名称", { exact: true }).fill(warehouseName);
    await dialog.getByLabel("首个库位编码").fill("DEFAULT");
    await dialog.getByRole("button", { name: "保存仓库", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await expect(
      page.getByRole("heading", { name: warehouseName, exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "展开工具箱", exact: true }).click();
    await page.getByRole("button", { name: "新增库位", exact: true }).click();
    dialog = page.getByRole("dialog");
    await dialog.getByLabel("库位编码", { exact: true }).fill("RECEIVE-02");
    await dialog.getByLabel("库位名称", { exact: true }).fill("常温收货二号位");
    await dialog.getByLabel("维护原因").fill("新增采购收货位置");
    await dialog.getByRole("button", { name: "保存库位", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    const warehouse = (
      await read(
        context,
        `/api/v1/warehouses?entity_id=${owner.entities[0].id}&search=${encodeURIComponent(warehouseName)}`,
      )
    ).items[0];
    let detail = await read(context, `/api/v1/warehouses/${warehouse.id}`);
    const location = detail.locations.find((l: any) => l.code === "RECEIVE-02");
    expect(location.name).toBe("常温收货二号位");
    const f = await stockedCustomer(context, owner, reviewer, checker, {
      warehouse: warehouse.id,
      location: location.id,
    });
    const availability = `/api/v1/inventory/availability?entity_id=${f.entity}&warehouse_id=${warehouse.id}&sku_id=${f.product.sku_id}`;
    const before = await read(context, availability);
    expect(before.items).toHaveLength(1);
    expect(before.items[0].on_hand).toBe("10.000000");
    await page
      .getByRole("row")
      .filter({ hasText: "RECEIVE-02" })
      .getByRole("button", { name: "选择库位", exact: true })
      .click();
    await page
      .getByRole("button", { name: "修改库位名称", exact: true })
      .click();
    dialog = page.getByRole("dialog");
    await dialog.getByLabel("库位名称", { exact: true }).fill("常温采购收货区");
    await dialog.getByLabel("维护原因").fill("现场标牌统一命名");
    await dialog.getByRole("button", { name: "保存名称", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await page
      .getByRole("button", { name: "修改仓库名称", exact: true })
      .click();
    dialog = page.getByRole("dialog");
    await dialog
      .getByLabel("仓库名称", { exact: true })
      .fill(warehouseName + "-中心");
    await dialog.getByLabel("维护原因").fill("仓库现场命名更正");
    await dialog.getByRole("button", { name: "保存名称", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    detail = await read(context, `/api/v1/warehouses/${warehouse.id}`);
    expect(detail.warehouse.version).toBe("4");
    expect(detail.locations.find((l: any) => l.id === location.id).code).toBe(
      "RECEIVE-02",
    );
    expect(detail.history).toHaveLength(4);
    const after = await read(context, availability);
    expect(after.items[0].location_id).toBe(location.id);
    expect(after.items[0].location_code).toBe("RECEIVE-02");
    expect(after.items[0].location_name).toBe("常温采购收货区");
    expect(after.items[0].balance_id).toBe(before.items[0].balance_id);
    expect(after.items[0].on_hand).toBe(before.items[0].on_hand);
    const options = await read(
      context,
      `/api/v1/purchasing/options?entity_id=${f.entity}`,
    );
    expect(options.locations.find((l: any) => l.id === location.id).name).toBe(
      "常温采购收货区",
    );
    await page.getByRole("button", { name: "变更记录", exact: true }).click();
    await expect(
      page.getByText("常温收货二号位 → 常温采购收货区", { exact: false }),
    ).toBeVisible();
    await expect(page.locator(".el-message")).toHaveCount(0, { timeout: 8000 });
    await page.screenshot({
      path: "../../artifacts/warehouse-locations.png",
      fullPage: true,
      animations: "disabled",
    });
    await page.keyboard.press("Escape");
    await page
      .getByRole("button", { name: "返回仓库列表", exact: true })
      .click();
    await page.getByLabel("仓库编码或名称").fill("WL-" + suffix);
    await page.getByRole("button", { name: "查询仓库", exact: true }).click();
    await expect(
      page.getByRole("row").filter({ hasText: warehouseName + "-中心" }),
    ).toBeVisible();
    await nav(page, "库存查询");
    await page
      .getByRole("combobox", { name: "库存仓库", exact: true })
      .press("Enter");
    await page
      .getByRole("option", { name: warehouseName + "-中心", exact: true })
      .click();
    await page
      .getByRole("combobox", { name: "库存商品", exact: true })
      .fill(f.name);
    await page
      .getByRole("option", { name: f.name + " · SS-" + f.suffix, exact: true })
      .click();
    await page.getByRole("button", { name: "查询库存", exact: true }).click();
    await expect(
      page.getByRole("row").filter({ hasText: "RECEIVE-02" }),
    ).toContainText("常温采购收货区");
    await page.screenshot({
      path: "../../artifacts/warehouse-stock-location.png",
      fullPage: true,
      animations: "disabled",
    });
    expect(errors).toEqual([]);
  } finally {
    await reviewer.close();
  }
});

test("viewer reads warehouse locations but cannot maintain them via UI or HTTP", async ({
  page,
  context,
}) => {
  const viewer = await wholesale(context, "demo.viewer");
  await page.goto("/");
  await nav(page, "仓库与库位");
  await expect(
    page.getByRole("button", { name: "新增实体仓库", exact: true }),
  ).toBeDisabled();
  await page
    .getByRole("button", { name: "管理库位", exact: true })
    .first()
    .click();
  await page.getByRole("button", { name: "展开工具箱", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "新增库位", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "修改仓库名称", exact: true }),
  ).toBeDisabled();
  const warehouse = (
    await read(context, `/api/v1/warehouses?entity_id=${viewer.entities[0].id}`)
  ).items[0];
  const response = await context.request.post(
    origin + `/api/v1/warehouses/${warehouse.id}/rename`,
    {
      headers: { ...headers(viewer), "Idempotency-Key": randomUUID() },
      data: {
        expected_version: warehouse.version,
        name: "不可越权修改",
        reason: "角色边界验证",
      },
    },
  );
  expect(response.status()).toBe(403);
});
