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

async function openOrder(page: Page, number: string) {
  await page.goto("/");
  await nav(page, "采购入库");
  await page
    .getByRole("row")
    .filter({ hasText: number })
    .getByRole("button", { name: "查看采购单", exact: true })
    .click();
}

test("unreceived packaging purchase revision preserves units, requires independent review and posts revised amounts", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(120000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext(),
    viewerContext = await browser.newContext();
  const quality = await reviewer.newPage();
  const errors: string[] = [];
  for (const p of [page, quality])
    p.on("pageerror", (e) => errors.push(e.message));
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    const f = await stockedCustomer(context, owner, reviewer, checker);
    const path = `/api/v1/catalog/skus/${f.product.sku_id}/packaging`;
    const packs = await read(context, path);
    const pack = await command(context, owner, path, {
      expected_sku_version: packs.sku_version,
      unit_name: "箱",
      numerator: "3",
      denominator: "1",
      reason: "采购变更包装测试",
    });
    await command(reviewer, checker, `${path}/${pack.id}/review`, {
      expected_version: pack.version,
      decision: "APPROVE",
      evidence_ref: "REVISION-PACK",
      reason: "独立核对包装",
    });
    const created = await command(context, owner, "/api/v1/purchase-orders", {
      entity_id: f.entity,
      supplier_id: f.party.id,
      warehouse_id: f.warehouse,
      lines: [
        {
          sku_id: f.product.sku_id,
          packaging_id: pack.id,
          qty: "2",
          unit_price: "9",
          tax_rate: "0.13",
        },
      ],
    });
    await command(
      reviewer,
      checker,
      `/api/v1/purchase-orders/${created.id}/approve`,
      { expected_version: "1", reason: "初始采购审批" },
    );
    await openOrder(page, created.number);
    await page.getByRole("button", { name: "修改采购单", exact: true }).click();
    const form = page.getByRole("region", { name: "修改采购单", exact: true });
    await expect(
      form.getByRole("combobox", { name: /^交易单位 1/ }),
    ).toHaveValue(pack.id);
    await expect(form.getByLabel("采购数量", { exact: true })).toHaveValue(
      "2.000000",
    );
    await form.getByLabel("采购数量", { exact: true }).fill("3");
    await form.getByLabel("含税单价（元）", { exact: true }).fill("12");
    await form
      .getByLabel("变更原因", { exact: true })
      .fill("供应商确认调整为三箱，每箱十二元");
    await form
      .getByRole("button", { name: "保存变更并待审", exact: true })
      .click();
    await expect(form).not.toBeVisible();
    await expect(
      page.getByRole("alert").filter({ hasText: "采购内容已修改" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "登记到货", exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "审核采购单", exact: true }),
    ).toBeDisabled();
    const history = page.getByTestId("purchase-history");
    await expect(history).toContainText("供应商确认调整为三箱，每箱十二元");
    await expect(history.getByText("修改前", { exact: true })).toBeVisible();
    await expect(history.getByText("修改后", { exact: true })).toBeVisible();
    await expect(page.locator(".el-message")).toHaveCount(0, { timeout: 8000 });
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: "../../artifacts/purchase-revised-pending.png",
      fullPage: true,
      animations: "disabled",
    });
    let doc = await read(context, `/api/v1/purchase-orders/${created.id}`);
    expect(doc.order.revision_no).toBe(2);
    expect(doc.lines[0].qty).toBe("9.000000");
    expect(doc.lines[0].gross_amount).toBe("36.00");
    expect(doc.lines[0].package_snapshot.packaging_id).toBe(pack.id);

    await openOrder(quality, created.number);
    await expect(
      quality.getByRole("button", { name: "修改采购单", exact: true }),
    ).toHaveCount(0);
    await quality
      .getByRole("button", { name: "审核采购单", exact: true })
      .click();
    await quality
      .getByRole("dialog")
      .getByLabel("补充说明", { exact: true })
      .fill("独立核对修改前后数量及单价");
    await quality
      .getByRole("button", { name: "确认审核", exact: true })
      .click();
    await expect(quality.getByRole("dialog")).not.toBeVisible();
    await page.getByRole("button", { name: "刷新单据", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "登记到货", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("alert").filter({ hasText: "采购内容已修改" }),
    ).toHaveCount(0);
    doc = await read(context, `/api/v1/purchase-orders/${created.id}`);
    const choices = await read(
      context,
      `/api/v1/purchasing/options?entity_id=${f.entity}`,
    );
    const loc = choices.locations.find(
      (l: any) => l.warehouse_id === f.warehouse,
    );
    const arrival = await command(
      context,
      owner,
      `/api/v1/purchase-orders/${created.id}/receipts`,
      {
        expected_version: doc.order.version,
        shipment_ref: "REVISED-" + f.suffix,
        transport_note: "按修改后的采购约定到货一箱",
        lines: [
          {
            order_line_id: doc.lines[0].id,
            location_id: loc.id,
            qty: "3",
            lot_no: "REV-" + f.suffix,
            mfg_date: `${f.year}-01-01`,
            expiry_date: `${f.year + 1}-12-31`,
          },
        ],
      },
    );
    doc = await read(context, `/api/v1/purchase-orders/${created.id}`);
    const inspection = await command(
      reviewer,
      checker,
      `/api/v1/receipts/${arrival.id}/inspections`,
      {
        expected_version: "1",
        note: "按基本单位验收三件",
        lines: [
          {
            receipt_line_id: doc.receipts[0].lines[0].id,
            qualified_qty: "3",
            rejected_qty: "0",
          },
        ],
      },
    );
    await command(
      reviewer,
      checker,
      `/api/v1/receipt-postings/${inspection.receipt_posting_id}/post`,
      {
        expected_version: "1",
        inspection_id: inspection.inspection_id,
        posting_segment_id: inspection.receipt_posting_id,
      },
    );
    await page.getByRole("button", { name: "刷新单据", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "修改采购单", exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("heading", {
        name: "到货记录 · REVISED-" + f.suffix,
        exact: true,
      }),
    ).toBeVisible();
    await page
      .getByTestId("purchase-history")
      .locator("details")
      .filter({ hasText: "供应商确认调整为三箱" })
      .locator("summary")
      .click();
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: "../../artifacts/purchase-revised-posted.png",
      fullPage: true,
      animations: "disabled",
    });
    const stock = await read(
      context,
      `/api/v1/inventory/availability?entity_id=${f.entity}&warehouse_id=${f.warehouse}&sku_id=${f.product.sku_id}`,
    );
    expect(
      stock.items.reduce((sum: number, r: any) => sum + Number(r.on_hand), 0),
    ).toBe(13);
    const viewer = await wholesale(viewerContext, "demo.viewer");
    const viewPage = await viewerContext.newPage();
    await openOrder(viewPage, created.number);
    await expect(viewPage.getByTestId("purchase-history")).toContainText(
      "供应商确认调整为三箱",
    );
    await expect(
      viewPage.getByRole("button", { name: "修改采购单", exact: true }),
    ).toHaveCount(0);
    const denied = await viewerContext.request.post(
      origin + `/api/v1/purchase-orders/${created.id}/revise`,
      {
        headers: { ...headers(viewer), "Idempotency-Key": randomUUID() },
        data: {
          expected_version: doc.order.version,
          supplier_id: f.party.id,
          warehouse_id: f.warehouse,
          reason: "只读不可修改",
          lines: [{ sku_id: f.product.sku_id, qty: "1", unit_price: "1" }],
        },
      },
    );
    expect(denied.status()).toBe(403);
    expect(errors).toEqual([]);
  } finally {
    await reviewer.close();
    await viewerContext.close();
  }
});
