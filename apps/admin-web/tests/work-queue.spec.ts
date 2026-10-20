import { test, expect, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";
import {
  command,
  read,
  wholesale,
  stockedCustomer,
  nav,
  origin,
} from "./business-helpers";
import { headers } from "./helpers";

async function find(page: Page, search: string, kind = "all") {
  await page
    .getByRole("combobox", { name: "工作台方案", exact: true })
    .selectOption("operations");
  const queue = page.getByRole("region", { name: "进销存业务待办" });
  await expect(queue.getByLabel("待办环节").locator("option")).toHaveCount(33);
  await queue.getByLabel("待办环节").selectOption(kind);
  await expect(
    queue.getByRole("button", { name: "查询待办", exact: true }),
  ).toBeEnabled();
  await queue.getByLabel("查找待办").fill(search);
  await queue.getByRole("button", { name: "查询待办", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "刷新业务待办" }),
  ).toBeEnabled();
  return queue;
}

test("work queue takes two roles through original procurement forms and clears finished work", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(180000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext();
  const quality = await reviewer.newPage();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  quality.on("pageerror", (e) => errors.push(e.message));
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    const f = await stockedCustomer(context, owner, reviewer, checker);
    const order = await command(context, owner, "/api/v1/purchase-orders", {
      entity_id: f.entity,
      supplier_id: f.party.id,
      warehouse_id: f.warehouse,
      lines: [
        {
          sku_id: f.product.sku_id,
          qty: "4",
          unit_price: "13",
          tax_rate: "0.13",
        },
      ],
    });
    await page.goto("/");
    let queue = await find(page, order.number);
    await expect(
      queue.getByRole("row").filter({ hasText: order.number }),
    ).toContainText("采购待审核");
    await queue.getByRole("button", { name: "查看原单" }).click();
    await expect(page.locator(".document-number:visible").first()).toHaveText(
      order.number,
    );
    await expect(
      page.getByRole("button", { name: "审核采购单", exact: true }),
    ).toBeDisabled();
    await page.getByRole("button", { name: "返回业务待办" }).click();
    await expect(page.getByLabel("查找待办")).toHaveValue(order.number);

    await quality.goto("/");
    queue = await find(quality, order.number);
    await queue.getByRole("button", { name: "查看原单" }).click();
    await quality
      .getByRole("button", { name: "审核采购单", exact: true })
      .click();
    let dialog = quality.getByRole("dialog");
    await dialog.getByLabel("操作说明").fill("待办进入原单，独立审核");
    await dialog.getByRole("button", { name: "确认审核", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await quality.getByRole("button", { name: "返回业务待办" }).click();
    await expect(
      quality.getByRole("row").filter({ hasText: order.number }),
    ).toContainText("采购待收货");

    await page.getByRole("button", { name: "刷新业务待办" }).click();
    queue = page.getByRole("region", { name: "进销存业务待办" });
    await expect(
      queue.getByRole("row").filter({ hasText: order.number }),
    ).toContainText("采购待收货");
    await queue.getByRole("button", { name: "查看原单" }).click();
    await page.getByRole("button", { name: "登记到货", exact: true }).click();
    dialog = page.getByRole("dialog");
    await dialog.getByLabel("随货单号").fill("QUEUE-" + f.suffix);
    await dialog.getByLabel("运输及随货核查").fill("常温随货核查");
    await dialog.getByLabel("到货数量").fill("4");
    await dialog.getByLabel("生产批号").fill("QUEUE-" + f.suffix);
    await dialog
      .getByRole("textbox", { name: "生产日期", exact: true })
      .fill(`${f.year}-01-01`);
    await dialog
      .getByRole("textbox", { name: "有效期至", exact: true })
      .fill(`${f.year + 1}-12-31`);
    await dialog.getByRole("button", { name: "保存到货记录" }).click();
    await expect(dialog).not.toBeVisible();
    await page.getByRole("button", { name: "返回业务待办" }).click();
    await expect(
      page.getByRole("row").filter({ hasText: order.number }),
    ).toContainText("到货待验收");

    await quality.getByRole("button", { name: "刷新业务待办" }).click();
    await expect(
      quality.getByRole("row").filter({ hasText: order.number }),
    ).toContainText("到货待验收");
    await quality.getByRole("button", { name: "查看原单" }).click();
    await quality
      .getByRole("button", { name: "登记验收", exact: true })
      .click();
    dialog = quality.getByRole("dialog");
    await dialog.getByLabel("本次合格数量").fill("4");
    await dialog.getByLabel("本次拒收数量").fill("0");
    await dialog.getByLabel("验收依据与结论").fill("4件合格，待办独立验收");
    await dialog.getByRole("button", { name: "保存验收记录" }).click();
    await expect(dialog).not.toBeVisible();
    await quality.getByRole("button", { name: "返回业务待办" }).click();
    await expect(
      quality.getByRole("row").filter({ hasText: order.number }),
    ).toContainText("合格待入库");
    await expect(quality.locator(".el-message")).toHaveCount(0);
    await quality.screenshot({
      path: "../../artifacts/work-queue-purchase.png",
      fullPage: true,
      animations: "disabled",
    });
    await quality.getByRole("button", { name: "查看原单" }).click();
    await quality
      .getByRole("button", { name: "合格入库", exact: true })
      .click();
    dialog = quality.getByRole("dialog");
    await expect(dialog).toContainText("4 个");
    await dialog.getByRole("button", { name: "确认入库", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await quality.getByRole("button", { name: "返回业务待办" }).click();
    await expect(
      quality.getByText("当前筛选范围没有待办，可切换环节或清空条件。", {
        exact: true,
      }),
    ).toBeVisible();
    await expect(quality.getByLabel("查找待办")).toHaveValue(order.number);
    const balances = await read(
      context,
      `/api/v1/inventory/availability?entity_id=${f.entity}&warehouse_id=${f.warehouse}&sku_id=${f.product.sku_id}`,
    );
    expect(
      balances.items.reduce(
        (sum: number, row: any) => sum + Number(row.on_hand),
        0,
      ),
    ).toBe(14);
    const payables = await read(
      context,
      `/api/v1/settlements/items?entity_id=${f.entity}&kind=AP&party_id=${f.party.id}`,
    );
    expect(
      payables.items
        .filter((p: any) => p.party_id === f.party.id)
        .map((p: any) => p.gross_amount)
        .sort(),
    ).toEqual(["130.00", "52.00"]);
    expect(errors).toEqual([]);
  } finally {
    await reviewer.close();
  }
});

test("work queue resumes wholesale picking and paid retail delivery without new orders", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(120000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext();
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
        reason: "业务待办测试",
      },
    );
    const order = await command(context, owner, "/api/v1/sales-orders", {
      entity_id: f.entity,
      customer_id: f.party.id,
      warehouse_id: f.warehouse,
      delivery_address: "软件验收交付点",
      lines: [
        {
          sku_id: f.product.sku_id,
          qty: "2",
          unit_price: "20",
          tax_rate: "0.13",
        },
      ],
    });
    await command(
      reviewer,
      checker,
      `/api/v1/sales-orders/${order.id}/approve`,
      { expected_version: "1", reason: "独立审核" },
    );
    const doc = await read(context, `/api/v1/sales-orders/${order.id}`);
    const shipment = await command(
      context,
      owner,
      `/api/v1/sales-orders/${order.id}/shipments`,
      {
        expected_version: doc.order.version,
        client_operation_id: randomUUID(),
        lines: [{ order_line_id: doc.lines[0].id, qty: "2" }],
      },
    );
    await page.goto("/");
    let queue = await find(page, shipment.number, "sales_pick");
    await expect(
      queue.getByRole("row").filter({ hasText: order.number }),
    ).toContainText("发货待拣货");
    await queue.getByRole("button", { name: "查看原单" }).click();
    await expect(page.locator(".document-number:visible").first()).toHaveText(
      order.number,
    );
    await page.getByRole("button", { name: "登记拣货", exact: true }).click();
    let dialog = page.getByRole("dialog");
    await dialog.getByLabel("实物批号").fill(f.lot);
    await dialog.getByLabel("实物数量").fill("2");
    await dialog.getByLabel("核查说明").fill("待办核对实物");
    await dialog.getByRole("button", { name: "保存拣货", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await page.getByRole("button", { name: "返回业务待办" }).click();
    await expect(page.getByLabel("待办环节")).toHaveValue("sales_pick");
    await expect(
      page.getByText("当前筛选范围没有待办，可切换环节或清空条件。", {
        exact: true,
      }),
    ).toBeVisible();
    queue = await find(page, shipment.number, "sales_verify");
    await expect(
      queue.getByRole("row").filter({ hasText: order.number }),
    ).toContainText("发货待复核");

    const sale = await command(context, owner, "/api/v1/retail/sales", {
      entity_id: f.entity,
      warehouse_id: f.warehouse,
      client_operation_id: randomUUID(),
      lines: [
        {
          sku_id: f.product.sku_id,
          qty: "3",
          unit_price: "20",
          tax_rate: "0.13",
        },
      ],
    });
    const account = await command(
      context,
      owner,
      "/api/v1/settlements/accounts",
      {
        entity_id: f.entity,
        code: "Q-" + f.suffix,
        name: "待办验收现金",
        method: "CASH",
      },
    );
    await command(context, owner, `/api/v1/retail/sales/${sale.id}/cash`, {
      client_operation_id: randomUUID(),
      expected_version: "1",
      account_id: account.id,
      tendered: "100",
      confirmed: true,
      reason: "实际现金核对",
    });
    queue = await find(page, sale.number, "retail_complete");
    await expect(
      queue.getByRole("row").filter({ hasText: sale.number }),
    ).toContainText("零售已收款待出库");
    await queue.getByRole("button", { name: "查看原单" }).click();
    await expect(page.locator(".document-number:visible").first()).toHaveText(
      sale.number,
    );
    await expect(
      page.getByRole("button", { name: "登记现金收款", exact: true }),
    ).toHaveCount(0);
    await page
      .getByRole("button", { name: "核对并完成出库", exact: true })
      .click();
    dialog = page.getByRole("dialog");
    await dialog.getByLabel("交付数量 1", { exact: true }).fill("3");
    await dialog.getByLabel("交付批号 1", { exact: true }).fill(f.lot);
    await dialog.getByLabel("操作说明").fill("核对现金已收及实物交付");
    await dialog.getByRole("button", { name: "确认保存", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await page.getByRole("button", { name: "返回业务待办" }).click();
    await expect(
      page.getByText("当前筛选范围没有待办，可切换环节或清空条件。", {
        exact: true,
      }),
    ).toBeVisible();
    const final = await read(context, `/api/v1/retail/sales/${sale.id}`);
    expect(final.sale.state).toBe("COMPLETED");
    expect(final.payments).toHaveLength(1);
    // Finish the already-picked wholesale sub-order through the independent checker.
    const picked = (await read(context, `/api/v1/sales-orders/${order.id}`))
      .shipments[0];
    await command(reviewer, checker, `/api/v1/shipments/${picked.id}/verify`, {
      expected_version: picked.version,
      note: "独立复核",
      shipping_ref: "Q-" + f.suffix,
      lines: picked.lines.map((l: any) => ({
        shipment_line_id: l.id,
        qty: l.qty,
        lot_no: l.lot_no,
      })),
    });
    await command(reviewer, checker, `/api/v1/shipments/${picked.id}/post`, {
      expected_version: "3",
    });
    queue = await find(page, order.number);
    await expect(
      queue.getByText("当前筛选范围没有待办，可切换环节或清空条件。", {
        exact: true,
      }),
    ).toBeVisible();
  } finally {
    await reviewer.close();
  }
});

test("viewer work queue keeps write permissions and clears filters on enterprise switch", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(90000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext();
  let order: { id: string; number: string };
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    const f = await stockedCustomer(context, owner, reviewer, checker);
    order = await command(context, owner, "/api/v1/purchase-orders", {
      entity_id: f.entity,
      supplier_id: f.party.id,
      warehouse_id: f.warehouse,
      lines: [
        {
          sku_id: f.product.sku_id,
          qty: "1",
          unit_price: "13",
          tax_rate: "0.13",
        },
      ],
    });
    await command(
      reviewer,
      checker,
      `/api/v1/purchase-orders/${order.id}/approve`,
      {
        expected_version: "1",
        reason: "只读待办验收",
      },
    );
  } finally {
    await reviewer.close();
  }
  const viewerContext = await browser.newContext();
  try {
    const viewer = await wholesale(viewerContext, "demo.viewer");
    expect(viewer.permissions).not.toContain("purchase.receive");
    const viewerPage = await viewerContext.newPage();
    await viewerPage.goto("/");
    const viewerQueue = await find(
      viewerPage,
      order.number,
      "purchase_receive",
    );
    await viewerQueue.getByRole("button", { name: "查看原单" }).click();
    await expect(
      viewerPage.locator(".document-number:visible").first(),
    ).toHaveText(order.number);
    await expect(
      viewerPage.getByRole("button", { name: "登记到货", exact: true }),
    ).toHaveCount(0);
    await expect(
      viewerPage.getByRole("button", { name: "关闭未收余量", exact: true }),
    ).toHaveCount(0);
    const response = await viewerContext.request.get(
      origin + `/api/v1/work-queue?entity_id=${randomUUID()}`,
      { headers: headers(viewer) },
    );
    expect(response.status()).toBe(404);
  } finally {
    await viewerContext.close();
  }
  await command(context, owner, `/api/v1/purchase-orders/${order.id}/close`, {
    expected_version: "2",
    reason: "验收结束，不再收货",
  });
  await page.goto("/");
  const queue = await find(page, "只在原企业的查找条件", "retail_pay");
  const switcher = page.getByRole("combobox", { name: "切换企业" });
  await switcher.press("Enter");
  const other = owner.tenants.find(
    (tenant: { id: string }) => tenant.id !== owner.tenant_id,
  );
  const switched = page.waitForResponse(
    (r) =>
      r.url().endsWith("/auth/tenant") &&
      r.request().method() === "POST" &&
      r.status() === 200,
  );
  await page.getByRole("option", { name: other.name, exact: true }).click();
  await switched;
  await expect(
    page.getByRole("heading", { name: "经营总览", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("combobox", { name: "工作台方案", exact: true })
    .selectOption("operations");
  await expect(queue.getByLabel("查找待办")).toHaveValue("");
  await expect(queue.getByLabel("待办环节")).toHaveValue("all");
  await expect(page.getByRole("button", { name: "返回业务待办" })).toHaveCount(
    0,
  );
});
