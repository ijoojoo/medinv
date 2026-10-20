import { chooseParty } from "./business-helpers";
import { chooseEntrySku } from "./business-helpers";
import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";
import {
  command,
  read,
  wholesale,
  stockedCustomer,
  nav,
} from "./business-helpers";

test("retail reservation and wholesale partial shipment share visible stock through completion", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(180000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext();
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    const f = await stockedCustomer(context, owner, reviewer, checker);
    const empty = await command(context, owner, "/api/v1/warehouses", {
      client_operation_id: randomUUID(),
      entity_id: f.entity,
      code: "STOCK-" + f.suffix,
      name: "可售验收空仓-" + f.suffix,
      location_code: "A01",
    });
    await command(
      context,
      owner,
      `/api/v1/customer-credit/${f.party.id}?entity_id=${f.entity}`,
      {
        expected_version: "0",
        credit_limit: "1000",
        valid_until: `${f.year + 1}-12-31`,
        reason: "软件验收授信",
      },
    );
    await page.goto("/");
    await nav(page, "零售收银");
    await page.getByRole("button", { name: "新建零售单", exact: true }).click();
    let dialog = page.getByRole("dialog");
    await dialog.getByLabel("出货仓库").selectOption(f.warehouse);
    await dialog.getByLabel("搜索商品").fill(f.name);
    await dialog.getByRole("button", { name: "查找可售商品" }).click();
    await dialog
      .getByRole("row")
      .filter({ hasText: f.name })
      .getByRole("button", { name: "加入", exact: true })
      .click();
    let stock = dialog.getByRole("region", { name: "仓库可售库存" });
    await expect(
      stock.getByRole("row").filter({ hasText: f.name }),
    ).toContainText("10 个");
    await dialog.getByLabel("零售数量 1", { exact: true }).fill("3");
    await dialog.getByLabel("零售单价 1", { exact: true }).fill("20");
    const reserved = page.waitForResponse(
      (r) =>
        r.url().endsWith("/retail/sales") && r.request().method() === "POST",
    );
    await dialog
      .getByRole("button", { name: "建单并预占库存", exact: true })
      .click();
    const retail = (await (await reserved).json()).data;
    await expect(dialog).not.toBeVisible();

    await nav(page, "批发销售");
    await page.getByRole("button", { name: "新建销售单", exact: true }).click();
    dialog = page.getByRole("dialog");
    await chooseParty(
      dialog.getByRole("combobox", { name: "客户", exact: true }),
      f.party.id,
    );
    await dialog.getByLabel("发货仓库").selectOption(f.warehouse);
    await dialog.getByLabel("收货地址").fill("可售库存软件验收地址");
    await chooseEntrySku(dialog, f.product.sku_id);
    await dialog.getByLabel("销售数量").fill("10");
    await dialog.getByLabel("含税单价（元）").fill("20");
    stock = dialog.getByRole("region", { name: "仓库可售库存" });
    await expect(
      stock.getByRole("row").filter({ hasText: f.name }),
    ).toContainText("7 个");
    await dialog.getByLabel("最低剩余效期（天）").fill("3650");
    await expect(
      stock.getByRole("row").filter({ hasText: f.name }),
    ).toContainText("0 个");
    await dialog.getByLabel("最低剩余效期（天）").fill("30");
    await expect(
      stock.getByRole("row").filter({ hasText: f.name }),
    ).toContainText("7 个");
    await dialog.getByLabel("发货仓库").selectOption(empty.id);
    await expect(
      stock.getByRole("row").filter({ hasText: f.name }),
    ).toContainText("0 个");
    await dialog.getByLabel("发货仓库").selectOption(f.warehouse);
    await expect(
      stock.getByRole("row").filter({ hasText: f.name }),
    ).toContainText("7 个");
    // Failed refresh must remove the old quantity and allow an explicit retry.
    await page.route(
      "**/selling/stock?**",
      (route) =>
        route.fulfill({
          status: 503,
          contentType: "application/json",
          body: "{}",
        }),
      { times: 1 },
    );
    await stock.getByRole("button", { name: "刷新可售库存" }).click();
    await expect(stock).toContainText("库存查询失败");
    await expect(stock.getByRole("row")).toHaveCount(0);
    await stock.getByRole("button", { name: "刷新可售库存" }).click();
    await expect(
      stock.getByRole("row").filter({ hasText: f.name }),
    ).toContainText("7 个");
    await expect(dialog.getByLabel("销售数量")).toHaveValue("10");
    const created = page.waitForResponse(
      (r) =>
        r.url().endsWith("/sales-orders") && r.request().method() === "POST",
    );
    await dialog.getByRole("button", { name: "保存销售单" }).click();
    const order = (await (await created).json()).data;
    await expect(dialog).not.toBeVisible();
    await command(
      reviewer,
      checker,
      `/api/v1/sales-orders/${order.id}/approve`,
      { expected_version: "1", reason: "独立审核销售" },
    );
    await page.getByRole("button", { name: "刷新销售详情" }).click();

    for (const qty of ["7", "3"]) {
      if (qty === "3") {
        await command(
          context,
          owner,
          `/api/v1/retail/sales/${retail.id}/cancel`,
          { expected_version: "1", reason: "顾客未付款取消，释放三件" },
        );
      }
      await page
        .getByRole("button", { name: "分配发货批次", exact: true })
        .click();
      dialog = page.getByRole("dialog");
      stock = dialog.getByRole("region", { name: "仓库可售库存" });
      await expect(
        stock.getByRole("row").filter({ hasText: f.name }),
      ).toContainText(`${qty} 个`);
      await expect(dialog.getByLabel("本次发货数量")).toHaveValue("0");
      await stock.getByRole("button", { name: "按可售量填入本次发货" }).click();
      await expect(dialog.getByLabel("本次发货数量")).toHaveValue(
        `${qty}.000000`,
      );
      if (qty === "7") {
        await expect(page.locator(".el-message")).toHaveCount(0);
        await page.screenshot({
          path: "../../artifacts/sale-stock-partial.png",
          fullPage: true,
          animations: "disabled",
        });
      }
      await dialog
        .getByRole("button", { name: "确认分配", exact: true })
        .click();
      await expect(dialog).not.toBeVisible();
      await page.getByRole("button", { name: "登记拣货", exact: true }).click();
      dialog = page.getByRole("dialog");
      await dialog.getByLabel("实物批号").fill(f.lot);
      await dialog.getByLabel("实物数量").fill(qty);
      await dialog.getByLabel("核查说明").fill("逐项核对数量批号");
      await dialog
        .getByRole("button", { name: "保存拣货", exact: true })
        .click();
      await expect(dialog).not.toBeVisible();
      const doc = await read(context, `/api/v1/sales-orders/${order.id}`);
      const shipment = doc.shipments.find((s: any) => s.state === "PICKED");
      await command(
        reviewer,
        checker,
        `/api/v1/shipments/${shipment.id}/verify`,
        {
          expected_version: shipment.version,
          note: "独立复核",
          shipping_ref: `STOCK-${f.suffix}-${qty}`,
          lines: shipment.lines.map((l: any) => ({
            shipment_line_id: l.id,
            qty: l.qty,
            lot_no: l.lot_no,
          })),
        },
      );
      await command(
        reviewer,
        checker,
        `/api/v1/shipments/${shipment.id}/post`,
        { expected_version: "3" },
      );
      await page.getByRole("button", { name: "刷新销售详情" }).click();
    }
    await expect(page.locator(".panel-heading .el-tag").first()).toHaveText(
      "已发齐",
    );
    const finalStock = await read(
      context,
      `/api/v1/inventory/availability?entity_id=${f.entity}&warehouse_id=${f.warehouse}&sku_id=${f.product.sku_id}`,
    );
    expect(finalStock.items[0].on_hand).toBe("0.000000");
    expect(finalStock.items[0].reserved).toBe("0.000000");
    const ar = await read(context, `/api/v1/receivables?entity_id=${f.entity}`);
    const items = ar.items.filter((r: any) => r.customer_id === f.party.id);
    expect(items).toHaveLength(2);
    expect(items.map((r: any) => r.gross_amount).sort()).toEqual([
      "140.00",
      "60.00",
    ]);
    expect(
      items.reduce((sum: number, r: any) => sum + Number(r.cost_amount), 0),
    ).toBeCloseTo(115.04, 8);
    await expect(page.locator(".el-message")).toHaveCount(0);
    await page.screenshot({
      path: "../../artifacts/sale-stock-fulfilled.png",
      fullPage: true,
      animations: "disabled",
    });
    expect(errors).toEqual([]);
  } finally {
    await reviewer.close();
  }
});
