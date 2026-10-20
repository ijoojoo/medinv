import { test, expect } from "@playwright/test";
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

test("purchase remainder closure preserves arrivals, pending inspection and later posting", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(120000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext(),
    viewerContext = await browser.newContext();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    const f = await stockedCustomer(context, owner, reviewer, checker);
    const created = await command(context, owner, "/api/v1/purchase-orders", {
      entity_id: f.entity,
      supplier_id: f.party.id,
      warehouse_id: f.warehouse,
      lines: [
        {
          sku_id: f.product.sku_id,
          qty: "10",
          unit_price: "13",
          tax_rate: "0.13",
        },
      ],
    });
    await command(
      reviewer,
      checker,
      `/api/v1/purchase-orders/${created.id}/approve`,
      { expected_version: "1", reason: "软件验收采购" },
    );
    const order = await read(context, `/api/v1/purchase-orders/${created.id}`);
    const choices = await read(
      context,
      `/api/v1/purchasing/options?entity_id=${f.entity}`,
    );
    const location = choices.locations.find(
      (l: any) => l.warehouse_id === f.warehouse,
    );
    const arrival = await command(
      context,
      owner,
      `/api/v1/purchase-orders/${created.id}/receipts`,
      {
        expected_version: order.order.version,
        shipment_ref: "CLOSE-" + f.suffix,
        transport_note: "部分到货测试",
        lines: [
          {
            order_line_id: order.lines[0].id,
            location_id: location.id,
            qty: "4",
            lot_no: "CLOSE-" + f.suffix,
            mfg_date: `${f.year}-01-01`,
            expiry_date: `${f.year + 1}-12-31`,
          },
        ],
      },
    );
    let detail = await read(context, `/api/v1/purchase-orders/${created.id}`);
    await command(
      reviewer,
      checker,
      `/api/v1/receipts/${arrival.id}/inspections`,
      {
        expected_version: "1",
        note: "先验收部分到货，暂未入库",
        lines: [
          {
            receipt_line_id: detail.receipts[0].lines[0].id,
            qualified_qty: "2",
            rejected_qty: "0",
          },
        ],
      },
    );
    const quality = await reviewer.newPage();
    quality.on("pageerror", (e) => errors.push(e.message));
    await quality.goto("/");
    await nav(quality, "采购入库");
    await quality
      .getByRole("row")
      .filter({ hasText: order.order.number })
      .getByRole("button", { name: "查看采购单" })
      .click();
    await expect(
      quality.getByRole("button", { name: "关闭未收余量", exact: true }),
    ).toHaveCount(0);

    await page.goto("/");
    await nav(page, "采购入库");
    await page
      .getByRole("row")
      .filter({ hasText: order.order.number })
      .getByRole("button", { name: "查看采购单" })
      .click();
    await page
      .getByRole("button", { name: "关闭未收余量", exact: true })
      .click();
    let dialog = page.getByRole("dialog");
    await expect(
      dialog.getByRole("row").filter({ hasText: f.name }),
    ).toContainText("6 个");
    await expect(dialog).toContainText("已到货的待验");
    await dialog
      .getByLabel("操作说明")
      .fill("供应商确认剩余 6 个不再交付，已收 4 个继续处理");
    await dialog
      .getByRole("button", { name: "确认关闭余量", exact: true })
      .click();
    await expect(dialog).not.toBeVisible();
    await expect(
      page.getByRole("alert").filter({ hasText: "未到货余量已关闭" }),
    ).toContainText("未到货余量已关闭");
    await expect(
      page.getByRole("button", { name: "登记到货", exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "关闭未收余量", exact: true }),
    ).toHaveCount(0);
    await expect(page.getByLabel("采购关闭记录")).toContainText(
      "剩余 6 个不再交付",
    );
    detail = await read(context, `/api/v1/purchase-orders/${created.id}`);
    expect(detail.lines[0].received_qty).toBe("4.000000");
    expect(detail.lines[0].closed_qty).toBe("6.000000");
    expect(detail.lines[0].remaining_qty).toBe("0.000000");
    await expect(page.locator(".el-message")).toHaveCount(0, { timeout: 8000 });
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: "../../artifacts/purchase-closed-pending.png",
      fullPage: true,
      animations: "disabled",
    });

    await quality
      .getByRole("button", { name: "刷新单据", exact: true })
      .click();
    await quality
      .getByRole("button", { name: "登记验收", exact: true })
      .click();
    dialog = quality.getByRole("dialog");
    await dialog.getByLabel("本次合格数量").fill("2");
    await dialog.getByLabel("验收依据与结论").fill("关闭后完成原到货余下验收");
    await dialog.getByRole("button", { name: "保存验收记录" }).click();
    await expect(dialog).not.toBeVisible();
    for (let i = 0; i < 2; i++) {
      await quality
        .getByRole("button", { name: "合格入库", exact: true })
        .first()
        .click();
      await quality
        .getByRole("dialog")
        .getByRole("button", { name: "确认入库", exact: true })
        .click();
      await expect(quality.getByRole("dialog")).not.toBeVisible();
    }
    await expect(
      quality.getByRole("button", { name: "登记验收", exact: true }),
    ).toHaveCount(0);
    await expect(
      quality.getByRole("alert").filter({ hasText: "未到货余量已关闭" }),
    ).toContainText("未到货余量已关闭");
    await expect(quality.locator(".el-message")).toHaveCount(0, {
      timeout: 8000,
    });
    await quality.evaluate(() => window.scrollTo(0, 0));
    await quality.screenshot({
      path: "../../artifacts/purchase-closed-posted.png",
      fullPage: true,
      animations: "disabled",
    });
    detail = await read(context, `/api/v1/purchase-orders/${created.id}`);
    expect(detail.receipts[0].lines[0].posted_qty).toBe("4.000000");
    expect(detail.order.fulfillment_state).toBe("CLOSED");
    const stock = await read(
      context,
      `/api/v1/inventory/availability?entity_id=${f.entity}&warehouse_id=${f.warehouse}&sku_id=${f.product.sku_id}`,
    );
    expect(
      stock.items.reduce(
        (total: number, row: any) => total + Number(row.on_hand),
        0,
      ),
    ).toBe(14);
    const viewer = await wholesale(viewerContext, "demo.viewer");
    const viewPage = await viewerContext.newPage();
    await viewPage.goto("/");
    await nav(viewPage, "采购入库");
    await viewPage
      .getByRole("row")
      .filter({ hasText: order.order.number })
      .getByRole("button", { name: "查看采购单" })
      .click();
    await expect(
      viewPage.getByRole("alert").filter({ hasText: "未到货余量已关闭" }),
    ).toContainText("未到货余量已关闭");
    await expect(
      viewPage.getByRole("button", { name: "关闭未收余量", exact: true }),
    ).toHaveCount(0);
    const forbidden = await viewerContext.request.post(
      origin + `/api/v1/purchase-orders/${created.id}/close`,
      {
        headers: { ...headers(viewer), "Idempotency-Key": randomUUID() },
        data: { expected_version: detail.order.version, reason: "无权关闭" },
      },
    );
    expect(forbidden.status()).toBe(403);
    expect(errors).toEqual([]);
  } finally {
    await reviewer.close();
    await viewerContext.close();
  }
});
