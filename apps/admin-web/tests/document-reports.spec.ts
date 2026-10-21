import { test, expect } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { wholesale, stockedCustomer, read, nav } from "./business-helpers";
const report = (page: any) => page.locator(".document-reports:visible");
async function settled(page: any) {
  await expect(
    report(page).getByRole("button", { name: "查询", exact: true }),
  ).toBeEnabled();
  await expect(report(page).locator(".report-metrics")).toBeVisible();
}

test("independent purchase reports drill down, configure columns, link records, export and graph filtered facts", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(180000);
  const checker = await browser.newContext();
  try {
    const owner = await wholesale(context, "demo.owner"),
      quality = await wholesale(checker, "demo.quality");
    const f = await stockedCustomer(context, owner, checker, quality);
    const doc = await read(context, `/api/v1/purchase-orders/${f.purchase.id}`);
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(
      `/workspace/purchase-reports?tenant=${owner.tenant_id}&entity=${f.entity}&kind=purchase&q_order_id=${f.purchase.id}`,
    );
    const root = report(page);
    await settled(page);
    await expect(
      root.getByRole("tab", { name: "单据汇总", exact: true }),
    ).toHaveAttribute("aria-selected", "true");
    await expect(root.locator(".report-pager")).toContainText("共 1 张单据");
    await expect(root.locator(".report-metrics")).toContainText("130.00");
    await expect(
      root.getByRole("button", { name: "新建采购订单" }),
    ).toHaveCount(0);
    await root.getByRole("button", { name: "配置字段" }).click();
    const drawer = page.getByRole("dialog", { name: "配置查询字段" });
    await drawer.getByRole("checkbox", { name: "仓库", exact: true }).uncheck();
    await drawer.getByRole("button", { name: "往来单位上移" }).click();
    await drawer.getByRole("button", { name: "保存配置" }).click();
    await expect(root.locator("th").filter({ hasText: /^仓库$/ })).toHaveCount(
      0,
    );
    await root
      .getByRole("button", { name: doc.order.number, exact: true })
      .click();
    await settled(page);
    await expect(page).toHaveURL(new RegExp(`q_document_id=${f.purchase.id}`));
    await expect(
      root.getByRole("tab", { name: "单据明细", exact: true }),
    ).toHaveAttribute("aria-selected", "true");
    await expect(root.locator("tbody")).toContainText(f.name);
    await expect(root.locator("th").filter({ hasText: "操作" })).toHaveCount(0);
    await expect(root.locator("th").filter({ hasText: /^仓库$/ })).toHaveCount(
      1,
    );
    const download = page.waitForEvent("download");
    await root.getByRole("button", { name: "导出全部筛选结果" }).click();
    expect((await download).suggestedFilename()).toMatch(
      /采购订单-明细-.*\.csv/,
    );
    await page.reload();
    await settled(page);
    await expect(
      root.getByRole("tab", { name: "单据明细", exact: true }),
    ).toHaveAttribute("aria-selected", "true");
    await root.getByRole("tab", { name: "单据汇总", exact: true }).click();
    await settled(page);
    await expect(root.locator("th").filter({ hasText: /^仓库$/ })).toHaveCount(
      0,
    );
    await root
      .getByRole("button", { name: "查看关联记录", exact: true })
      .click();
    const related = page.getByRole("dialog", { name: "查看关联记录" });
    await expect(
      related.getByRole("heading", { name: "采购验收", exact: true }),
    ).toBeVisible();
    await related
      .locator(".report-related")
      .filter({
        has: page.getByRole("heading", { name: "采购验收", exact: true }),
      })
      .getByRole("button")
      .first()
      .click();
    await settled(page);
    await expect(root.getByRole("combobox", { name: "记录类型" })).toHaveValue(
      "inspection",
    );
    await expect(root.locator("tbody")).toContainText("10.000000");
    await expect(root.locator("th").filter({ hasText: "已入库" })).toHaveCount(
      0,
    );
    await root.getByRole("tab", { name: "图形化报表" }).click();
    await settled(page);
    await root.getByLabel("分组维度", { exact: true }).selectOption("product");
    await settled(page);
    await expect(root.locator(".report-chart")).toContainText(f.name);
    for (const type of ["line", "donut", "table", "bar"]) {
      await root.getByLabel("图形样式").selectOption(type);
      await expect(root.locator(".report-chart")).toBeVisible();
    }
    await root.getByLabel("统计指标").selectOption("qty");
    await settled(page);
    await root.getByLabel("基本单位", { exact: true }).selectOption("个");
    await settled(page);
    await expect(root.locator(".report-chart")).toContainText("10.000000");
    await mkdir("../../outputs/document-reports", { recursive: true });
    await page.screenshot({
      path: "../../outputs/document-reports/验收图形报表.png",
      fullPage: true,
    });
    await root.getByRole("tab", { name: "单据汇总", exact: true }).click();
    await settled(page);
    await root.getByLabel("综合查询", { exact: true }).fill("does-not-exist");
    await expect(
      root.getByRole("button", { name: "导出全部筛选结果" }),
    ).toBeDisabled();
    await root.getByRole("button", { name: "查询", exact: true }).click();
    await settled(page);
    await expect(root.locator(".report-pager")).toContainText("共 0 张单据");
    await root.getByLabel("综合查询", { exact: true }).fill("");
    await root.getByRole("button", { name: "查询", exact: true }).click();
    await settled(page);
    for (const width of [390, 768, 1440, 2560]) {
      await page.setViewportSize({ width, height: 1000 });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      await page.screenshot({
        path: `../../outputs/document-reports/单据汇总-${width}.png`,
        fullPage: true,
      });
    }
    await page.setViewportSize({ width: 390, height: 1000 });
    await root.locator(".report-table").scrollIntoViewIfNeeded();
    await expect(
      root.locator(".report-table .el-table-fixed-column--left"),
    ).toHaveCount(0);
    await expect(
      root.locator(".report-table .el-table-fixed-column--right"),
    ).toHaveCount(0);
    await page.screenshot({
      path: "../../outputs/document-reports/窄屏表格.png",
      fullPage: true,
    });
    expect(errors).toEqual([]);
  } finally {
    await checker.close();
  }
});

