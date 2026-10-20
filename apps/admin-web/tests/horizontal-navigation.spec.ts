import { test, expect } from "@playwright/test";
import { wholesale, nav } from "./business-helpers";

test("horizontal categories, remembered sections and keyboard navigation remain connected to business pages", async ({
  page,
  context,
}) => {
  test.setTimeout(60000);
  const current = await wholesale(context, "demo.owner");
  const faults: string[] = [];
  page.on("pageerror", (e) => faults.push(e.message));
  await page.goto("/");
  const primary = page.getByRole("navigation", {
    name: "主要功能",
    exact: true,
  });
  await expect(primary.getByRole("button")).toHaveText([
    "工作台",
    "采购⌄",
    "销售⌄",
    "库存⌄",
    "质量⌄",
    "售后⌄",
    "资料⌄",
    "财务⌄",
    "设置⌄",
  ]);
  const home = primary.getByRole("button", { name: "企业工作台", exact: true });
  await home.focus();
  await home.press("ArrowRight");
  await expect(
    primary.getByRole("button", { name: "采购", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(
    page.getByRole("navigation", { name: "采购功能", exact: true }),
  ).toBeVisible();
  await expect(
    page
      .getByRole("navigation", { name: "采购功能", exact: true })
      .getByRole("button", { name: "采购订单", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("navigation", { name: "采购功能", exact: true }),
  ).not.toBeVisible();
  for (const name of [
    "库存盘点",
    "零售收银",
    "往来单位",
    "质量处置",
    "往来结算",
    "退货与退款",
    "商品字段设置",
  ])
    await nav(page, name);
  await primary.getByRole("button", { name: "库存", exact: true }).click();
  await expect(
    page.getByRole("navigation", { name: "库存功能", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("navigation", { name: "库存功能", exact: true })
    .getByRole("button", { name: "库存查询", exact: true })
    .click();
  const box = await page.locator("main").boundingBox();
  expect(box!.width).toBeGreaterThan(1300);
  await expect(page.locator(".breadcrumb")).toHaveCount(0);
  expect(
    (await page.locator(".workspace-chrome").boundingBox())!.height,
  ).toBeLessThanOrEqual(50);
  await expect(
    primary.getByRole("button", { name: "运营", exact: true }),
  ).toHaveCount(0);
  await expect(
    primary.getByRole("button", { name: "医保", exact: true }),
  ).toHaveCount(0);
  await expect(page.locator(".el-loading-mask:visible")).toHaveCount(0);
  await page.screenshot({
    path: "../../artifacts/horizontal-inventory-desktop.png",
    animations: "disabled",
  });
  await nav(page, "企业工作台");
  await expect(
    page
      .locator(".account .el-select__selected-item")
      .filter({ hasText: current.tenant_name }),
  ).toBeVisible();
  await expect(page.locator(".el-loading-mask:visible")).toHaveCount(0);
  await page.screenshot({
    path: "../../artifacts/horizontal-workspace-desktop.png",
    animations: "disabled",
  });
  await page
    .getByRole("region", { name: "常用入口" })
    .getByRole("button", { name: "采购入库", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "采购入库", exact: true }),
  ).toBeVisible();
  expect(faults).toEqual([]);
});

test("narrow viewport keeps category and section navigation reachable without horizontal page overflow", async ({
  page,
  context,
}) => {
  await wholesale(context, "demo.owner");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const primary = page.getByRole("navigation", {
    name: "主要功能",
    exact: true,
  });
  await primary
    .getByRole("button", { name: "企业工作台", exact: true })
    .focus();
  await page.keyboard.press("End");
  const settings = primary.getByRole("button", { name: "设置", exact: true });
  await expect(settings).toBeFocused();
  await page.keyboard.press("Enter");
  const secondary = page.getByRole("navigation", {
    name: "设置功能",
    exact: true,
  });
  await secondary
    .getByRole("button", { name: "商品字段设置", exact: true })
    .focus();
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("heading", { name: "成员与权限", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await expect(page.locator(".el-loading-mask:visible")).toHaveCount(0);
  await page.screenshot({
    path: "../../artifacts/horizontal-mobile.png",
    animations: "disabled",
  });
  await page.setViewportSize({ width: 1366, height: 768 });
  await nav(page, "批发销售");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await expect(page.locator(".el-loading-mask:visible")).toHaveCount(0);
  await page.screenshot({
    path: "../../artifacts/horizontal-sales-1366.png",
    animations: "disabled",
  });
});

test("permission filtering and tenant switching reset navigation within the current account", async ({
  page,
  context,
  browser,
}) => {
  const current = await wholesale(context, "demo.owner");
  await page.goto("/");
  await nav(page, "库存台账");
  const other = current.tenants.find(
    (t: { id: string }) => t.id !== current.tenant_id,
  );
  await page
    .getByRole("combobox", { name: "切换企业", exact: true })
    .press("Enter");
  await page.getByRole("option", { name: other.name, exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "企业工作台", exact: true }),
  ).toBeVisible();
  await expect(
    page
      .locator(".account .el-select__selected-item")
      .filter({ hasText: other.name }),
  ).toBeVisible();
  await page
    .getByRole("navigation", { name: "主要功能", exact: true })
    .getByRole("button", { name: "库存", exact: true })
    .click();
  await page
    .getByRole("navigation", { name: "库存功能", exact: true })
    .getByRole("button", { name: "库存查询", exact: true })
    .click();
  const viewer = await browser.newContext();
  try {
    await wholesale(viewer, "demo.viewer");
    const p = await viewer.newPage();
    await p.goto("/");
    const primary = p.getByRole("navigation", {
      name: "主要功能",
      exact: true,
    });
    await expect(
      primary.getByRole("button", { name: "设置", exact: true }),
    ).toHaveCount(0);
    await expect(
      p.getByRole("button", { name: "成员与权限", exact: true }),
    ).toHaveCount(0);
    await nav(p, "采购入库");
    await expect(
      p.getByRole("button", { name: "新建采购订单", exact: true }),
    ).toHaveCount(0);
  } finally {
    await viewer.close();
  }
});
