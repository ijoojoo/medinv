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

async function save(page: Page, reason: string) {
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("操作说明").fill(reason);
  await dialog.getByRole("button", { name: "保存", exact: true }).click();
  await expect(dialog).not.toBeVisible();
}
async function capture(page: Page, name: string) {
  await expect(page.locator(".el-message")).toHaveCount(0, { timeout: 8000 });
  await expect(page.locator(".el-loading-mask:visible")).toHaveCount(0);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: `../../artifacts/${name}.png`,
    fullPage: true,
    animations: "disabled",
  });
}

test("warehouse setup and transfer through independent review, dispatch and partial/isolated receipts in real UI", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(180000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext();
  const checker = await wholesale(reviewer, "demo.quality");
  const qualityPage = await reviewer.newPage();
  try {
    const f = await stockedCustomer(context, owner, reviewer, checker);
    await page.goto("/");
    await nav(page, "仓库调拨");
    await page
      .getByRole("button", { name: "新增实体仓库", exact: true })
      .click();
    let dialog = page.getByRole("dialog");
    await dialog.getByLabel("仓库编码", { exact: true }).fill("WT-" + f.suffix);
    await dialog
      .getByLabel("仓库名称", { exact: true })
      .fill("验收目的仓-" + f.suffix);
    await dialog.getByLabel("首个库位编码", { exact: true }).fill("DEST-A");
    await dialog.getByRole("button", { name: "保存仓库", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    const options = await read(
      context,
      `/api/v1/transfers/options?entity_id=${f.entity}`,
    );
    const dest = options.warehouses.find(
      (w: any) => w.code === "WT-" + f.suffix,
    );
    expect(dest).toBeTruthy();
    await page.getByRole("button", { name: "新建调拨", exact: true }).click();
    await page
      .getByRole("combobox", { name: "调出仓库", exact: true })
      .selectOption(f.warehouse);
    await page
      .getByRole("combobox", { name: "调入仓库", exact: true })
      .selectOption(dest.id);
    await page.getByLabel("搜索商品或批号").fill(f.name);
    await page.getByRole("button", { name: "查询批次", exact: true }).click();
    await page.getByRole("checkbox", { name: new RegExp(f.name) }).check();
    await page.getByLabel("调拨数量 1", { exact: true }).fill("10");
    const reason = "调拨浏览器验收-" + f.suffix;
    await page.getByLabel("调拨说明", { exact: true }).fill(reason);
    await page
      .getByRole("button", { name: "提交调拨申请", exact: true })
      .click();
    await expect(page.locator(".el-tag")).toHaveText("待审核");
    await expect(
      page.getByRole("button", { name: "独立审核调拨", exact: true }),
    ).toHaveCount(0);
    const list = await read(context, `/api/v1/transfers?entity_id=${f.entity}`);
    const doc = list.items.find((r: any) => r.reason === reason);
    await qualityPage.goto("/");
    await nav(qualityPage, "仓库调拨");
    await qualityPage
      .getByRole("row")
      .filter({ hasText: doc.number })
      .getByRole("button", { name: "调拨详情" })
      .click();
    await qualityPage
      .getByRole("button", { name: "独立审核调拨", exact: true })
      .click();
    await save(qualityPage, "核对来源目的和批次数量");
    await expect(qualityPage.locator(".el-tag")).toHaveText("待发出");
    await expect(
      qualityPage.getByRole("button", { name: "确认调拨发出", exact: true }),
    ).toHaveCount(0);
    await page
      .getByRole("button", { name: "刷新调拨详情", exact: true })
      .click();
    await page
      .getByRole("button", { name: "确认调拨发出", exact: true })
      .click();
    await save(page, "实物装运出库");
    await expect(page.locator(".el-tag")).toHaveText("在途待收货");
    await expect(
      page.getByRole("button", { name: "取消调拨", exact: true }),
    ).toHaveCount(0);
    let data = await read(context, `/api/v1/transfers/${doc.id}`);
    const transit = await read(
      context,
      `/api/v1/inventory/availability?entity_id=${f.entity}&warehouse_id=${data.document.transit_warehouse_id}&sku_id=${f.product.sku_id}`,
    );
    expect(transit.items[0].available).toBe("0.000000");
    expect(transit.items[0].ineligibility_codes).toContain("IN_TRANSIT");
    const line = data.lines[0];
    for (const [qty, quality] of [
      ["8", "QUALIFIED"],
      ["2", "QUARANTINE"],
    ]) {
      await page
        .getByRole("button", { name: "登记本次收货", exact: true })
        .click();
      dialog = page.getByRole("dialog");
      await dialog.getByLabel("本次收货数量 1", { exact: true }).fill(qty!);
      await dialog.getByLabel("实物批号 1", { exact: true }).fill(f.lot);
      await dialog
        .getByLabel("收货质量 1", { exact: true })
        .selectOption(quality!);
      await save(
        page,
        qty === "8"
          ? "第一车实收8，剩余2仍在途"
          : "第二车实收2，外观待进一步核验，隔离存放",
      );
      await expect(page.locator(".el-tag")).toHaveText(
        qty === "8" ? "部分收货" : "已收齐",
      );
      data = await read(context, `/api/v1/transfers/${doc.id}`);
      expect(data.lines[0].remaining_qty).toBe(
        qty === "8" ? "2.000000" : "0.000000",
      );
      await capture(
        page,
        qty === "8" ? "transfer-partial" : "transfer-received",
      );
    }
    expect(data.receipts).toHaveLength(2);
    const duplicate = await context.request.post(
      origin + `/api/v1/transfers/${doc.id}/receive`,
      {
        headers: { ...headers(owner), "Idempotency-Key": randomUUID() },
        data: {
          client_operation_id: randomUUID(),
          expected_version: data.document.version,
          reason: "已收齐不得再收",
          lines: [
            {
              line_id: line.id,
              qty: "1",
              lot_no: f.lot,
              quality_status: "QUALIFIED",
            },
          ],
        },
      },
    );
    expect(duplicate.status()).toBe(409);
    const stock = await read(
      context,
      `/api/v1/inventory/availability?entity_id=${f.entity}&warehouse_id=${dest.id}&sku_id=${f.product.sku_id}`,
    );
    expect(stock.items.find((r: any) => r.eligible).available).toBe("8.000000");
    expect(stock.items.find((r: any) => !r.eligible).on_hand).toBe("2.000000");
    // Received stock is usable by the existing POS allocation path.
    const sale = await command(context, owner, "/api/v1/retail/sales", {
      client_operation_id: randomUUID(),
      entity_id: f.entity,
      warehouse_id: dest.id,
      lines: [
        {
          sku_id: f.product.sku_id,
          qty: "8",
          unit_price: "20",
          tax_rate: "0.13",
        },
      ],
    });
    await command(context, owner, `/api/v1/retail/sales/${sale.id}/cancel`, {
      expected_version: "1",
      reason: "释放验收预占",
    });
  } finally {
    await reviewer.close();
  }
});

test("viewer can read transfers but cannot set up warehouses or create transfer requests", async ({
  page,
  context,
}) => {
  await wholesale(context, "demo.viewer");
  await page.goto("/");
  await nav(page, "仓库调拨");
  await expect(
    page.getByRole("button", { name: "刷新调拨", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "新增实体仓库", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "新建调拨", exact: true }),
  ).toHaveCount(0);
});