test("business record buttons open independent reports and query failures do not expose stale results", async ({
  page,
  context,
}) => {
  await wholesale(context, "demo.owner");
  await page.goto("/");
  await nav(page, "采购入库");
  const business = page.locator(".purchase-worklist:visible");
  await expect(
    business.getByRole("button", { name: "采购记录", exact: true }),
  ).toBeVisible();
  await business.getByRole("button", { name: "采购记录", exact: true }).click();
  await settled(page);
  await expect(page).toHaveURL(/workspace\/purchase-reports/);
  const root = report(page);
  await root.getByLabel("综合查询").fill("query-does-not-exist");
  await root.getByRole("button", { name: "查询", exact: true }).click();
  await settled(page);
  await nav(page, "销售出库");
  await page.getByRole("button", { name: "销售记录查询", exact: true }).click();
  await settled(page);
  await expect(root.getByRole("combobox", { name: "记录类型" })).toHaveValue(
    "sale",
  );
  await root
    .getByRole("combobox", { name: "记录类型" })
    .selectOption("outbound");
  await settled(page);
  await expect(root.locator(".report-context")).toContainText("实际金额");
  await nav(page, "采购记录查询");
  await settled(page);
  await expect(root.getByLabel("综合查询")).toHaveValue("query-does-not-exist");
  await page.route(
    "**/api/v1/document-reports/purchase?*",
    (route) =>
      route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({
          error: { code: "TEST", message: "测试查询失败" },
        }),
      }),
    { times: 1 },
  );
  await root.getByRole("button", { name: "查询", exact: true }).click();
  await expect(
    root.getByText("记录查询失败，未展示旧数据；请重新查询。"),
  ).toBeVisible();
  await expect(root.locator(".report-metrics")).toHaveCount(0);
  await expect(
    root.getByRole("button", { name: "导出全部筛选结果" }),
  ).toBeDisabled();
  await root.getByRole("button", { name: "查询", exact: true }).click();
  await settled(page);
});

test("report chart and export HTTP APIs use read permission with no mutation buttons", async ({
  page,
  context,
}) => {
  const viewer = await wholesale(context, "demo.viewer");
  await page.goto(
    `/workspace/sales-reports?tenant=${viewer.tenant_id}&entity=${viewer.entities[0].id}`,
  );
  await settled(page);
  const root = report(page);
  await expect(
    root.getByRole("button", { name: /新建|保存单据|审核|确认出库/ }),
  ).toHaveCount(0);
  await root.getByRole("tab", { name: "图形化报表" }).click();
  await settled(page);
  await expect(root.locator(".report-chart")).toBeVisible();
  const response = await context.request.post("/api/v1/document-reports/sale", {
    data: { entity_id: viewer.entities[0].id },
  });
  expect(response.status()).not.toBe(200);
});
