import { test, expect, type Page, type BrowserContext } from "@playwright/test";
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
async function completedSale(
  context: BrowserContext,
  owner: any,
  f: any,
  account: string,
  qty: string,
) {
  const sale = await command(context, owner, "/api/v1/retail/sales", {
    client_operation_id: randomUUID(),
    entity_id: f.entity,
    warehouse_id: f.warehouse,
    lines: [
      { sku_id: f.product.sku_id, qty, unit_price: "20", tax_rate: "0.13" },
    ],
  });
  await command(context, owner, `/api/v1/retail/sales/${sale.id}/cash`, {
    client_operation_id: randomUUID(),
    expected_version: "1",
    confirmed: true,
    reason: "零售退货验收实际现金模拟",
    account_id: account,
    tendered: "100",
  });
  const data = await read(context, `/api/v1/retail/sales/${sale.id}`);
  await command(context, owner, `/api/v1/retail/sales/${sale.id}/complete`, {
    expected_version: "2",
    reason: "原销售核对出库",
    lines: data.allocations.map((a: any) => ({
      allocation_id: a.id,
      qty: a.allocated_qty,
      lot_no: a.lot_no,
    })),
  });
  return sale;
}
async function startReturn(page: Page, sale: any, qty: string) {
  await page.goto("/");
  await nav(page, "零售收银");
  await page
    .getByRole("row")
    .filter({ hasText: sale.number })
    .getByRole("button", { name: "零售详情" })
    .click();
  await page.getByRole("button", { name: "办理原单退货", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "退货与退款", exact: true }),
  ).toBeVisible();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("本次退货数量 1", { exact: true }).fill(qty);
  await dialog
    .getByLabel("退货原因")
    .fill("顾客退回本次数量，按原销售现金退款");
  await dialog
    .getByRole("button", { name: "创建退货申请", exact: true })
    .click();
  await expect(dialog).not.toBeVisible();
  return page.locator(".document-number").innerText();
}
async function receive(page: Page, qty: string, lot: string) {
  await page.getByRole("button", { name: "登记隔离收货", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("收货数量 1", { exact: true }).fill(qty);
  await dialog.getByLabel("实物批号 1", { exact: true }).fill(lot);
  await dialog.getByLabel("隔离保管位置").fill("门店退货隔离柜 R-01");
  await dialog.getByLabel("我已核对本次全部实物交接完成").check();
  await confirm(page, "已收到全部本次退回实物");
  await expect(page.locator(".panel-heading .el-tag").first()).toHaveText(
    "隔离待验",
  );
}

test("retail partial returns share quarantine and independent review, refund original cash, reconcile original sale", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(180000);
  const owner = await wholesale(context, "demo.owner"),
    reviewer = await browser.newContext();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    const checker = await wholesale(reviewer, "demo.quality"),
      checkPage = await reviewer.newPage();
    checkPage.on("pageerror", (e) => errors.push(e.message));
    const f = await stockedCustomer(context, owner, reviewer, checker);
    const account = await command(
      context,
      owner,
      "/api/v1/settlements/accounts",
      {
        entity_id: f.entity,
        code: "RR-" + f.suffix,
        name: "零售退款现金-" + f.suffix,
        method: "CASH",
      },
    );
    const sale = await completedSale(context, owner, f, account.id, "3");
    const stock = () =>
      read(
        context,
        `/api/v1/inventory/availability?entity_id=${f.entity}&warehouse_id=${f.warehouse}&sku_id=${f.product.sku_id}`,
      );
    for (const [qty, amount, onHand] of [
      ["1", "20.00", "8.000000"],
      ["2", "40.00", "10.000000"],
    ]) {
      const number = await startReturn(page, sale, qty!);
      await expect(
        page.getByRole("button", { name: "独立复核退货", exact: true }),
      ).toHaveCount(0);
      await receive(page, qty!, f.lot);
      expect((await stock()).items[0].on_hand).toBe(
        qty === "1" ? "7.000000" : "8.000000",
      );
      await checkPage.goto("/");
      await nav(checkPage, "退货与退款");
      await checkPage
        .getByRole("row")
        .filter({ hasText: number })
        .getByRole("button", { name: "退货详情" })
        .click();
      await checkPage
        .getByRole("button", { name: "独立复核退货", exact: true })
        .click();
      const dialog = checkPage.getByRole("dialog");
      await dialog.getByLabel("实物核对数量 1", { exact: true }).fill(qty!);
      await dialog.getByLabel("实物批号 1", { exact: true }).fill(f.lot);
      await confirm(checkPage, "独立检查本次退回品质量、数量、批号合格");
      await checkPage
        .getByRole("button", { name: "确认退货入库", exact: true })
        .click();
      await confirm(checkPage, "核对退货入库");
      await expect(
        checkPage.locator(".panel-heading .el-tag").first(),
      ).toHaveText("已退货");
      await expect(
        checkPage.getByRole("button", {
          name: "登记本次零售退款",
          exact: true,
        }),
      ).toHaveCount(0);
      expect((await stock()).items[0].on_hand).toBe(onHand);
      await page
        .getByRole("button", { name: "刷新退货详情", exact: true })
        .click();
      await expect(
        page.getByRole("region", { name: "零售退货现金退款" }),
      ).toContainText(amount!);
      await expect(
        page.getByRole("heading", { name: "原单有效核销", exact: true }),
      ).toHaveCount(0);
      await capture(page, "retail-return-awaiting-refund");
      await page
        .getByRole("button", { name: "登记本次零售退款", exact: true })
        .click();
      const refundDialog = page.getByRole("dialog");
      await refundDialog
        .getByLabel("退款核对说明")
        .fill("已实际退还净收现金 " + amount + " 元");
      await refundDialog.getByLabel("我已按本次退货金额实际退还现金").check();
      await refundDialog
        .getByRole("button", { name: "确认登记零售退款", exact: true })
        .click();
      await expect(refundDialog).not.toBeVisible();
      await expect(
        page.getByRole("button", { name: "登记本次零售退款", exact: true }),
      ).toBeDisabled();
      await capture(page, "retail-return-refunded");
    }
    await page.goto("/");
    await nav(page, "零售收银");
    await page
      .getByRole("row")
      .filter({ hasText: sale.number })
      .getByRole("button", { name: "零售详情" })
      .click();
    await expect(
      page.getByRole("region", { name: "零售退货汇总" }),
    ).toContainText("60.00 元");
    const data = await read(context, `/api/v1/retail/sales/${sale.id}`);
    expect(data.returns_summary).toEqual({
      returned_amount: "60.00",
      refunded_amount: "60.00",
      pending_refund_amount: "0.00",
    });
    expect(data.payments).toHaveLength(1);
    await capture(page, "retail-return-original-sale");

    const rejectedSale = await completedSale(
      context,
      owner,
      f,
      account.id,
      "1",
    );
    const rejected = await startReturn(page, rejectedSale, "1");
    await receive(page, "1", f.lot);
    await checkPage.goto("/");
    await nav(checkPage, "退货与退款");
    await checkPage
      .getByRole("row")
      .filter({ hasText: rejected })
      .getByRole("button", { name: "退货详情" })
      .click();
    await checkPage
      .getByRole("button", { name: "独立复核退货", exact: true })
      .click();
    await checkPage
      .getByRole("dialog")
      .getByLabel("复核结论")
      .selectOption("REJECT");
    await confirm(checkPage, "外包装破损，本次拒收");
    await page
      .getByRole("button", { name: "刷新退货详情", exact: true })
      .click();
    await page
      .getByRole("button", { name: "登记交还客户", exact: true })
      .click();
    await page
      .getByRole("dialog")
      .getByLabel("我已核对本次全部实物交接完成")
      .check();
    await confirm(page, "已将原实物交还顾客");
    await expect(page.locator(".panel-heading .el-tag").first()).toHaveText(
      "已取消",
    );
    expect((await stock()).items[0].on_hand).toBe("9.000000");
    const rejectedData = await read(
      context,
      `/api/v1/retail/sales/${rejectedSale.id}`,
    );
    expect(rejectedData.returns_summary.returned_amount).toBe("0");
    expect(rejectedData.returns_summary.refunded_amount).toBe("0");
    await capture(page, "retail-return-rejected");
    expect(errors).toEqual([]);
  } finally {
    await reviewer.close();
  }
});

test("viewer cannot refund a retail return through direct API", async ({
  context,
}) => {
  const viewer = await wholesale(context, "demo.viewer");
  const response = await context.request.post(
    origin + `/api/v1/returns/${randomUUID()}/retail-refund`,
    {
      headers: { ...headers(viewer), "Idempotency-Key": randomUUID() },
      data: {
        client_operation_id: randomUUID(),
        expected_version: "4",
        reason: "禁止越权退款",
        confirmed: true,
      },
    },
  );
  expect(response.status()).toBe(403);
});
