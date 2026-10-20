import { test, expect, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { headers } from "./helpers";
import {
  wholesale,
  stockedCustomer,
  command,
  read,
  nav,
  origin,
} from "./business-helpers";

async function confirm(page: Page, reason: string) {
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("操作说明").fill(reason);
  await dialog.getByRole("button", { name: "确认保存", exact: true }).click();
  await expect(dialog).not.toBeVisible();
}

test("same warehouse partial move independently reviewed, stock conserved and destination available", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(120000);
  const owner = await wholesale(context, "demo.owner"),
    reviewer = await browser.newContext();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    const checker = await wholesale(reviewer, "demo.quality"),
      quality = await reviewer.newPage();
    quality.on("pageerror", (e) => errors.push(e.message));
    const f = await stockedCustomer(context, owner, reviewer, checker);
    const wh = await read(context, `/api/v1/warehouses/${f.warehouse}`);
    const target = await command(
      context,
      owner,
      `/api/v1/warehouses/${f.warehouse}/locations`,
      {
        client_operation_id: randomUUID(),
        expected_version: wh.warehouse.version,
        code: "MOVE-" + f.suffix,
        name: "同仓移库目标货架",
        reason: "移库流程测试",
      },
    );
    const path = `/api/v1/inventory/availability?entity_id=${f.entity}&warehouse_id=${f.warehouse}&sku_id=${f.product.sku_id}`;
    const before = await read(context, path);
    await page.goto("/");
    await nav(page, "同仓移库");
    await page.getByRole("button", { name: "新建移库单", exact: true }).click();
    await page.getByLabel("商品 / 批号", { exact: true }).fill(f.name);
    await page
      .getByRole("button", { name: "查找移库库存", exact: true })
      .click();
    await page
      .getByRole("row")
      .filter({ hasText: f.name })
      .getByRole("button", { name: "选择批次" })
      .click();
    await page
      .getByRole("combobox", { name: "目标库位", exact: true })
      .selectOption(target.id);
    await page.getByLabel("移动数量", { exact: true }).fill("4");
    await page.getByLabel("实物批号", { exact: true }).fill(f.lot);
    await page.getByLabel("移库原因").fill("按实际货架分布移动四件");
    await page
      .getByRole("button", { name: "保存移库申请", exact: true })
      .click();
    await expect(page.locator(".panel-heading .el-tag")).toHaveText("待审核");
    const number = await page.locator(".document-number").innerText();
    await expect(
      page.getByRole("button", { name: "独立审核移库", exact: true }),
    ).toHaveCount(0);
    expect((await read(context, path)).items).toEqual(before.items);
    await quality.goto("/");
    await nav(quality, "同仓移库");
    await quality
      .getByRole("row")
      .filter({ hasText: number })
      .getByRole("button", { name: "查看移库单" })
      .click();
    await quality
      .getByRole("button", { name: "独立审核移库", exact: true })
      .click();
    await confirm(quality, "独立核对来源批次与目标库位");
    await expect(quality.locator(".panel-heading .el-tag")).toHaveText(
      "待移动",
    );
    await page.getByRole("button", { name: "刷新移库", exact: true }).click();
    await page
      .getByRole("button", { name: "确认库位移动", exact: true })
      .click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("核对实物批号").fill(f.lot);
    await dialog
      .getByLabel("我已核对实物已移至目标库位，数量与单据一致")
      .check();
    await confirm(page, "确认四件实物已移到指定目标货架");
    await expect(page.locator(".panel-heading .el-tag")).toHaveText("已移动");
    const after = await read(context, path);
    expect(after.items).toHaveLength(2);
    expect(
      after.items.find((r: any) => r.location_id === target.id).on_hand,
    ).toBe("4.000000");
    expect(
      after.items.find((r: any) => r.location_id === target.id).available,
    ).toBe("4.000000");
    expect(
      after.items.find((r: any) => r.balance_id === before.items[0].balance_id)
        .on_hand,
    ).toBe("6.000000");
    const ap = await read(
      context,
      `/api/v1/settlements/items?entity_id=${f.entity}&kind=AP&party_id=${f.party.id}`,
    );
    expect(ap.items[0].outstanding_amount).toBe("130.00");
    await expect(page.locator(".el-message")).toHaveCount(0, { timeout: 8000 });
    await page.screenshot({
      path: "../../artifacts/location-move-posted.png",
      fullPage: true,
      animations: "disabled",
    });
    await page
      .getByRole("button", { name: "返回移库列表", exact: true })
      .click();
    await expect(
      page.getByRole("row").filter({ hasText: number }),
    ).toContainText("已移动");
    expect(errors).toEqual([]);
  } finally {
    await reviewer.close();
  }
});

test("viewer can read location moves but cannot create them via UI or direct HTTP", async ({
  page,
  context,
}) => {
  const current = await wholesale(context, "demo.viewer");
  await page.goto("/");
  await nav(page, "同仓移库");
  await expect(
    page.getByRole("button", { name: "新建移库单", exact: true }),
  ).toHaveCount(0);
  const response = await context.request.post(
    origin + "/api/v1/location-moves",
    {
      headers: { ...headers(current), "Idempotency-Key": randomUUID() },
      data: {
        client_operation_id: randomUUID(),
        entity_id: current.entities[0].id,
        balance_id: randomUUID(),
        destination_location_id: randomUUID(),
        qty: "1",
        lot_no: "TEST",
        reason: "只读拒绝测试",
      },
    },
  );
  expect(response.status()).toBe(403);
});
