import { chooseParty } from "./business-helpers";
import { test, expect, type Page, type BrowserContext } from "@playwright/test";
import { randomUUID } from "node:crypto";
import {
  command,
  read,
  wholesale,
  stockedCustomer,
  origin,
} from "./business-helpers";
import { headers } from "./helpers";

async function prepare(
  context: BrowserContext,
  owner: any,
  reviewer: BrowserContext,
  checker: any,
) {
  const f = await stockedCustomer(context, owner, reviewer, checker);
  await command(
    context,
    owner,
    `/api/v1/customer-credit/${f.party.id}?entity_id=${f.entity}`,
    {
      expected_version: "0",
      credit_limit: "1000",
      valid_until: `${f.year + 1}-12-31`,
      reason: "补货业务验收",
    },
  );
  const sale = await command(context, owner, "/api/v1/sales-orders", {
    entity_id: f.entity,
    customer_id: f.party.id,
    warehouse_id: f.warehouse,
    delivery_address: "补货交付地址",
    lines: [
      {
        sku_id: f.product.sku_id,
        qty: "15",
        unit_price: "20",
        tax_rate: "0.13",
      },
    ],
  });
  await command(reviewer, checker, `/api/v1/sales-orders/${sale.id}/approve`, {
    expected_version: "1",
    reason: "独立审核补货需求",
  });
  return { ...f, sale };
}
async function openFromQueue(page: Page, number: string) {
  await page.goto("/");
  const queue = page.getByRole("region", { name: "进销存业务待办" });
  await expect(
    queue.getByRole("button", { name: "刷新业务待办" }),
  ).toBeEnabled();
  await queue.getByLabel("查找待办").fill(number);
  await queue.getByRole("button", { name: "查询待办", exact: true }).click();
  await queue.getByRole("button", { name: "查看原单" }).click();
  await expect(page.locator(".document-number").first()).toHaveText(number);
}
async function panel(page: Page) {
  await page.getByRole("button", { name: "缺货与补货", exact: true }).click();
  const area = page.getByRole("region", { name: "销售缺货与补货" });
  await expect(
    area.getByRole("button", { name: "刷新补货建议" }),
  ).toBeEnabled();
  return area;
}

