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
  await nav(page, "批发销售");
  await page
    .getByRole("row")
    .filter({ hasText: number })
    .getByRole("button", { name: "查看销售单", exact: true })
    .click();
}

test("sales amendment releases credit, preserves packaging and independently resumes normal shipment", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(150000);
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
    const packsPath = `/api/v1/catalog/skus/${f.product.sku_id}/packaging`;
    const packOptions = await read(context, packsPath);
    const pack = await command(context, owner, packsPath, {
      expected_sku_version: packOptions.sku_version,
      unit_name: "箱",
      numerator: "3",
      denominator: "1",
      reason: "核对销售交易包装",
    });
    await command(reviewer, checker, `${packsPath}/${pack.id}/review`, {
      expected_version: pack.version,
      decision: "APPROVE",
      evidence_ref: "SALES-PACK",
      reason: "独立确认每箱三件",
    });
    const creditPath = `/api/v1/customer-credit/${f.party.id}?entity_id=${f.entity}`;
    await command(context, owner, creditPath, {
      expected_version: "0",
      credit_limit: "1000",
      valid_until: `${f.year + 1}-12-31`,
      reason: "演示客户授信",
    });
    const created = await command(context, owner, "/api/v1/sales-orders", {
      entity_id: f.entity,
      customer_id: f.party.id,
      warehouse_id: f.warehouse,
      delivery_address: "原收货地址",
      credit_days: 30,
      minimum_shelf_days: 0,
      lines: [
        {
          sku_id: f.product.sku_id,
          packaging_id: pack.id,
          qty: "2",
          unit_price: "30",
          tax_rate: "0.13",
        },
      ],
    });
    const path = `/api/v1/sales-orders/${created.id}`;
    await command(reviewer, checker, path + "/approve", {
      expected_version: "1",
      reason: "原销售约定审核",
    });
    expect((await read(context, creditPath)).exposure).toBe("60.00");
    await openOrder(page, created.number);
    await page.getByRole("button", { name: "修改销售单", exact: true }).click();
    let dialog = page.getByRole("region", { name: "修改销售单", exact: true });
    await expect(
      dialog.getByRole("combobox", { name: /^交易单位 1/ }),
    ).toHaveValue(pack.id);
    await expect(dialog.getByLabel("销售数量", { exact: true })).toHaveValue(
      "2.000000",
    );
    await dialog.getByLabel("销售数量", { exact: true }).fill("3");
    await dialog.getByLabel("含税单价（元）", { exact: true }).fill("40");
    await dialog
      .getByLabel("收货地址", { exact: true })
      .fill("客户确认的新收货地址");
    await dialog.getByLabel("账期（天）", { exact: true }).fill("15");
    await dialog
      .getByLabel("变更原因", { exact: true })
      .fill("客户将两箱改为三箱，每箱四十元并确认新地址");
    await dialog
      .getByRole("button", { name: "保存变更并待审", exact: true })
      .click();
    await expect(dialog).not.toBeVisible();
    await expect(
      page.getByRole("alert").filter({ hasText: "销售内容已修改" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "审核销售单", exact: true }),
    ).toBeDisabled();
    await expect(
      page.getByRole("button", { name: "分配发货批次", exact: true }),
    ).toHaveCount(0);
    expect((await read(context, creditPath)).exposure).toBe("0.00");
    let doc = await read(context, path);
    expect(doc.order.revision_no).toBe(2);
    expect(doc.lines[0].qty).toBe("9.000000");
    expect(doc.lines[0].gross_amount).toBe("120.00");
    expect(doc.lines[0].package_snapshot.packaging_id).toBe(pack.id);
    const history = page.getByTestId("sales-history");
    await expect(history).toContainText("客户将两箱改为三箱");
    await expect(history.getByText("修改前", { exact: true })).toBeVisible();
    await expect(history.getByText("修改后", { exact: true })).toBeVisible();
    await expect(page.locator(".el-message")).toHaveCount(0);
    await page.screenshot({
      path: "../../artifacts/sales-revised-pending.png",
      fullPage: true,
      animations: "disabled",
    });

    await openOrder(quality, created.number);
    await expect(
      quality.getByRole("button", { name: "修改销售单", exact: true }),
    ).toHaveCount(0);
    await quality
      .getByRole("button", { name: "审核销售单", exact: true })
      .click();
    dialog = quality.getByRole("dialog");
    await dialog.getByLabel("操作说明").fill("核对客户变更内容和信用额度");
    await dialog.getByRole("button", { name: "确认审核", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    expect((await read(context, creditPath)).exposure).toBe("120.00");
    await page
      .getByRole("button", { name: "刷新销售详情", exact: true })
      .click();
    await page
      .getByRole("button", { name: "分配发货批次", exact: true })
      .click();
    dialog = page.getByRole("dialog");
    await dialog.getByLabel("本次发货数量").fill("9");
    await dialog.getByRole("button", { name: "确认分配", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await expect(
      page.getByRole("button", { name: "修改销售单", exact: true }),
    ).toHaveCount(0);
    await page.getByRole("button", { name: "登记拣货", exact: true }).click();
    dialog = page.getByRole("dialog");
    await dialog.getByLabel("实物批号").fill(f.lot);
    await dialog.getByLabel("实物数量").fill("9");
    await dialog.getByLabel("核查说明").fill("按新约定拣货三箱共九件");
    await dialog.getByRole("button", { name: "保存拣货", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await quality
      .getByRole("button", { name: "刷新销售详情", exact: true })
      .click();
    await quality
      .getByRole("button", { name: "出库复核", exact: true })
      .click();
    dialog = quality.getByRole("dialog");
    await dialog.getByLabel("实物批号").fill(f.lot);
    await dialog.getByLabel("实物数量").fill("9");
    await dialog.getByLabel("核查说明").fill("独立核对修改后批号数量");
    await dialog.getByLabel("物流 / 交接凭据").fill("SALES-REV-" + f.suffix);
    await dialog.getByRole("button", { name: "确认复核", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await quality
      .getByRole("button", { name: "销售出库", exact: true })
      .click();
    dialog = quality.getByRole("dialog");
    await dialog.getByRole("button", { name: "确认出库", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await expect(quality.locator(".shipment-card")).toContainText("已出库");
    await expect(quality.locator(".el-message")).toHaveCount(0);
    await quality.screenshot({
      path: "../../artifacts/sales-revised-posted.png",
      fullPage: true,
      animations: "disabled",
    });
    doc = await read(context, path);
    expect(doc.order.state).toBe("FULFILLED");
    expect(doc.order.can_revise).toBe(false);
    const credit = await read(context, creditPath);
    expect(credit.exposure).toBe("0.00");
    expect(credit.receivable).toBe("120.00");
    const stock = await read(
      context,
      `/api/v1/inventory/availability?entity_id=${f.entity}&warehouse_id=${f.warehouse}&sku_id=${f.product.sku_id}`,
    );
    expect(stock.items[0].on_hand).toBe("1.000000");
    expect(stock.items[0].reserved).toBe("0.000000");
    const ar = await read(context, `/api/v1/receivables?entity_id=${f.entity}`);
    expect(
      ar.items.find((r: any) => r.customer_id === f.party.id).gross_amount,
    ).toBe("120.00");
    const viewer = await wholesale(viewerContext, "demo.viewer");
    const viewerPage = await viewerContext.newPage();
    await openOrder(viewerPage, created.number);
    await expect(viewerPage.getByTestId("sales-history")).toContainText(
      "销售变更与审核记录",
    );
    await expect(
      viewerPage.getByRole("button", { name: "修改销售单", exact: true }),
    ).toHaveCount(0);
    const denied = await viewerContext.request.post(origin + path + "/revise", {
      headers: { ...headers(viewer), "Idempotency-Key": randomUUID() },
      data: {
        expected_version: doc.order.version,
        customer_id: f.party.id,
        warehouse_id: f.warehouse,
        delivery_address: "越权修改",
        lines: [{ sku_id: f.product.sku_id, qty: "1", unit_price: "1" }],
        reason: "越权验收",
      },
    });
    expect(denied.status()).toBe(403);
    expect(errors).toEqual([]);
  } finally {
    await reviewer.close();
    await viewerContext.close();
  }
});
