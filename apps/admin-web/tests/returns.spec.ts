import { test, expect, type Page, type BrowserContext } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { headers } from "./helpers";
import { businessToday } from "../src/dates";
import {
  wholesale,
  stockedCustomer,
  command,
  read,
  nav,
  origin,
} from "./business-helpers";

async function confirmReason(page: Page, reason: string) {
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("操作说明").fill(reason);
  await dialog.getByRole("button", { name: "确认保存", exact: true }).click();
  await expect(dialog).not.toBeVisible();
}

async function capture(page: Page, path: string) {
  await expect(page.locator(".el-message")).toHaveCount(0, { timeout: 8000 });
  await expect(page.locator(".el-loading-mask:visible")).toHaveCount(0);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path, fullPage: true, animations: "disabled" });
}

async function receiveInUI(page: Page, qty: string, lot: string) {
  await page.getByRole("button", { name: "登记隔离收货", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("收货数量 1", { exact: true }).fill(qty);
  await dialog.getByLabel("实物批号 1", { exact: true }).fill(lot);
  await dialog
    .getByLabel("隔离保管位置", { exact: true })
    .fill("一号仓退货隔离区 A-01");
  await dialog.getByLabel("我已核对本次全部实物交接完成").check();
  await confirmReason(page, "实际收取退回实物，隔离保管待验");
  await expect(page.locator(".panel-heading .el-tag").first()).toHaveText(
    "隔离待验",
  );
}

async function prepareSale(
  context: BrowserContext,
  owner: any,
  reviewer: BrowserContext,
  checker: any,
) {
  const f = await stockedCustomer(context, owner, reviewer, checker);
  await command(
    context,
    owner,
    "/api/v1/customer-credit/" + f.party.id + "?entity_id=" + f.entity,
    {
      expected_version: "0",
      credit_limit: "1000",
      valid_until: f.year + 1 + "-12-31",
      reason: "退货验收授信",
    },
  );
  const order = await command(context, owner, "/api/v1/sales-orders", {
    entity_id: f.entity,
    customer_id: f.party.id,
    warehouse_id: f.warehouse,
    delivery_address: "退货软件测试",
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
    "/api/v1/sales-orders/" + order.id + "/approve",
    { expected_version: "1", reason: "核对原销售" },
  );
  let sale = await read(context, "/api/v1/sales-orders/" + order.id);
  const shipment = await command(
    context,
    owner,
    "/api/v1/sales-orders/" + order.id + "/shipments",
    {
      expected_version: sale.order.version,
      client_operation_id: randomUUID(),
      lines: [{ order_line_id: sale.lines[0].id, qty: "3" }],
    },
  );
  sale = await read(context, "/api/v1/sales-orders/" + order.id);
  const lines = sale.shipments[0].lines.map((r: any) => ({
    shipment_line_id: r.id,
    qty: r.qty,
    lot_no: r.lot_no,
  }));
  await command(context, owner, "/api/v1/shipments/" + shipment.id + "/pick", {
    expected_version: "1",
    lines,
    note: "原单拣货",
  });
  await command(
    reviewer,
    checker,
    "/api/v1/shipments/" + shipment.id + "/verify",
    {
      expected_version: "2",
      lines,
      note: "独立核验",
      shipping_ref: "RT-" + f.suffix,
    },
  );
  await command(
    reviewer,
    checker,
    "/api/v1/shipments/" + shipment.id + "/post",
    { expected_version: "3" },
  );
  return { ...f, order, shipment };
}

test("two-role repeated partial sales and purchase returns with quarantine receipts and refunds", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(210000);
  const owner = await wholesale(context, "demo.owner"),
    reviewer = await browser.newContext();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    const checker = await wholesale(reviewer, "demo.quality"),
      checkPage = await reviewer.newPage();
    checkPage.on("pageerror", (e) => errors.push(e.message));
    const f = await prepareSale(context, owner, reviewer, checker);
    const account = await command(
      context,
      owner,
      "/api/v1/settlements/accounts",
      {
        entity_id: f.entity,
        code: "RT-" + f.suffix,
        name: "退货验收银行-" + f.suffix,
        method: "BANK",
      },
    );
    const today = new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(new Date());
    const cashIds: Record<string, string> = {};
    for (const kind of ["SALES", "PURCHASE"]) {
      const item = (
        await read(
          context,
          "/api/v1/settlements/items?entity_id=" +
            f.entity +
            "&kind=" +
            (kind === "SALES" ? "AR" : "AP") +
            "&party_id=" +
            f.party.id,
        )
      ).items[0];
      const cash = await command(
        context,
        owner,
        "/api/v1/settlements/cash-documents",
        {
          client_operation_id: randomUUID(),
          entity_id: f.entity,
          party_id: f.party.id,
          account_id: account.id,
          direction: kind === "SALES" ? "RECEIPT" : "PAYMENT",
          amount: kind === "SALES" ? "80" : "130",
          reference: kind + f.suffix,
          occurred_on: today,
          note: "核对原收付款虚拟凭据",
          confirmed: true,
        },
      );
      cashIds[kind] = cash.id;
      await command(context, owner, "/api/v1/settlements/allocations", {
        client_operation_id: randomUUID(),
        cash_document_id: cash.id,
        expected_cash_version: "1",
        allocations: [
          {
            settlement_item_id: item.id,
            expected_item_version: item.version,
            amount: item.gross_amount,
          },
        ],
      });
    }
    for (const [kind, qty, amount, first] of [
      ["SALES", "1", "20", true],
      ["SALES", "2", "40", false],
      ["PURCHASE", "4", "52", true],
      ["PURCHASE", "6", "78", false],
    ] as const) {
      await page.goto("/");
      await nav(page, "退货与退款");
      await page
        .getByRole("tab", {
          name: kind === "SALES" ? "销售退货来源" : "采购退货来源",
          exact: true,
        })
        .click();
      const source = page.getByRole("row").filter({ hasText: f.customer });
      await source.getByRole("button", { name: "申请退货" }).click();
      let dialog = page.getByRole("dialog");
      await dialog.getByLabel("本次退货数量 1", { exact: true }).fill(qty);
      await dialog
        .getByLabel("退货原因")
        .fill("按本次数量部分退回，实物已单独保管");
      await dialog.getByRole("button", { name: "创建退货申请" }).click();
      await expect(dialog).not.toBeVisible();
      const number = await page.locator(".document-number").innerText();
      if (kind === "SALES") {
        await receiveInUI(page, qty, f.lot);
      }
      if (first) {
        await page
          .getByRole("button", { name: "撤销原核销", exact: true })
          .click();
        await confirmReason(page, "原单退货，解除原核销后退款");
        await expect(
          page.getByRole("button", { name: "撤销原核销", exact: true }),
        ).toHaveCount(0);
      }
      await checkPage.goto("/");
      await nav(checkPage, "退货与退款");
      await checkPage
        .getByRole("row")
        .filter({ hasText: number })
        .getByRole("button", { name: "退货详情" })
        .click();
      await checkPage.getByRole("button", { name: "独立复核退货" }).click();
      dialog = checkPage.getByRole("dialog");
      await dialog.getByLabel("实物核对数量 1", { exact: true }).fill(qty);
      await dialog.getByLabel("实物批号 1", { exact: true }).fill(f.lot);
      await confirmReason(checkPage, "确认数量、批号及质量符合本次退货条件");
      await checkPage
        .getByRole("button", {
          name: kind === "SALES" ? "确认退货入库" : "确认退货出库",
          exact: true,
        })
        .click();
      await confirmReason(checkPage, "确认本次实物交接完成");
      await expect(
        checkPage.locator(".panel-heading .el-tag").first(),
      ).toHaveText("已退货");
      await page
        .getByRole("button", { name: "刷新退货详情", exact: true })
        .click();
      for (const refundAmount of [amount]) {
        await page
          .getByRole("button", { name: "登记实际退款", exact: true })
          .click();
        dialog = page.getByRole("dialog");
        await dialog
          .getByRole("combobox", { name: "原收付款单", exact: true })
          .selectOption(cashIds[kind]);
        await dialog.getByLabel("实际退款金额（元）").fill(refundAmount);
        await dialog
          .getByLabel("退款凭据编号")
          .fill("RF-" + kind + "-" + refundAmount + "-" + f.suffix);
        await dialog.getByLabel("退款核对说明").fill("核对实际退款虚拟凭据");
        await dialog.getByLabel("我已核对退款实际发生及退款凭据").check();
        const date = dialog.getByRole("textbox", {
          name: "实际退款日期",
          exact: true,
        });
        let writes = 0;
        const count = (r: import("@playwright/test").Request) => {
          if (
            r.method() === "POST" &&
            new URL(r.url()).pathname === "/api/v1/return-refunds"
          )
            writes++;
        };
        page.on("request", count);
        for (const value of ["20260230", "99991231"]) {
          await date.fill(value);
          await dialog
            .locator("form")
            .evaluate((f: HTMLFormElement) => f.requestSubmit());
          await expect(date).toHaveAttribute("aria-invalid", "true");
          await expect(date).toBeFocused();
          expect(writes).toBe(0);
        }
        const actualDate = businessToday();
        await date.fill(actualDate.replaceAll("-", ""));
        const submitted = page.waitForRequest(
          (r) =>
            r.method() === "POST" &&
            new URL(r.url()).pathname === "/api/v1/return-refunds",
        );
        let release!: () => void;
        const pending = new Promise<void>((resolve) => (release = resolve));
        await page.route("**/api/v1/return-refunds", async (route) => {
          const response = await route.fetch();
          await pending;
          await route.fulfill({ response });
        });
        await dialog
          .getByRole("button", { name: "确认保存退款记录", exact: true })
          .click();
        const sent = await submitted;
        try {
          await expect(date).toBeDisabled();
          await expect(dialog.getByLabel("实际退款金额（元）")).toBeDisabled();
          expect(sent.postDataJSON().occurred_on).toBe(actualDate);
        } finally {
          release();
        }
        await expect(dialog).not.toBeVisible();
        await page.unroute("**/api/v1/return-refunds");
        page.off("request", count);
        expect(writes).toBe(1);
      }
      await expect(
        page.getByRole("button", { name: "登记实际退款", exact: true }),
      ).toBeDisabled();
      await capture(
        page,
        "../../artifacts/" +
          (kind === "SALES"
            ? "sales-partial-return"
            : "purchase-partial-return") +
          ".png",
      );
    }
    const stock = await read(
      context,
      "/api/v1/inventory/availability?entity_id=" +
        f.entity +
        "&warehouse_id=" +
        f.warehouse +
        "&sku_id=" +
        f.product.sku_id,
    );
    expect(stock.items).toHaveLength(1);
    expect(stock.items[0].on_hand).toBe("0.000000");
    const customer = await read(
      context,
      "/api/v1/customer-credit/" + f.party.id + "?entity_id=" + f.entity,
    );
    expect(customer.receivable).toBe("0.00");
    expect(customer.available).toBe("1000.00");
    for (const kind of ["SALES", "PURCHASE"]) {
      const cash = await read(
        context,
        "/api/v1/settlements/cash-documents/" + cashIds[kind],
      );
      expect(cash.cash.remaining_amount).toBe(
        kind === "SALES" ? "20.00" : "0.00",
      );
    }
    expect(errors).toEqual([]);
  } finally {
    await reviewer.close();
  }
});

test("quarantine rejection holds goods until physical handback and never increases available stock", async ({
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
    const checkPage = await reviewer.newPage();
    checkPage.on("pageerror", (e) => errors.push(e.message));
    const f = await prepareSale(context, owner, reviewer, checker);
    await page.goto("/");
    await nav(page, "退货与退款");
    await page.getByRole("tab", { name: "销售退货来源", exact: true }).click();
    await page
      .getByRole("row")
      .filter({ hasText: f.customer })
      .getByRole("button", { name: "申请退货", exact: true })
      .click();
    let dialog = page.getByRole("dialog");
    await dialog.getByLabel("本次退货数量 1", { exact: true }).fill("1");
    await dialog.getByLabel("退货原因").fill("客户退回一件，等待质量判定");
    await dialog.getByRole("button", { name: "创建退货申请" }).click();
    await expect(dialog).not.toBeVisible();
    const number = await page.locator(".document-number").innerText();
    await expect(
      page.getByRole("button", { name: "独立复核退货", exact: true }),
    ).toHaveCount(0);
    await receiveInUI(page, "1", f.lot);
    await page
      .getByRole("button", { name: "返回退货列表", exact: true })
      .click();
    await page.getByRole("tab", { name: "隔离保管台账", exact: true }).click();
    const held = page.getByRole("row").filter({ hasText: number });
    await expect(held).toContainText("一号仓退货隔离区 A-01");
    await held.getByRole("button", { name: "退货详情" }).click();
    await capture(page, "../../artifacts/return-quarantine-held.png");
    const available = () =>
      read(
        context,
        "/api/v1/inventory/availability?entity_id=" +
          f.entity +
          "&warehouse_id=" +
          f.warehouse +
          "&sku_id=" +
          f.product.sku_id,
      );
    expect((await available()).items[0].on_hand).toBe("7.000000");
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
    dialog = checkPage.getByRole("dialog");
    await dialog.getByLabel("复核结论").selectOption("REJECT");
    await confirmReason(checkPage, "包装破损拒收，等待交还客户");
    await expect(
      checkPage.locator(".panel-heading .el-tag").first(),
    ).toHaveText("拒收待交还");
    await page
      .getByRole("button", { name: "刷新退货详情", exact: true })
      .click();
    await page
      .getByRole("button", { name: "登记交还客户", exact: true })
      .click();
    dialog = page.getByRole("dialog");
    await dialog.getByLabel("我已核对本次全部实物交接完成").check();
    await confirmReason(page, "原一件实物已交还客户并签收");
    await expect(page.locator(".panel-heading .el-tag").first()).toHaveText(
      "已取消",
    );
    await expect(
      page.getByRole("region", { name: "隔离保管记录" }),
    ).toContainText("已交还客户");
    await capture(page, "../../artifacts/return-quarantine-handed-back.png");
    expect((await available()).items[0].on_hand).toBe("7.000000");
    const credit = await read(
      context,
      "/api/v1/customer-credit/" + f.party.id + "?entity_id=" + f.entity,
    );
    expect(credit.receivable).toBe("60.00");
    await page
      .getByRole("button", { name: "返回退货列表", exact: true })
      .click();
    await page.getByRole("tab", { name: "隔离保管台账", exact: true }).click();
    await expect(page.getByRole("row").filter({ hasText: number })).toHaveCount(
      0,
    );
    expect(errors).toEqual([]);
  } finally {
    await reviewer.close();
  }
});

test("viewer can inspect return sources but cannot create returns", async ({
  page,
  context,
}) => {
  const current = await wholesale(context, "demo.viewer");
  await page.goto("/");
  await nav(page, "退货与退款");
  await page.getByRole("tab", { name: "销售退货来源", exact: true }).click();
  await expect(page.getByRole("button", { name: "申请退货" })).toHaveCount(0);
  const response = await context.request.post(origin + "/api/v1/returns", {
    headers: { ...headers(current), "Idempotency-Key": randomUUID() },
    data: {
      client_operation_id: randomUUID(),
      source_posting_id: randomUUID(),
      reason: "禁止越权",
    },
  });
  expect(response.status()).toBe(403);
});

test("original purchase and sales documents continue partial returns and reconcile source progress", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(210000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext(),
    viewer = await browser.newContext();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    const quality = await reviewer.newPage();
    quality.on("pageerror", (e) => errors.push(e.message));
    const f = await prepareSale(context, owner, reviewer, checker);
    const openOriginal = async (target: Page, kind: string) => {
      await target.goto("/");
      await nav(target, kind === "SALES" ? "批发销售" : "采购入库");
      await target
        .getByRole("row")
        .filter({
          hasText: kind === "SALES" ? f.order.number : f.purchase.number,
        })
        .getByRole("button", {
          name: kind === "SALES" ? "查看销售单" : "查看采购单",
          exact: true,
        })
        .click();
      await target
        .getByRole("button", {
          name: kind === "SALES" ? "本次出库退货与进度" : "本次入库退货与进度",
          exact: true,
        })
        .click();
      await expect(
        target.getByText("原单退货进度", { exact: false }),
      ).toBeVisible();
    };
    for (const [kind, qty, returned, remaining] of [
      ["SALES", "1", "20.00", "2"],
      ["SALES", "2", "60.00", "0"],
      ["PURCHASE", "4", "52.00", "6"],
      ["PURCHASE", "6", "130.00", "0"],
    ]) {
      await openOriginal(page, kind!);
      await page
        .getByRole("button", { name: "申请本次退货", exact: true })
        .click();
      const form = page.getByRole("dialog", { name: "申请退货", exact: true });
      await form.getByLabel("本次退货数量 1", { exact: true }).fill(qty!);
      await form.getByLabel("退货原因").fill("从原单按本次实物办理部分退货");
      await form.getByRole("button", { name: "创建退货申请" }).click();
      await expect(form).not.toBeVisible();
      const number = await page.locator(".document-number").innerText();
      await page
        .getByRole("button", { name: "返回退货列表", exact: true })
        .click();
      await expect(
        page.getByRole("button", { name: "申请本次退货", exact: true }),
      ).toBeDisabled();
      await page
        .getByRole("button", { name: "继续办理退货", exact: true })
        .click();
      await expect(page.locator(".document-number")).toHaveText(number);
      if (kind === "SALES") await receiveInUI(page, qty!, f.lot);
      await openOriginal(quality, kind!);
      await quality
        .getByRole("button", { name: "继续办理退货", exact: true })
        .click();
      await expect(quality.locator(".document-number")).toHaveText(number);
      await quality
        .getByRole("button", { name: "独立复核退货", exact: true })
        .click();
      const review = quality.getByRole("dialog");
      await review.getByLabel("实物核对数量 1", { exact: true }).fill(qty!);
      await review.getByLabel("实物批号 1", { exact: true }).fill(f.lot);
      await confirmReason(quality, "独立核对实物与原单批次");
      await quality
        .getByRole("button", {
          name: kind === "SALES" ? "确认退货入库" : "确认退货出库",
          exact: true,
        })
        .click();
      await confirmReason(quality, "确认本次退货交接");
      await expect(
        quality.locator(".panel-heading .el-tag").first(),
      ).toHaveText("已退货");
      await page
        .getByRole("button", { name: "返回退货列表", exact: true })
        .click();
      await expect(page.getByTestId("source-return-amount")).toContainText(
        `已完成退货 ${returned} 元`,
      );
      const cells = page
        .getByTestId("source-return-lines")
        .getByRole("row")
        .filter({ hasText: f.name })
        .getByRole("cell");
      await expect(cells.last()).toHaveText(`${remaining} 个`);
      await expect(
        page.getByRole("button", { name: "继续办理退货", exact: true }),
      ).toHaveCount(0);
      if (remaining === "0") {
        await expect(
          page.getByRole("button", { name: "申请本次退货", exact: true }),
        ).toBeDisabled();
        await expect(
          page.getByRole("button", { name: "退货详情", exact: true }),
        ).toHaveCount(2);
        await capture(
          page,
          `../../artifacts/original-${kind.toLowerCase()}-returns.png`,
        );
      }
      await page.getByRole("button", { name: "返回原单", exact: true }).click();
      await expect(page.locator(".document-number").first()).toHaveText(
        kind === "SALES" ? f.order.number : f.purchase.number,
      );
    }
    const stock = await read(
      context,
      `/api/v1/inventory/availability?entity_id=${f.entity}&warehouse_id=${f.warehouse}&sku_id=${f.product.sku_id}`,
    );
    expect(stock.items[0].on_hand).toBe("0.000000");
    const order = await read(context, `/api/v1/sales-orders/${f.order.id}`);
    expect(order.lines[0].fulfilled_qty).toBe("3.000000");
    for (const kind of ["AR", "AP"]) {
      const items = await read(
        context,
        `/api/v1/settlements/items?entity_id=${f.entity}&party_id=${f.party.id}&kind=${kind}`,
      );
      expect(items.items[0].outstanding_amount).toBe("0.00");
    }
    await wholesale(viewer, "demo.viewer");
    const readOnly = await viewer.newPage();
    await openOriginal(readOnly, "PURCHASE");
    await expect(
      readOnly.getByRole("button", { name: "申请本次退货", exact: true }),
    ).toHaveCount(0);
    await expect(
      readOnly.getByRole("button", { name: "退货详情", exact: true }),
    ).toHaveCount(2);
    expect(errors).toEqual([]);
  } finally {
    await reviewer.close();
    await viewer.close();
  }
});