test("sales shortage creates a linked purchase, receives stock and fulfills the original sales order", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(180000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext();
  const quality = await reviewer.newPage();
  const errors: string[] = [];
  for (const p of [page, quality])
    p.on("pageerror", (e) => errors.push(e.message));
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    const f = await prepare(context, owner, reviewer, checker);
    await openFromQueue(page, f.sale.number);
    let area = await panel(page);
    const stockRow = area.getByRole("row").filter({ hasText: f.name }).first();
    await expect(stockRow.getByRole("cell").nth(1)).toHaveText("15");
    await expect(stockRow.getByRole("cell").nth(2)).toHaveText("10");
    await expect(
      area.getByLabel("本次补货数量 1", { exact: true }),
    ).toHaveValue("5.000000");
    await expect(
      area.getByLabel("基本单位含税进价 1", { exact: true }),
    ).toHaveValue("");
    await chooseParty(area.getByLabel("补货供应商"), f.party.id);
    await area.getByLabel("基本单位含税进价 1", { exact: true }).fill("13");
    await area.screenshot({
      path: "../../artifacts/replenishment-shortage.png",
      animations: "disabled",
    });
    const created = page.waitForResponse(
      (r) =>
        r.url().endsWith(`/sales-orders/${f.sale.id}/replenishment`) &&
        r.request().method() === "POST",
    );
    await area.getByRole("button", { name: "保存补货采购单" }).click();
    const purchase = (await (await created).json()).data;
    await expect(page.locator(".document-number").first()).toHaveText(
      purchase.number,
    );
    await expect(
      page.getByRole("button", { name: "查看来源销售单" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "审核采购单", exact: true }),
    ).toBeDisabled();
    await page.getByRole("button", { name: "返回原销售单" }).click();
    await expect(page.locator(".document-number").first()).toHaveText(
      f.sale.number,
    );
    area = await panel(page);
    await expect(
      area.getByRole("button", { name: "保存补货采购单" }),
    ).toHaveCount(0);
    await expect(
      area.getByRole("row").filter({ hasText: purchase.number }),
    ).toContainText("待审核");
    await area.getByRole("button", { name: "办理补货采购" }).click();

    await openFromQueue(quality, purchase.number);
    await quality
      .getByRole("button", { name: "审核采购单", exact: true })
      .click();
    let dialog = quality.getByRole("dialog");
    await dialog.getByLabel("操作说明").fill("独立确认补货价格与供应商");
    await dialog.getByRole("button", { name: "确认审核", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await page.getByRole("button", { name: "刷新单据", exact: true }).click();
    await page.getByRole("button", { name: "登记到货", exact: true }).click();
    dialog = page.getByRole("dialog");
    await dialog.getByLabel("随货单号").fill("REPL-" + f.suffix);
    await dialog.getByLabel("运输及随货核查").fill("补货常温资料齐全");
    await dialog.getByLabel("到货数量").fill("5");
    await dialog.getByLabel("生产批号").fill("REPL-" + f.suffix);
    await dialog
      .getByLabel("生产日期", { exact: true })
      .fill(`${f.year}-01-01`);
    await dialog
      .getByLabel("有效期至", { exact: true })
      .fill(`${f.year + 2}-12-31`);
    await dialog.getByRole("button", { name: "保存到货记录" }).click();
    await expect(dialog).not.toBeVisible();
    await quality
      .getByRole("button", { name: "刷新单据", exact: true })
      .click();
    await quality
      .getByRole("button", { name: "登记验收", exact: true })
      .click();
    dialog = quality.getByRole("dialog");
    await dialog.getByLabel("本次合格数量").fill("5");
    await dialog.getByLabel("本次拒收数量").fill("0");
    await dialog.getByLabel("验收依据与结论").fill("补货五件合格");
    await dialog.getByRole("button", { name: "保存验收记录" }).click();
    await expect(dialog).not.toBeVisible();
    await quality
      .getByRole("button", { name: "合格入库", exact: true })
      .click();
    dialog = quality.getByRole("dialog");
    await dialog.getByRole("button", { name: "确认入库", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await quality.getByRole("button", { name: "查看来源销售单" }).click();
    await expect(
      quality.locator(".document-number:visible").first(),
    ).toHaveText(f.sale.number);

    await page.getByRole("button", { name: "查看来源销售单" }).click();
    await expect(page.locator(".document-number:visible").first()).toHaveText(
      f.sale.number,
    );
    area = await panel(page);
    await expect(
      area
        .getByRole("row")
        .filter({ hasText: f.name })
        .first()
        .getByRole("cell")
        .nth(2),
    ).toHaveText("15");
    await page
      .getByRole("button", { name: "分配发货批次", exact: true })
      .click();
    dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: "按可售量填入本次发货" }).click();
    await expect(dialog.getByLabel("本次发货数量")).toHaveValue("15.000000");
    await dialog.getByRole("button", { name: "确认分配", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    const salesDoc = await read(context, `/api/v1/sales-orders/${f.sale.id}`);
    const shipment = salesDoc.shipments[0];
    expect(shipment.lines).toHaveLength(2);
    await page.getByRole("button", { name: "登记拣货", exact: true }).click();
    dialog = page.getByRole("dialog");
    for (const [index, line] of shipment.lines.entries()) {
      await dialog.getByLabel("实物批号").nth(index).fill(line.lot_no);
      await dialog.getByLabel("实物数量").nth(index).fill(line.qty);
    }
    await dialog.getByLabel("核查说明").fill("原库存与补货库存共同交付");
    await dialog.getByRole("button", { name: "保存拣货", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await quality.getByRole("button", { name: "刷新销售详情" }).click();
    await quality
      .getByRole("button", { name: "出库复核", exact: true })
      .click();
    dialog = quality.getByRole("dialog");
    for (const [index, line] of shipment.lines.entries()) {
      await dialog.getByLabel("实物批号").nth(index).fill(line.lot_no);
      await dialog.getByLabel("实物数量").nth(index).fill(line.qty);
    }
    await dialog.getByLabel("核查说明").fill("独立核验十五件");
    await dialog
      .getByLabel("物流 / 交接凭据")
      .fill("REPL-DELIVERY-" + f.suffix);
    await dialog.getByRole("button", { name: "确认复核", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await quality
      .getByRole("button", { name: "销售出库", exact: true })
      .click();
    dialog = quality.getByRole("dialog");
    await dialog.getByRole("button", { name: "确认出库", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await expect(
      quality.locator(".panel-heading .el-tag:visible").first(),
    ).toHaveText("已发齐");
    const availability = await read(
      context,
      `/api/v1/inventory/availability?entity_id=${f.entity}&warehouse_id=${f.warehouse}&sku_id=${f.product.sku_id}`,
    );
    expect(
      availability.items.reduce(
        (sum: number, r: any) => sum + Number(r.on_hand),
        0,
      ),
    ).toBe(0);
    const ap = await read(
      context,
      `/api/v1/settlements/items?entity_id=${f.entity}&kind=AP&party_id=${f.party.id}`,
    );
    expect(
      ap.items
        .filter((r: any) => r.party_id === f.party.id)
        .reduce((sum: number, r: any) => sum + Number(r.gross_amount), 0),
    ).toBe(195);
    const ar = await read(context, `/api/v1/receivables?entity_id=${f.entity}`);
    const obligation = ar.items.find((r: any) => r.shipment_id === shipment.id);
    expect(obligation.gross_amount).toBe("300.00");
    expect(Number(obligation.cost_amount)).toBe(172.56);
    expect(errors).toEqual([]);
  } finally {
    await reviewer.close();
  }
});

test("replenishment rechecks stale suggestions, clears failed queries and keeps viewer read-only", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(120000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext();
  const viewerContext = await browser.newContext();
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    const f = await prepare(context, owner, reviewer, checker);
    await openFromQueue(page, f.sale.number);
    const area = await panel(page);
    const endpoint = `/api/v1/sales-orders/${f.sale.id}/replenishment`;
    await page.route("**" + endpoint, (route) =>
      route.fulfill({
        status: 503,
        contentType: "application/json",
        body: "{}",
      }),
    );
    await area.getByRole("button", { name: "刷新补货建议" }).click();
    await expect(
      area.getByText("补货信息未能加载，请刷新重试。", { exact: true }),
    ).toBeVisible();
    await expect(
      area.getByLabel("本次补货数量 1", { exact: true }),
    ).toHaveCount(0);
    await page.unroute("**" + endpoint);
    await area.getByRole("button", { name: "刷新补货建议" }).click();
    await expect(
      area.getByLabel("本次补货数量 1", { exact: true }),
    ).toHaveValue("5.000000");
    const external = await command(context, owner, endpoint, {
      expected_version: "2",
      supplier_id: f.party.id,
      lines: [
        {
          sku_id: f.product.sku_id,
          qty: "2",
          unit_price: "13",
          tax_rate: "0.13",
        },
      ],
    });
    await chooseParty(area.getByLabel("补货供应商"), f.party.id);
    await area.getByLabel("基本单位含税进价 1", { exact: true }).fill("13");
    const conflict = page.waitForResponse(
      (r) => r.url().endsWith(endpoint) && r.request().method() === "POST",
    );
    await area.getByRole("button", { name: "保存补货采购单" }).click();
    expect((await conflict).status()).toBe(409);
    await area.getByRole("button", { name: "刷新补货建议" }).click();
    await expect(
      area.getByLabel("本次补货数量 1", { exact: true }),
    ).toHaveValue("3.000000");
    await expect(
      area.getByLabel("基本单位含税进价 1", { exact: true }),
    ).toHaveValue("13");
    expect((await read(context, endpoint)).purchases).toHaveLength(1);
    const viewer = await wholesale(viewerContext, "demo.viewer");
    const viewerPage = await viewerContext.newPage();
    await openFromQueue(viewerPage, f.sale.number);
    const readonly = await panel(viewerPage);
    await expect(
      readonly.getByRole("row").filter({ hasText: external.number }),
    ).toBeVisible();
    await expect(
      readonly.getByRole("button", { name: "保存补货采购单" }),
    ).toHaveCount(0);
    const denied = await viewerContext.request.post(origin + endpoint, {
      headers: { ...headers(viewer), "Idempotency-Key": randomUUID() },
      data: {
        expected_version: "2",
        supplier_id: f.party.id,
        lines: [{ sku_id: f.product.sku_id, qty: "3", unit_price: "13" }],
      },
    });
    expect(denied.status()).toBe(403);
  } finally {
    await reviewer.close();
    await viewerContext.close();
  }
});
