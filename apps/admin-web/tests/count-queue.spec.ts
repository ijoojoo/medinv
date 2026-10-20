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
  const suffix = randomUUID().slice(0, 8);
  const dest = await command(context, owner, "/api/v1/warehouses", {
    client_operation_id: randomUUID(),
    entity_id: owner.entities[0].id,
    code: "CQ-" + suffix,
    name: "盘点待办仓-" + suffix,
    location_code: "CQ-A",
  });
  const f = await stockedCustomer(context, owner, reviewer, checker, {
    warehouse: dest.id,
    location: dest.location_id,
  });
  const candidates = await read(
    context,
    `/api/v1/stock-counts/candidates?entity_id=${f.entity}&warehouse_id=${f.warehouse}&search=${f.lot}`,
  );
  return {
    ...f,
    balance: candidates.items[0].id,
    warehouseName: "盘点待办仓-" + suffix,
  };
}
async function create(context: BrowserContext, owner: any, f: any) {
  return command(context, owner, "/api/v1/stock-counts", {
    client_operation_id: randomUUID(),
    entity_id: f.entity,
    warehouse_id: f.warehouse,
    balance_ids: [f.balance],
    reason: "盘点待办业务验收",
  });
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
  await expect(row).toContainText("所选主体批次盘点冻结中");
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
async function stock(context: BrowserContext, f: any, frozen: boolean) {
  const data = await read(
    context,
    `/api/v1/inventory/availability?entity_id=${f.entity}&warehouse_id=${f.warehouse}&sku_id=${f.product.sku_id}`,
  );
  expect(data.items[0].ineligibility_codes.includes("COUNT_FROZEN")).toBe(
    frozen,
  );
  expect(data.items[0].available).toBe(
    frozen ? "0.000000" : data.items[0].on_hand,
  );
  return data.items[0];
}
async function cleanup(context: BrowserContext, owner: any, ids: string[]) {
  for (const id of ids) {
    const data = await read(context, `/api/v1/stock-counts/${id}`);
    if (["COUNTING", "SUBMITTED", "APPROVED"].includes(data.document.state))
      await command(context, owner, `/api/v1/stock-counts/${id}/cancel`, {
        expected_version: data.document.version,
        reason: "验收结束取消未完成测试盘点",
      });
  }
}

test("count queue handles a loss and a zero-difference count with independent review and freeze until posting", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(180000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext();
  const ids: string[] = [],
    errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    const quality = await reviewer.newPage();
    quality.on("pageerror", (e) => errors.push(e.message));
    const f = await prepare(context, owner, reviewer, checker);
    for (const step of ["loss", "unchanged"]) {
      const doc = await create(context, owner, f);
      ids.push(doc.id);
      await page.goto("/");
      const queue = await find(page, f.warehouseName, "count_enter");
      await expect(
        queue.getByRole("row").filter({ hasText: doc.number }),
      ).toContainText("盘点待实盘");
      await open(page, doc.number, "盘点待实盘");
      await stock(context, f, true);
      await page
        .getByRole("button", { name: "录入实盘并提交", exact: true })
        .click();
      const dialog = page.getByRole("dialog");
      await dialog.getByLabel("实盘数量 1", { exact: true }).fill("8");
      await dialog.getByLabel("实物批号 1", { exact: true }).fill(f.lot);
      await dialog
        .getByLabel("实盘依据 1", { exact: true })
        .fill("实物八件，复盘确认");
      await save(page, "逐行录入真实盘点结果");
      await expect(
        page.getByRole("button", { name: "独立审核盘点", exact: true }),
      ).toHaveCount(0);
      await back(page, doc.number, "盘点待审核");
      await stock(context, f, true);
      await quality.goto("/");
      await open(quality, doc.number, "盘点待审核");
      await quality
        .getByRole("button", { name: "独立审核盘点", exact: true })
        .click();
      await save(quality, "独立复核数量与差异");
      await back(quality, doc.number, "盘点待过账");
      const before = await stock(context, f, true);
      expect(before.on_hand).toBe(step === "loss" ? "10.000000" : "8.000000");
      if (step === "loss") {
        await expect(quality.locator(".el-message")).toHaveCount(0);
        await quality.screenshot({
          path: "../../artifacts/count-queue-awaiting-post.png",
          fullPage: true,
          animations: "disabled",
        });
      }
      await open(quality, doc.number, "盘点待过账");
      await quality
        .getByRole("button", { name: "确认盘点过账", exact: true })
        .click();
      await save(quality, "确认结单并恢复库存作业");
      await expect(quality.getByTestId("count-result")).toBeVisible();
      await back(quality, doc.number);
      const after = await stock(context, f, false);
      expect(after.on_hand).toBe("8.000000");
      const detail = await read(context, `/api/v1/stock-counts/${doc.id}`);
      expect(detail.document.state).toBe("POSTED");
      expect(detail.result.changed_lines).toBe(step === "loss" ? 1 : 0);
      expect(detail.result.cost_delta).toBe(
        step === "loss" ? "-23.00800000" : "0.00000000",
      );
    }
    const sale = await command(context, owner, "/api/v1/retail/sales", {
      client_operation_id: randomUUID(),
      entity_id: f.entity,
      warehouse_id: f.warehouse,
      lines: [
        {
          sku_id: f.product.sku_id,
          qty: "1",
          unit_price: "20",
          tax_rate: "0.13",
        },
      ],
    });
    await command(context, owner, `/api/v1/retail/sales/${sale.id}/cancel`, {
      expected_version: "1",
      reason: "验证盘点结单后可正常预占，取消未收款测试单",
    });
    await stock(context, f, false);
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
    await cleanup(context, owner, ids);
    await reviewer.close();
  }
});

