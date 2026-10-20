import { test, expect, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { headers } from "./helpers";
import {
  command,
  nav,
  origin,
  read,
  stockedCustomer,
  wholesale,
} from "./business-helpers";

async function allocateUI(page: Page, amount: string) {
  await page.getByRole("button", { name: "核销单据", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("本次核销金额").fill(amount);
  await dialog.getByRole("button", { name: "确认核销", exact: true }).click();
  await expect(dialog).not.toBeVisible();
}

test("record actual receipt/payment, partial allocations, reverse and reconcile in UI", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(150000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext();
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    const f = await stockedCustomer(context, owner, reviewer, checker);
    await command(
      context,
      owner,
      `/api/v1/customer-credit/${f.party.id}?entity_id=${f.entity}`,
      {
        expected_version: "0",
        credit_limit: "1000",
        valid_until: `${f.year + 1}-12-31`,
        reason: "验收授信",
      },
    );
    const order = await command(context, owner, "/api/v1/sales-orders", {
      entity_id: f.entity,
      customer_id: f.party.id,
      warehouse_id: f.warehouse,
      delivery_address: "软件测试交付点",
      lines: [
        {
          sku_id: f.product.sku_id,
          qty: "3",
          unit_price: "20",
          tax_rate: "0.13",
        },
      ],
    });
    await command(
      reviewer,
      checker,
      `/api/v1/sales-orders/${order.id}/approve`,
      { expected_version: "1", reason: "核查通过" },
    );
    let doc = await read(context, `/api/v1/sales-orders/${order.id}`);
    const shipment = await command(
      context,
      owner,
      `/api/v1/sales-orders/${order.id}/shipments`,
      {
        expected_version: doc.order.version,
        client_operation_id: randomUUID(),
        lines: [{ order_line_id: doc.lines[0].id, qty: "3" }],
      },
    );
    doc = await read(context, `/api/v1/sales-orders/${order.id}`);
    const lines = doc.shipments[0].lines.map((line: any) => ({
      shipment_line_id: line.id,
      qty: line.qty,
      lot_no: line.lot_no,
    }));
    await command(context, owner, `/api/v1/shipments/${shipment.id}/pick`, {
      expected_version: "1",
      lines,
      note: "拣货核查",
    });
    await command(
      reviewer,
      checker,
      `/api/v1/shipments/${shipment.id}/verify`,
      {
        expected_version: "2",
        lines,
        note: "独立复核",
        shipping_ref: "HANDOVER-" + f.suffix,
      },
    );
    await command(reviewer, checker, `/api/v1/shipments/${shipment.id}/post`, {
      expected_version: "3",
    });
    await page.goto("/");
    await nav(page, "往来结算");
    await page.getByRole("tab", { name: "资金账户", exact: true }).click();
    await page
      .getByRole("button", { name: "新增资金账户", exact: true })
      .click();
    let dialog = page.getByRole("dialog");
    await dialog
      .getByLabel("账户编码", { exact: true })
      .fill("BANK-" + f.suffix);
    await dialog
      .getByLabel("账户名称", { exact: true })
      .fill("验收银行账簿-" + f.suffix);
    await dialog
      .getByRole("button", { name: "保存资金账户", exact: true })
      .click();
    await expect(dialog).not.toBeVisible();
    await page.getByRole("tab", { name: "应收账款", exact: true }).click();
    let row = page.getByRole("row").filter({ hasText: f.customer });
    await expect(row).toContainText("60.00");
    await row.getByRole("button", { name: "登记收款", exact: true }).click();
    dialog = page.getByRole("dialog");
    await dialog
      .getByRole("combobox", { name: "资金账户", exact: true })
      .selectOption({ label: `验收银行账簿-${f.suffix} · BANK-${f.suffix}` });
    await dialog.getByLabel("实际金额（元）").fill("80");
    await dialog.getByLabel("流水 / 凭据编号").fill("BANK-RECEIPT-" + f.suffix);
    await dialog
      .getByLabel("核对说明")
      .fill("核对本次实际收到 80 元的虚拟凭据");
    await dialog.getByLabel("我已核对款项实际发生及收付凭据").check();
    await dialog.getByRole("button", { name: "确认登记款项" }).click();
    await expect(dialog).not.toBeVisible();
    const cashNumber = await page.locator(".document-number").innerText();
    await allocateUI(page, "20");
    await expect(
      page.getByRole("definition").filter({ hasText: /60[.]00\s+元/ }),
    ).toHaveCount(1);
    await allocateUI(page, "40");
    await expect(
      page.getByRole("button", { name: "作废错误登记", exact: true }),
    ).toBeDisabled();
    const first = page
      .getByRole("row")
      .filter({ has: page.getByRole("cell", { name: "20.00", exact: true }) });
    await first.getByRole("button", { name: "撤销核销", exact: true }).click();
    dialog = page.getByRole("dialog");
    await dialog.getByLabel("操作原因").fill("测试核销纠错及应收恢复");
    await dialog
      .getByRole("button", { name: "确认撤销核销", exact: true })
      .click();
    await expect(dialog).not.toBeVisible();
    await expect(first).toContainText("已撤销");
    await allocateUI(page, "20");
    await expect(page.locator(".el-message")).toHaveCount(0);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: "../../artifacts/finance-receipt-reconciliation.png",
      fullPage: true,
      animations: "disabled",
    });
    await page.getByRole("button", { name: "返回往来账" }).click();
    await page.getByRole("tab", { name: "应收账款", exact: true }).click();
    row = page.getByRole("row").filter({ hasText: f.customer });
    await expect(row).toContainText("已结清");
    const credit = await read(
      context,
      `/api/v1/customer-credit/${f.party.id}?entity_id=${f.entity}`,
    );
    expect(credit.receivable).toBe("0.00");
    expect(credit.available).toBe("1000.00");
    await page.getByRole("tab", { name: "应付账款", exact: true }).click();
    row = page.getByRole("row").filter({ hasText: f.customer });
    await expect(row).toContainText("130.00");
    await row.getByRole("button", { name: "登记付款", exact: true }).click();
    dialog = page.getByRole("dialog");
    await dialog
      .getByRole("combobox", { name: "资金账户", exact: true })
      .selectOption({ label: `验收银行账簿-${f.suffix} · BANK-${f.suffix}` });
    await dialog.getByLabel("实际金额（元）").fill("130");
    await dialog.getByLabel("流水 / 凭据编号").fill("BANK-PAYMENT-" + f.suffix);
    await dialog.getByLabel("核对说明").fill("核对已付供应商款项");
    await dialog.getByLabel("我已核对款项实际发生及收付凭据").check();
    await dialog.getByRole("button", { name: "确认登记款项" }).click();
    await expect(dialog).not.toBeVisible();
    await allocateUI(page, "130");
    await expect(
      page.getByRole("button", { name: "核销单据", exact: true }),
    ).toBeDisabled();
    await page.getByRole("button", { name: "返回往来账" }).click();
    await page.getByRole("tab", { name: "应付账款", exact: true }).click();
    await expect(
      page.getByRole("row").filter({ hasText: f.customer }),
    ).toContainText("已结清");
    await page.getByRole("tab", { name: "收付款记录", exact: true }).click();
    await expect(
      page.getByRole("row").filter({ hasText: cashNumber }),
    ).toContainText("20.00");
    expect(errors).toEqual([]);
  } finally {
    await reviewer.close();
  }
});

test("viewer cannot record or allocate money even with direct HTTP requests", async ({
  page,
  context,
}) => {
  const current = await wholesale(context, "demo.viewer");
  await page.goto("/");
  await nav(page, "往来结算");
  await expect(
    page.getByRole("button", { name: "登记收款", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("tab", { name: "资金账户", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "新增资金账户", exact: true }),
  ).toHaveCount(0);
  const response = await context.request.post(
    origin + "/api/v1/settlements/accounts",
    {
      headers: { ...headers(current), "Idempotency-Key": randomUUID() },
      data: {
        entity_id: current.entities[0].id,
        code: "FORBIDDEN",
        name: "越权账户",
        method: "BANK",
      },
    },
  );
  expect(response.status()).toBe(403);
});
