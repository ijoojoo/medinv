import { test, expect, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { wholesale, stockedCustomer, command, read } from "./business-helpers";

async function find(page: Page, number: string, stage: string) {
  const queue = page.getByRole("region", { name: "进销存业务待办" });
  await expect(queue.getByLabel("待办环节").locator("option")).toHaveCount(33);
  await queue.getByLabel("查找待办").fill(number);
  await queue.getByRole("button", { name: "查询待办", exact: true }).click();
  const row = queue.getByRole("row").filter({ hasText: number });
  await expect(row).toHaveCount(1);
  await expect(row).toContainText(stage);
  return row;
}
async function open(page: Page, number: string, stage: string) {
  const row = await find(page, number, stage);
  await row.getByRole("button", { name: "查看原单", exact: true }).click();
  await expect(page.locator(".document-number")).toHaveText(number);
}
async function back(page: Page, number: string, stage?: string) {
  await page.getByRole("button", { name: "返回业务待办", exact: true }).click();
  await expect(page.getByLabel("查找待办")).toHaveValue(number);
  const queue = page.getByRole("region", { name: "进销存业务待办" });
  await expect(
    queue.getByRole("button", { name: "刷新业务待办" }),
  ).toBeEnabled();
  if (stage)
    await expect(
      queue.getByRole("row").filter({ hasText: number }),
    ).toContainText(stage);
  else
    await expect(
      queue.getByText("当前条件下暂无待办", { exact: true }),
    ).toBeVisible();
}
async function save(page: Page, reason: string) {
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("操作说明").fill(reason);
  await dialog.getByRole("button", { name: "确认保存", exact: true }).click();
  await expect(dialog).not.toBeVisible();
}
async function receive(page: Page, lot: string) {
  await page.getByRole("button", { name: "登记隔离收货", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("收货数量 1", { exact: true }).fill("1");
  await dialog.getByLabel("实物批号 1", { exact: true }).fill(lot);
  await dialog.getByLabel("隔离保管位置").fill("退货待办隔离柜");
  await dialog.getByLabel("我已核对本次全部实物交接完成").check();
  await save(page, "实物清点接收完成");
}
async function review(page: Page, qty: string, lot: string, rejected = false) {
  await page.getByRole("button", { name: "独立复核退货", exact: true }).click();
  const dialog = page.getByRole("dialog");
  if (rejected) await dialog.getByLabel("复核结论").selectOption("REJECT");
  else {
    await dialog.getByLabel("实物核对数量 1", { exact: true }).fill(qty);
    await dialog.getByLabel("实物批号 1", { exact: true }).fill(lot);
  }
  await save(
    page,
    rejected ? "破损拒收，等待实际交还" : "独立核对数量批号质量合格",
  );
}

test("return work queue carries retail receipt, review, stock, refund and rejection handback", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(180000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    const quality = await reviewer.newPage();
    quality.on("pageerror", (e) => errors.push(e.message));
    const f = await stockedCustomer(context, owner, reviewer, checker);
    const account = await command(
      context,
      owner,
      "/api/v1/settlements/accounts",
      {
        entity_id: f.entity,
        code: "RQ-" + f.suffix,
        name: "退货待办现金",
        method: "CASH",
      },
    );
    const sale = await command(context, owner, "/api/v1/retail/sales", {
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
    await command(context, owner, `/api/v1/retail/sales/${sale.id}/cash`, {
      client_operation_id: randomUUID(),
      expected_version: "1",
      confirmed: true,
      reason: "已收现金",
      account_id: account.id,
      tendered: "100",
    });
    const original = await read(context, `/api/v1/retail/sales/${sale.id}`);
    const posted = await command(
      context,
      owner,
      `/api/v1/retail/sales/${sale.id}/complete`,
      {
        expected_version: "2",
        reason: "原零售交付",
        lines: original.allocations.map((a: any) => ({
          allocation_id: a.id,
          qty: a.allocated_qty,
          lot_no: a.lot_no,
        })),
      },
    );
    const source = await read(
      context,
      `/api/v1/returns/sources/${posted.posting_id}`,
    );
    const create = () =>
      command(context, owner, "/api/v1/returns", {
        client_operation_id: randomUUID(),
        source_posting_id: posted.posting_id,
        reason: "顾客本次退一件",
        lines: [{ source_posting_line_id: source.lines[0].id, qty: "1" }],
      });
    const first = await create();
    await page.goto("/");
    await open(page, first.number, "退回品待收货");
    await receive(page, f.lot);
    await back(page, first.number, "退回品待验收");
    await quality.goto("/");
    await open(quality, first.number, "退回品待验收");
    await review(quality, "1", f.lot);
    await back(quality, first.number, "退回品待入库");
    await open(quality, first.number, "退回品待入库");
    await quality
      .getByRole("button", { name: "确认退货入库", exact: true })
      .click();
    await save(quality, "确认按原成本入库");
    await back(quality, first.number, "零售退货待退款");

    // The next physical return and the previous cash refund are separate unfinished work.
    const second = await create();
    await open(page, first.number, "零售退货待退款");
    await expect(
      page.getByRole("region", { name: "零售退货现金退款" }),
    ).toContainText("20.00");
    await page
      .getByRole("button", { name: "登记本次零售退款", exact: true })
      .click();
    let dialog = page.getByRole("dialog");
    await dialog.getByLabel("退款核对说明").fill("已实际退现金20元");
    await dialog.getByLabel("我已按本次退货金额实际退还现金").check();
    await dialog
      .getByRole("button", { name: "确认登记零售退款", exact: true })
      .click();
    await expect(dialog).not.toBeVisible();
    await back(page, first.number);
    await open(page, second.number, "退回品待收货");
    await receive(page, f.lot);
    await back(page, second.number, "退回品待验收");
    await open(quality, second.number, "退回品待验收");
    await review(quality, "1", f.lot, true);
    await back(quality, second.number, "退回品待交还");
    await find(page, second.number, "退回品待交还");
    await expect(page.locator(".el-message")).toHaveCount(0);
    await page.screenshot({
      path: "../../artifacts/return-queue-handback.png",
      fullPage: true,
      animations: "disabled",
    });
    await open(page, second.number, "退回品待交还");
    await page
      .getByRole("button", { name: "登记交还客户", exact: true })
      .click();
    dialog = page.getByRole("dialog");
    await dialog.getByLabel("我已核对本次全部实物交接完成").check();
    await save(page, "已将拒收实物交还顾客");
    await back(page, second.number);
    const inventory = await read(
      context,
      `/api/v1/inventory/availability?entity_id=${f.entity}&warehouse_id=${f.warehouse}&sku_id=${f.product.sku_id}`,
    );
    expect(inventory.items[0].on_hand).toBe("8.000000");
    const final = await read(context, `/api/v1/retail/sales/${sale.id}`);
    expect(final.returns_summary).toEqual({
      returned_amount: "20.00",
      refunded_amount: "20.00",
      pending_refund_amount: "0.00",
    });
    expect(errors).toEqual([]);
  } finally {
    await reviewer.close();
  }
});

test("purchase return queue opens exact return, preserves filter, records supplier refund and respects read-only role", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(180000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext();
  const viewer = await browser.newContext();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    const quality = await reviewer.newPage();
    const f = await stockedCustomer(context, owner, reviewer, checker);
    const items = await read(
      context,
      `/api/v1/settlements/items?entity_id=${f.entity}&kind=AP&party_id=${f.party.id}`,
    );
    const ap = items.items.find((r: any) => r.party_id === f.party.id);
    const source = await read(
      context,
      `/api/v1/returns/sources/${ap.posting_id}`,
    );
    const account = await command(
      context,
      owner,
      "/api/v1/settlements/accounts",
      {
        entity_id: f.entity,
        code: "PQ-" + f.suffix,
        name: "采购退款原账户",
        method: "BANK",
      },
    );
    const cash = await command(
      context,
      owner,
      "/api/v1/settlements/cash-documents",
      {
        client_operation_id: randomUUID(),
        entity_id: f.entity,
        party_id: f.party.id,
        account_id: account.id,
        direction: "PAYMENT",
        amount: "26",
        occurred_on: new Date().toLocaleDateString("en-CA"),
        reference: "PAY-" + f.suffix,
        note: "已实际支付",
        confirmed: true,
      },
    );
    const allocated = await command(
      context,
      owner,
      "/api/v1/settlements/allocations",
      {
        client_operation_id: randomUUID(),
        cash_document_id: cash.id,
        expected_cash_version: "1",
        allocations: [
          {
            settlement_item_id: ap.id,
            expected_item_version: ap.version,
            amount: "26",
          },
        ],
      },
    );
    await command(
      context,
      owner,
      `/api/v1/settlements/allocations/${allocated.allocation_ids[0]}/reverse`,
      {
        client_operation_id: randomUUID(),
        reason: "退货先撤销原核销",
      },
    );
    const ret = await command(context, owner, "/api/v1/returns", {
      client_operation_id: randomUUID(),
      source_posting_id: ap.posting_id,
      reason: "退供应商两件",
      lines: [{ source_posting_line_id: source.lines[0].id, qty: "2" }],
    });
    await quality.goto("/");
    await open(quality, ret.number, "采购退货待审核");
    await review(quality, "2", f.lot);
    await back(quality, ret.number, "采购退货待出库");
    await open(quality, ret.number, "采购退货待出库");
    await quality
      .getByRole("button", { name: "确认退货出库", exact: true })
      .click();
    await save(quality, "实物出库交付供应商");
    await back(quality, ret.number, "退货退款待核对");
    await wholesale(viewer, "demo.viewer");
    const viewPage = await viewer.newPage();
    await viewPage.goto("/");
    const row = await find(viewPage, ret.number, "退货退款待核对");
    await expect(row).toContainText("不代表应退欠款");
    await row.getByRole("button", { name: "查看原单" }).click();
    await expect(
      viewPage.getByRole("heading", { name: "收到供应商退款", exact: true }),
    ).toBeVisible();
    await expect(
      viewPage.getByRole("button", { name: "登记实际退款", exact: true }),
    ).toHaveCount(0);
    await page.goto("/");
    await open(page, ret.number, "退货退款待核对");
    // This is a document id, never the source posting id; returning to the list remains usable.
    await page
      .getByRole("button", { name: "返回退货列表", exact: true })
      .click();
    await page
      .getByRole("row")
      .filter({ hasText: ret.number })
      .getByRole("button", { name: "退货详情" })
      .click();
    await page
      .getByRole("button", { name: "登记实际退款", exact: true })
      .click();
    const dialog = page.getByRole("dialog");
    await dialog
      .getByRole("combobox", { name: "原收付款单", exact: true })
      .selectOption(cash.id);
    await dialog.getByLabel("实际退款金额（元）").fill("26");
    await dialog.getByLabel("退款凭据编号").fill("RF-" + f.suffix);
    await dialog.getByLabel("退款核对说明").fill("已收到供应商退回原账户26元");
    await dialog.getByLabel("我已核对退款实际发生及退款凭据").check();
    await dialog
      .getByRole("button", { name: "确认保存退款记录", exact: true })
      .click();
    await expect(dialog).not.toBeVisible();
    await back(page, ret.number);
    const inventory = await read(
      context,
      `/api/v1/inventory/availability?entity_id=${f.entity}&warehouse_id=${f.warehouse}&sku_id=${f.product.sku_id}`,
    );
    expect(inventory.items[0].on_hand).toBe("8.000000");
    const final = await read(
      context,
      `/api/v1/settlements/items?entity_id=${f.entity}&kind=AP&party_id=${f.party.id}`,
    );
    expect(final.items[0].returned_amount).toBe("26.00");
    expect(final.items[0].outstanding_amount).toBe("104.00");
    expect(errors).toEqual([]);
  } finally {
    await reviewer.close();
    await viewer.close();
  }
});
