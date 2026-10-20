import { test, expect, type Page, type BrowserContext } from "@playwright/test";
import { randomUUID } from "node:crypto";
import {
  wholesale,
  stockedCustomer,
  command,
  read,
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
  const dest = await command(context, owner, "/api/v1/warehouses", {
    client_operation_id: randomUUID(),
    entity_id: f.entity,
    code: "TQ-" + f.suffix,
    name: "调拨待办仓-" + f.suffix,
    location_code: "TQ-A",
  });
  const candidates = await read(
    context,
    `/api/v1/transfers/candidates?entity_id=${f.entity}&warehouse_id=${f.warehouse}&search=${f.lot}`,
  );
  const transfer = await command(context, owner, "/api/v1/transfers", {
    client_operation_id: randomUUID(),
    entity_id: f.entity,
    source_warehouse_id: f.warehouse,
    destination_warehouse_id: dest.id,
    destination_location_id: dest.location_id,
    reason: "调拨待办闭环",
    lines: [{ balance_id: candidates.items[0].id, qty: "10" }],
  });
  return { ...f, dest, transfer };
}
async function find(page: Page, search: string, kind = "all") {
  const queue = page.getByRole("region", { name: "进销存业务待办" });
  await expect(queue.getByLabel("待办环节").locator("option")).toHaveCount(33);
  await queue.getByLabel("待办环节").selectOption(kind);
  await expect(
    queue.getByRole("button", { name: "刷新业务待办" }),
  ).toBeEnabled();
  await queue.getByLabel("查找待办").fill(search);
  await queue.getByRole("button", { name: "查询待办", exact: true }).click();
  await expect(
    queue.getByRole("button", { name: "刷新业务待办" }),
  ).toBeEnabled();
  return queue;
}
async function open(page: Page, number: string, stage: string) {
  const queue = await find(page, number);
  const row = queue.getByRole("row").filter({ hasText: number });
  await expect(row).toContainText(stage);
  await row.getByRole("button", { name: "查看原单" }).click();
  await expect(
    page.getByRole("heading", { name: number, exact: true }),
  ).toBeVisible();
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
  await dialog.getByRole("button", { name: "保存", exact: true }).click();
  await expect(dialog).not.toBeVisible();
}

