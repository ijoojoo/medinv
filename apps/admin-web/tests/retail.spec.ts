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

async function capture(page: Page, name: string) {
  await expect(page.locator(".el-message")).toHaveCount(0, { timeout: 8000 });
  await expect(page.locator(".el-loading-mask:visible")).toHaveCount(0);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: "../../artifacts/" + name + ".png",
    fullPage: true,
    animations: "disabled",
  });
}
async function confirm(page: Page, reason: string) {
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("操作说明").fill(reason);
  await dialog.getByRole("button", { name: "确认保存", exact: true }).click();
}
test("cash retail sale and paid-pending refund use real stock without customer credit", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(150000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext(),
    errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    const f = await stockedCustomer(context, owner, reviewer, checker);
    const account = await command(
      context,
      owner,
      "/api/v1/settlements/accounts",
      {
        entity_id: f.entity,
        code: "POS-" + f.suffix,
        name: "收银验收-" + f.suffix,
        method: "CASH",
      },
    );
    await page.goto("/");
    await nav(page, "零售收银");
    for (const terminal of ["COMPLETED", "REFUNDED"]) {
      await page
        .getByRole("button", { name: "新建零售单", exact: true })
        .click();
      let dialog = page.getByRole("dialog");
      await dialog.getByLabel("搜索商品").fill(f.name);
      await dialog.getByRole("button", { name: "查找可售商品" }).click();
      await dialog
        .getByRole("row")
        .filter({ hasText: f.name })
        .getByRole("button", { name: "加入", exact: true })
        .click();
      await dialog.getByLabel("零售数量 1", { exact: true }).fill("3");
      await dialog.getByLabel("零售单价 1", { exact: true }).fill("20");
      await dialog
        .getByRole("button", { name: "建单并预占库存", exact: true })
        .click();
      await expect(dialog).not.toBeVisible();
      await expect(page.locator(".panel-heading .el-tag").first()).toHaveText(
        "待收款",
      );
      await page
        .getByRole("button", { name: "登记现金收款", exact: true })
        .click();
      dialog = page.getByRole("dialog");
      await dialog
        .getByLabel("现金账户", { exact: true })
        .selectOption(account.id);
      await dialog
        .getByLabel("顾客交付现金（元）", { exact: true })
        .fill("100");
      await expect(dialog).toContainText("应找零：40.00 元");
      await dialog.getByLabel("我已核对现金实收与找零").check();
      await confirm(page, "核对实际现金及找零，虚拟软件验收");
      await expect(dialog).not.toBeVisible();
      await expect(page.locator(".panel-heading .el-tag").first()).toHaveText(
        "已收款待出库",
      );
      await expect(
        page.getByRole("button", { name: "登记现金收款", exact: true }),
      ).toHaveCount(0);
      // The pending payment is persisted across a browser reload.
      const number = await page.locator(".document-number").innerText();
      await page.reload();
      await nav(page, "零售收银");
      await page
        .getByRole("row")
        .filter({ hasText: number })
        .getByRole("button", { name: "零售详情" })
        .click();
      await expect(
        page.getByRole("region", { name: "零售现金记录" }),
      ).toContainText("40.00");
      await capture(page, "retail-paid-pending");
      if (terminal === "COMPLETED") {
        await page
          .getByRole("button", { name: "核对并完成出库", exact: true })
          .click();
        dialog = page.getByRole("dialog");
        await dialog.getByLabel("交付数量 1", { exact: true }).fill("3");
        await dialog.getByLabel("交付批号 1", { exact: true }).fill("WRONG");
        const failed = page.waitForResponse(
          (r) => r.url().endsWith("/complete") && r.status() === 409,
        );
        await confirm(page, "核对交付");
        await failed;
        await expect(dialog).toBeVisible();
        await dialog.getByLabel("交付批号 1", { exact: true }).fill(f.lot);
        await confirm(page, "核对交付批号正确");
        await expect(dialog).not.toBeVisible();
        await expect(page.locator(".panel-heading .el-tag").first()).toHaveText(
          "已完成",
        );
      } else {
        await page
          .getByRole("button", { name: "登记退款并取消", exact: true })
          .click();
        dialog = page.getByRole("dialog");
        await dialog.getByLabel("我已向顾客退还本单实收现金").check();
        await confirm(page, "退回净收现金 60 元，未交付商品");
        await expect(dialog).not.toBeVisible();
        await expect(page.locator(".panel-heading .el-tag").first()).toHaveText(
          "已退款取消",
        );
        await expect(
          page.getByRole("region", { name: "零售现金记录" }),
        ).toContainText("退款");
      }
      await capture(
        page,
        terminal === "COMPLETED" ? "retail-completed" : "retail-refunded",
      );
      await page
        .getByRole("button", { name: "返回零售列表", exact: true })
        .click();
    }
    const stock = await read(
      context,
      `/api/v1/inventory/availability?entity_id=${f.entity}&warehouse_id=${f.warehouse}&sku_id=${f.product.sku_id}`,
    );
    expect(stock.items[0].on_hand).toBe("7.000000");
    expect(stock.items[0].reserved).toBe("0.000000");
    expect(errors).toEqual([]);
  } finally {
    await reviewer.close();
  }
});

test("retail viewer can read but cannot sell", async ({ page, context }) => {
  const viewer = await wholesale(context, "demo.viewer");
  await page.goto("/");
  await nav(page, "零售收银");
  await expect(
    page.getByRole("button", { name: "新建零售单", exact: true }),
  ).toHaveCount(0);
  const response = await context.request.post(origin + "/api/v1/retail/sales", {
    headers: { ...headers(viewer), "Idempotency-Key": randomUUID() },
    data: {
      client_operation_id: randomUUID(),
      entity_id: viewer.entities[0].id,
      warehouse_id: randomUUID(),
      lines: [
        { sku_id: randomUUID(), qty: "1", unit_price: "1", tax_rate: "0" },
      ],
    },
  });
  expect(response.status()).toBe(403);
});
