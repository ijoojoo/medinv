import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { resolve } from "node:path";
import { mkdir, writeFile, readFile } from "node:fs/promises";
import {
  wholesale,
  command,
  read,
  origin,
  stockedCustomer,
} from "./business-helpers";
import { headers } from "./helpers";

test("inspection report filters, historical CSV download, source navigation and narrow layout", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(240000);
  const reviewer = await browser.newContext();
  let owner = await wholesale(context, "demo.owner"),
    quality = await wholesale(reviewer, "demo.quality");
  const original = { owner, quality },
    suffix = randomUUID().slice(0, 8),
    folder = resolve("../../artifacts/quality-records"),
    shots = resolve("../../outputs/document-reports/quality");
  await mkdir(folder, { recursive: true });
  await mkdir(shots, { recursive: true });
  const file = resolve(folder, `tenant-${suffix}.json`);
  await writeFile(
    file,
    JSON.stringify({
      request_id: randomUUID(),
      tenant_name: `质量记录验收-${suffix}`,
      entity_name: `质量记录软件测试主体-${suffix}`,
      credit_code: `TEST-${suffix}`,
      warehouse_code: "TEST",
      warehouse_name: "测试仓库",
      location_code: "A1",
      reason: "隔离企业软件测试，非真实业务资料",
      members: [
        { principal_id: owner.principal_id, role_codes: ["OWNER"] },
        { principal_id: quality.principal_id, role_codes: ["QUALITY"] },
      ],
    }),
  );
  const result = await promisify(execFile)(
    resolve("../../backend/.venv/Scripts/python.exe"),
    [resolve("../../scripts/provision_enterprise.py"), file],
    { env: { ...process.env, PYTHONUTF8: "1" }, timeout: 30000 },
  );
  const tenant = JSON.parse(result.stdout);
  async function switchTo(ctx: typeof context, current: any, id: string) {
    const response = await ctx.request.post(origin + "/auth/tenant", {
      headers: headers(current),
      data: { tenant_id: id },
    });
    expect(response.status()).toBe(200);
    return (await response.json()).data;
  }
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    owner = await switchTo(context, owner, tenant.tenant_id);
    quality = await switchTo(reviewer, quality, tenant.tenant_id);
    const seeded = await stockedCustomer(context, owner, reviewer, quality);
    const order = await command(context, owner, "/api/v1/purchase-orders", {
      entity_id: tenant.entity_id,
      supplier_id: seeded.party.id,
      warehouse_id: tenant.warehouse_id,
      lines: [
        {
          sku_id: seeded.product.sku_id,
          qty: "6",
          unit_price: "13",
          tax_rate: "0.13",
        },
      ],
    });
    await command(
      reviewer,
      quality,
      `/api/v1/purchase-orders/${order.id}/approve`,
      {
        expected_version: "1",
        reason: "质量报表软件验收",
      },
    );
    let doc = await read(context, `/api/v1/purchase-orders/${order.id}`);
    const lot = "REPORT-" + suffix;
    const receipt = await command(
      context,
      owner,
      `/api/v1/purchase-orders/${order.id}/receipts`,
      {
        expected_version: doc.order.version,
        shipment_ref: "REPORT-DELIVERY-" + suffix,
        transport_note: "软件测试",
        lines: [
          {
            order_line_id: doc.lines[0].id,
            location_id: tenant.location_id,
            qty: "6",
            lot_no: lot,
            mfg_date: `${seeded.year}-01-01`,
            expiry_date: `${seeded.year + 1}-12-31`,
          },
        ],
      },
    );
    for (const [qualified, rejected] of [
      ["2", "0"],
      ["1", "1"],
      ["0", "2"],
    ]) {
      doc = await read(context, `/api/v1/purchase-orders/${order.id}`);
      await command(
        reviewer,
        quality,
        `/api/v1/receipts/${receipt.id}/inspections`,
        {
          expected_version: doc.receipts[0].version,
          note: '=软件测试,"含引号"\n第二行',
          lines: [
            {
              receipt_line_id: doc.receipts[0].lines[0].id,
              qualified_qty: qualified,
              rejected_qty: rejected,
            },
          ],
        },
      );
    }
    const url = `/workspace/quality-records?tenant=${tenant.tenant_id}&entity=${tenant.entity_id}`;
    await page.goto(url);
    const report = page.getByRole("region", {
        name: "质量记录查询",
        exact: true,
      }),
      summary = report.locator(".report-pager"),
      rows = report.locator("tbody tr"),
      exportButton = report.getByRole("button", {
        name: "导出全部筛选结果",
        exact: true,
      });
    await expect(summary).toContainText("共 4 张单据");
    await report.getByRole("tab", { name: "单据明细", exact: true }).click();
    await expect(summary).toContainText("共 4 条明细");
    await report.getByLabel("批号", { exact: true }).fill(lot);
    await expect(exportButton).toBeDisabled();
    await report.getByRole("button", { name: "查询", exact: true }).click();
    await expect(summary).toContainText("共 3 条明细");
    await expect(report.locator(".report-unit-totals")).toContainText(
      "合格 3.000000 / 拒收 3.000000",
    );
    await expect(rows).toHaveCount(3);
    await report
      .getByRole("combobox", { name: "状态／结果", exact: true })
      .selectOption("MIXED");
    await report.getByRole("button", { name: "查询", exact: true }).click();
    await expect(summary).toContainText("共 1 条明细");
    await expect(rows).toContainText("部分拒收");
    const downloadEvent = page.waitForEvent("download");
    await exportButton.click();
    const download = await downloadEvent;
    expect(download.suggestedFilename()).toMatch(/^采购验收-明细-.*\.csv$/);
    const csvFile = resolve(folder, "filtered-inspections.csv");
    await download.saveAs(csvFile);
    const csv = await readFile(csvFile, "utf-8");
    expect(csv.startsWith("\ufeff")).toBe(true);
    expect(csv).toContain("商品名称");
    expect(csv).toContain(seeded.name);
    expect(csv).toContain(lot);
    expect(csv).toContain('"\'=软件测试,""含引号""\n第二行"');
    expect(csv).toContain("'1.000000");
    expect(csv).not.toContain("全部拒收");
    await expect(page.locator(".el-message")).toHaveCount(0, { timeout: 8000 });
    await page.screenshot({
      path: resolve(shots, "质量记录-筛选导出.png"),
      fullPage: true,
    });
    await report.getByRole("tab", { name: "单据汇总", exact: true }).click();
    await expect(summary).toContainText("共 1 张单据");
    await report
      .getByRole("button", { name: "查看关联记录", exact: true })
      .click();
    const linked = page.getByRole("dialog", { name: "查看关联记录" });
    await linked
      .locator(".report-related")
      .filter({
        has: page.getByRole("heading", { name: "采购订单", exact: true }),
      })
      .getByRole("button", { name: doc.order.number, exact: true })
      .click();
    await expect(page).toHaveURL(new RegExp(`q_document_id=${order.id}`));
    await expect(
      page
        .locator(".document-reports:visible")
        .getByRole("tab", { name: "单据明细", exact: true }),
    ).toHaveAttribute("aria-selected", "true");
    await page.goto(url);
    await expect(summary).toContainText("共 4 张单据");
    await report.getByRole("button", { name: "近30天", exact: true }).click();
    await expect(summary).toContainText("共 4 张单据");
    await report.getByLabel("往来单位", { exact: true }).fill("不存在的单位");
    await report.getByRole("button", { name: "查询", exact: true }).click();
    await expect(summary).toContainText("共 0 张单据");
    await expect(exportButton).toBeDisabled();
    await report.getByRole("button", { name: "重置", exact: true }).click();
    await expect(summary).toContainText("共 4 张单据");
    await page.setViewportSize({ width: 768, height: 1024 });
    await page.locator(".main-shell").evaluate((el) => el.scrollTo(0, 0));
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: resolve(shots, "质量记录-窄屏.png"),
      fullPage: true,
    });
    expect(errors).toEqual([]);
    await writeFile(
      resolve(folder, "browser-evidence.json"),
      JSON.stringify(
        {
          tenant_id: tenant.tenant_id,
          entity_id: tenant.entity_id,
          order_id: order.id,
          receipt_id: receipt.id,
          lot,
          inspections: 4,
          filtered_inspections: 3,
          qualified_qty: "3",
          rejected_qty: "3",
          mixed_export_rows: 1,
          browser_errors: errors,
          narrow_width: 768,
        },
        null,
        2,
      ),
    );
  } finally {
    owner = await switchTo(context, owner, original.owner.tenant_id);
    quality = await switchTo(reviewer, quality, original.quality.tenant_id);
    await reviewer.close();
  }
});
