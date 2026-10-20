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

async function save(page: Page, reason: string) {
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("操作说明").fill(reason);
  await dialog.getByRole("button", { name: "保存", exact: true }).click();
  await expect(dialog).not.toBeVisible();
}

test("quarantined transfer stock is independently released then written off through quality UI", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(180000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext();
  const checker = await wholesale(reviewer, "demo.quality");
  const qualityPage = await reviewer.newPage();
  try {
    const f = await stockedCustomer(context, owner, reviewer, checker);
    const dest = await command(context, owner, "/api/v1/warehouses", {
      client_operation_id: randomUUID(),
      entity_id: f.entity,
      code: "QA-" + f.suffix,
      name: "质量验收仓-" + f.suffix,
      location_code: "QA-A",
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
      reason: "质量处置闭环验收",
      lines: [{ balance_id: candidates.items[0].id, qty: "10" }],
    });
    await command(
      reviewer,
      checker,
      `/api/v1/transfers/${transfer.id}/review`,
      { expected_version: "1", decision: "APPROVE", reason: "核对调拨" },
    );
    await command(context, owner, `/api/v1/transfers/${transfer.id}/dispatch`, {
      expected_version: "2",
      reason: "实物发出",
    });
    const incoming = await read(context, `/api/v1/transfers/${transfer.id}`);
    await command(context, owner, `/api/v1/transfers/${transfer.id}/receive`, {
      client_operation_id: randomUUID(),
      expected_version: incoming.document.version,
      reason: "到货外观待复验，隔离待验",
      lines: [
        {
          line_id: incoming.lines[0].id,
          qty: "10",
          lot_no: f.lot,
          quality_status: "QUARANTINE",
        },
      ],
    });
    await page.goto("/");
    await qualityPage.goto("/");
    await nav(page, "质量处置");
    await nav(qualityPage, "质量处置");
    for (const [kind, qty] of [
      ["RELEASE", "6"],
      ["WRITE_OFF", "4"],
    ]) {
      await page
        .getByRole("button", { name: "新建质量处置", exact: true })
        .click();
      await page.getByLabel("检索批次").fill(f.lot);
      await page
        .getByRole("button", { name: "查询待处置库存", exact: true })
        .click();
      const row = page.locator(".el-table__body tr").filter({ hasText: f.lot });
      await expect(row).toHaveCount(1);
      await row.getByRole("button", { name: "选择", exact: true }).click();
      await page.getByLabel("处置方式", { exact: true }).selectOption(kind!);
      await page.getByLabel("处置数量", { exact: true }).fill(qty!);
      await page.getByLabel("实物批号", { exact: true }).fill(f.lot);
      await page
        .getByLabel("申请原因", { exact: true })
        .fill(kind === "RELEASE" ? "复验6件合格" : "其余4件包装破损报损");
      await page
        .getByLabel("复验 / 处置依据", { exact: true })
        .fill("软件验收记录 QA-" + f.suffix);
      await page
        .getByRole("button", { name: "提交处置申请", exact: true })
        .click();
      await expect(page.locator(".el-tag")).toHaveText("待审核");
      await expect(
        page.getByRole("button", { name: "独立审核处置", exact: true }),
      ).toHaveCount(0);
      const list = await read(
        context,
        `/api/v1/quality-dispositions?entity_id=${f.entity}`,
      );
      const doc = list.items.find(
        (d: any) => d.lot_no === f.lot && d.kind === kind,
      );
      await qualityPage
        .getByRole("button", { name: "刷新质量处置", exact: true })
        .click();
      await qualityPage
        .getByRole("button", { name: doc.number, exact: true })
        .click();
      await qualityPage
        .getByRole("button", { name: "独立审核处置", exact: true })
        .click();
      await save(qualityPage, "独立复核实物、数量和处置依据");
      await expect(qualityPage.locator(".el-tag")).toHaveText("待过账");
      await qualityPage
        .getByRole("button", { name: "确认处置过账", exact: true })
        .click();
      await save(
        qualityPage,
        kind === "RELEASE"
          ? "复验合格，完成放行"
          : "确认报损实物已处置，登记扣账",
      );
      await expect(qualityPage.locator(".el-tag")).toHaveText("已过账");
      await expect(qualityPage.locator(".facts dt")).toContainText([
        "处置方式",
        "来源质量",
        "商品",
        "批号",
        "仓库 / 库位",
        "处置数量",
        "申请原因",
        "复验 / 处置依据",
      ]);
      if (kind === "WRITE_OFF")
        await expect(
          qualityPage.getByText(/扣减批次成本 46.01600000 元/),
        ).toBeVisible();
      await expect(qualityPage.locator(".el-message")).toHaveCount(0, {
        timeout: 8000,
      });
      await qualityPage.screenshot({
        path: `../../artifacts/quality-${kind.toLowerCase()}.png`,
        fullPage: true,
        animations: "disabled",
      });
      await page
        .getByRole("button", { name: "返回处置列表", exact: true })
        .click();
      await qualityPage
        .getByRole("button", { name: "返回处置列表", exact: true })
        .click();
    }
    const stock = await read(
      context,
      `/api/v1/inventory/availability?entity_id=${f.entity}&warehouse_id=${dest.id}&sku_id=${f.product.sku_id}`,
    );
    expect(stock.items.find((r: any) => r.eligible).available).toBe("6.000000");
    const remaining = await read(
      context,
      `/api/v1/quality-dispositions/candidates?entity_id=${f.entity}&search=${f.lot}`,
    );
    expect(remaining.items).toHaveLength(0);
    const sale = await command(context, owner, "/api/v1/retail/sales", {
      client_operation_id: randomUUID(),
      entity_id: f.entity,
      warehouse_id: dest.id,
      lines: [
        {
          sku_id: f.product.sku_id,
          qty: "6",
          unit_price: "20",
          tax_rate: "0.13",
        },
      ],
    });
    await command(context, owner, `/api/v1/retail/sales/${sale.id}/cancel`, {
      expected_version: "1",
      reason: "释放验收预占",
    });
  } finally {
    await reviewer.close();
  }
});

test("viewer quality page is read only and mutation is rejected by API", async ({
  page,
  context,
}) => {
  const viewer = await wholesale(context, "demo.viewer");
  await page.goto("/");
  await nav(page, "质量处置");
  await expect(
    page.getByRole("button", { name: "刷新质量处置", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "新建质量处置", exact: true }),
  ).toHaveCount(0);
  const response = await context.request.post(
    origin + "/api/v1/quality-dispositions",
    {
      headers: { ...headers(viewer), "Idempotency-Key": randomUUID() },
      data: {
        client_operation_id: randomUUID(),
        entity_id: viewer.entities[0].id,
        balance_id: randomUUID(),
        kind: "RELEASE",
        qty: "1",
        lot_no: "X",
        reason: "拒绝越权",
        evidence: "X",
      },
    },
  );
  expect(response.status()).toBe(403);
});