test("count queue supports cancellation, read-only original document and enterprise switch isolation", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(120000);
  let owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext(),
    viewerContext = await browser.newContext();
  const ids: string[] = [];
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    const f = await prepare(context, owner, reviewer, checker);
    const doc = await create(context, owner, f);
    ids.push(doc.id);
    await page.goto("/");
    await open(page, doc.number, "盘点待实盘");
    await page.getByRole("button", { name: "取消盘点", exact: true }).click();
    await save(page, "范围有误，取消本次盘点");
    await back(page, doc.number);
    await stock(context, f, false);
    const replacement = await create(context, owner, f);
    ids.push(replacement.id);
    const viewer = await wholesale(viewerContext, "demo.viewer");
    const viewPage = await viewerContext.newPage();
    await viewPage.goto("/");
    await open(viewPage, replacement.number, "盘点待实盘");
    for (const name of ["录入实盘并提交", "取消盘点"])
      await expect(
        viewPage.getByRole("button", { name, exact: true }),
      ).toHaveCount(0);
    const denied = await viewerContext.request.post(
      origin + `/api/v1/stock-counts/${replacement.id}/cancel`,
      {
        headers: { ...headers(viewer), "Idempotency-Key": randomUUID() },
        data: { expected_version: "1", reason: "只读用户不得取消盘点冻结" },
      },
    );
    expect(denied.status()).toBe(403);
    await open(page, replacement.number, "盘点待实盘");
    await page
      .getByRole("button", { name: "返回盘点列表", exact: true })
      .click();
    await page
      .getByRole("row")
      .filter({ hasText: replacement.number })
      .getByRole("button", { name: "盘点详情" })
      .click();
    await page.getByRole("combobox", { name: "切换企业" }).press("Enter");
    const other = owner.tenants.find((t: any) => t.id !== owner.tenant_id);
    await page.getByRole("option", { name: other.name, exact: true }).click();
    await expect(
      page.getByRole("heading", { name: replacement.number, exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "返回业务待办", exact: true }),
    ).toHaveCount(0);
    await expect(page.getByLabel("查找待办")).toHaveValue("");
    await expect(page.getByLabel("待办环节")).toHaveValue("all");
  } finally {
    owner = await wholesale(context, "demo.owner");
    await cleanup(context, owner, ids);
    await reviewer.close();
    await viewerContext.close();
  }
});