test("transfer queue continues independent review, dispatch and partial/quarantine receipts in original document", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(180000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext();
  const viewerContext = await browser.newContext();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    const quality = await reviewer.newPage();
    quality.on("pageerror", (e) => errors.push(e.message));
    const f = await prepare(context, owner, reviewer, checker);
    const number = f.transfer.number;
    await page.goto("/");
    const queue = await find(page, "调拨待办仓-" + f.suffix, "transfer_review");
    await expect(
      queue.getByRole("row").filter({ hasText: number }),
    ).toContainText("→");
    await open(page, number, "调拨待审核");
    await expect(
      page.getByRole("button", { name: "独立审核调拨", exact: true }),
    ).toHaveCount(0);
    await back(page, number, "调拨待审核");
    await quality.goto("/");
    await open(quality, number, "调拨待审核");
    await quality
      .getByRole("button", { name: "独立审核调拨", exact: true })
      .click();
    await save(quality, "独立核对来源、目的及数量");
    await expect(
      quality.getByRole("button", { name: "确认调拨发出", exact: true }),
    ).toHaveCount(0);
    await back(quality, number, "调拨待发出");
    await open(page, number, "调拨待发出");
    await page
      .getByRole("button", { name: "确认调拨发出", exact: true })
      .click();
    await save(page, "十件实物装运发出");
    await back(page, number, "调拨待收货");

    const viewer = await wholesale(viewerContext, "demo.viewer");
    const viewPage = await viewerContext.newPage();
    await viewPage.goto("/");
    await open(viewPage, number, "调拨待收货");
    await expect(
      viewPage.getByRole("button", { name: "登记本次收货", exact: true }),
    ).toHaveCount(0);
    const forbidden = await viewerContext.request.post(
      origin + `/api/v1/transfers/${f.transfer.id}/dispatch`,
      {
        headers: { ...headers(viewer), "Idempotency-Key": randomUUID() },
        data: { expected_version: "3", reason: "只读权限检查" },
      },
    );
    expect(forbidden.status()).toBe(403);
    for (const [qty, status] of [
      ["8", "QUALIFIED"],
      ["2", "QUARANTINE"],
    ]) {
      await open(page, number, "调拨待收货");
      await page
        .getByRole("button", { name: "登记本次收货", exact: true })
        .click();
      const dialog = page.getByRole("dialog");
      await dialog.getByLabel("本次收货数量 1", { exact: true }).fill(qty!);
      await dialog.getByLabel("实物批号 1", { exact: true }).fill(f.lot);
      await dialog
        .getByLabel("收货质量 1", { exact: true })
        .selectOption(status!);
      await save(
        page,
        qty === "8"
          ? "实际收八件合格，余二件仍在途"
          : "余二件实际到货，隔离待验",
      );
      await back(page, number, qty === "8" ? "调拨待收货" : undefined);
      if (qty === "8") {
        await expect(page.locator(".el-message")).toHaveCount(0);
        await page.screenshot({
          path: "../../artifacts/transfer-queue-partial.png",
          fullPage: true,
          animations: "disabled",
        });
      }
    }
    const detail = await read(context, `/api/v1/transfers/${f.transfer.id}`);
    expect(detail.document.state).toBe("RECEIVED");
    expect(detail.lines[0].remaining_qty).toBe("0.000000");
    expect(detail.receipts).toHaveLength(2);
    const stock = await read(
      context,
      `/api/v1/inventory/availability?entity_id=${f.entity}&warehouse_id=${f.dest.id}&sku_id=${f.product.sku_id}`,
    );
    expect(stock.items.find((r: any) => r.eligible).available).toBe("8.000000");
    expect(stock.items.find((r: any) => !r.eligible).on_hand).toBe("2.000000");
    const transit = await read(
      context,
      `/api/v1/inventory/availability?entity_id=${f.entity}&warehouse_id=${detail.document.transit_warehouse_id}&sku_id=${f.product.sku_id}`,
    );
    expect(transit.items[0].on_hand).toBe("0.000000");
    const ap = await read(
      context,
      `/api/v1/settlements/items?entity_id=${f.entity}&kind=AP&party_id=${f.party.id}`,
    );
    expect(ap.items).toHaveLength(1);
    expect(ap.items[0].outstanding_amount).toBe("130.00");
    const ar = await read(
      context,
      `/api/v1/settlements/items?entity_id=${f.entity}&kind=AR&party_id=${f.party.id}`,
    );
    expect(ar.items).toHaveLength(0);
    expect(errors).toEqual([]);
  } finally {
    await reviewer.close();
    await viewerContext.close();
  }
});

test("transfer queue failure clears old rows, retry restores them and enterprise switch clears direct launch", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(120000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext();
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    const f = await prepare(context, owner, reviewer, checker);
    await page.goto("/");
    const queue = await find(page, f.transfer.number, "transfer_review");
    await expect(queue.getByRole("button", { name: "查看原单" })).toHaveCount(
      1,
    );
    await page.route("**/api/v1/work-queue?**", (route) =>
      route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          error: { code: "TEST_UNAVAILABLE", message: "待办查询暂不可用" },
        }),
      }),
    );
    await queue.getByRole("button", { name: "刷新业务待办" }).click();
    await expect(
      queue.getByText("待办未能加载，请点击刷新重试。"),
    ).toBeVisible();
    await expect(queue.getByRole("button", { name: "查看原单" })).toHaveCount(
      0,
    );
    await page.unroute("**/api/v1/work-queue?**");
    await queue.getByRole("button", { name: "刷新业务待办" }).click();
    await expect(queue.getByRole("button", { name: "查看原单" })).toHaveCount(
      1,
    );
    await queue.getByRole("button", { name: "查看原单" }).click();
    await expect(
      page.getByRole("heading", { name: f.transfer.number, exact: true }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "返回调拨列表", exact: true })
      .click();
    await page
      .getByRole("row")
      .filter({ hasText: f.transfer.number })
      .getByRole("button", { name: "调拨详情", exact: true })
      .click();
    const switcher = page.getByRole("combobox", { name: "切换企业" });
    await switcher.press("Enter");
    const other = owner.tenants.find((t: any) => t.id !== owner.tenant_id);
    await page.getByRole("option", { name: other.name, exact: true }).click();
    await expect(
      page.getByRole("heading", { name: f.transfer.number, exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "返回业务待办", exact: true }),
    ).toHaveCount(0);
    await expect(page.getByLabel("查找待办")).toHaveValue("");
    await expect(page.getByLabel("待办环节")).toHaveValue("all");
  } finally {
    await reviewer.close();
  }
});
