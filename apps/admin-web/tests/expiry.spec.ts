import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { resolve } from "node:path";
import {
  wholesale,
  stockedCustomer,
  command,
  read,
  nav,
} from "./business-helpers";

test("expired unpaid retail and unpicked shipment release stock and explain recovery", async ({
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
    const checker = await wholesale(reviewer, "demo.quality");
    const f = await stockedCustomer(context, owner, reviewer, checker);
    const age = async (rid: string) => {
      await promisify(execFile)(
        resolve(
          "../../backend/.venv",
          process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
        ),
        [
          resolve("../../scripts/expire_browser_fixture.py"),
          owner.tenant_id,
          f.entity,
          rid,
          f.product.sku_id,
        ],
        { timeout: 20000, env: { ...process.env, PYTHONUTF8: "1" } },
      );
    };
    const created = await command(context, owner, "/api/v1/retail/sales", {
      client_operation_id: randomUUID(),
      entity_id: f.entity,
      warehouse_id: f.warehouse,
      lines: [
        {
          sku_id: f.product.sku_id,
          qty: "3",
          unit_price: "20",
          tax_rate: "0.13",
        },
      ],
    });
    const detail = await read(context, `/api/v1/retail/sales/${created.id}`);
    await age(detail.reservation.id);
    await page.goto("/");
    await nav(page, "零售收银");
    const row = page.getByRole("row").filter({ hasText: detail.sale.number });
    await expect(row).toContainText("已超时取消");
    await row.getByRole("button", { name: "零售详情" }).click();
    await expect(
      page.getByRole("alert").filter({ hasText: "未付款预占已到期" }),
    ).toContainText("自动取消并释放库存");
    await expect(
      page.getByRole("button", { name: "登记现金收款", exact: true }),
    ).toHaveCount(0);
    await expect(page.locator(".el-loading-mask:visible")).toHaveCount(0);
    await page.screenshot({
      path: "../../artifacts/expiry-retail.png",
      fullPage: true,
      animations: "disabled",
    });
    await command(
      context,
      owner,
      `/api/v1/customer-credit/${f.party.id}?entity_id=${f.entity}`,
      {
        expected_version: "0",
        credit_limit: "1000",
        valid_until: `${f.year + 1}-12-31`,
        reason: "软件验收额度",
      },
    );
    const so = await command(context, owner, "/api/v1/sales-orders", {
      entity_id: f.entity,
      customer_id: f.party.id,
      warehouse_id: f.warehouse,
      delivery_address: "软件验收地址",
      minimum_shelf_days: 0,
      lines: [
        {
          sku_id: f.product.sku_id,
          qty: "10",
          unit_price: "20",
          tax_rate: "0.13",
        },
      ],
    });
    await command(reviewer, checker, `/api/v1/sales-orders/${so.id}/approve`, {
      expected_version: "1",
      reason: "超时回收软件验收",
    });
    let order = await read(context, `/api/v1/sales-orders/${so.id}`);
    const shipment = await command(
      context,
      owner,
      `/api/v1/sales-orders/${so.id}/shipments`,
      {
        expected_version: order.order.version,
        client_operation_id: randomUUID(),
        lines: [{ order_line_id: order.lines[0].id, qty: "3" }],
      },
    );
    order = await read(context, `/api/v1/sales-orders/${so.id}`);
    await age(order.shipments[0].reservation_id);
    await nav(page, "批发销售");
    await page
      .getByRole("row")
      .filter({ hasText: order.order.number })
      .getByRole("button", { name: "查看销售单" })
      .click();
    const card = page.getByTestId(`shipment-${shipment.id}`);
    await expect(card).toContainText("已超时取消");
    await expect(card.getByRole("alert")).toContainText(
      "原订单保留，可重新分配发货",
    );
    await expect(page.locator(".el-loading-mask:visible")).toHaveCount(0);
    await page.screenshot({
      path: "../../artifacts/expiry-shipment.png",
      fullPage: true,
      animations: "disabled",
    });
    const stock = await read(
      context,
      `/api/v1/inventory/availability?entity_id=${f.entity}&warehouse_id=${f.warehouse}&sku_id=${f.product.sku_id}`,
    );
    expect(stock.items[0].on_hand).toBe("10.000000");
    expect(stock.items[0].reserved).toBe("0.000000");
    order = await read(context, `/api/v1/sales-orders/${so.id}`);
    expect(order.order.state).toBe("APPROVED");
    await command(context, owner, `/api/v1/sales-orders/${so.id}/shipments`, {
      expected_version: order.order.version,
      client_operation_id: randomUUID(),
      lines: [{ order_line_id: order.lines[0].id, qty: "10" }],
    });
    expect(errors).toEqual([]);
  } finally {
    await reviewer.close();
  }
});
