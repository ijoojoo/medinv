import { test, expect } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { wholesale, nav } from "./business-helpers";

test("cached master pages do not narrow business reports or widen settings", async ({
  page,
  context,
}) => {
  await wholesale(context, "demo.owner");
  await page.setViewportSize({ width: 2560, height: 1000 });
  await page.goto("/");
  await nav(page, "商品档案");
  const workspace = page.locator(".workspace-content");
  await expect(workspace).toHaveAttribute("data-master", "true");
  expect((await workspace.boundingBox())!.width).toBeLessThanOrEqual(1720);
  await nav(page, "采购记录查询");
  await expect(workspace).toHaveAttribute("data-master", "false");
  expect((await workspace.boundingBox())!.width).toBeGreaterThan(2200);
  await nav(page, "界面与操作");
  expect((await workspace.boundingBox())!.width).toBeLessThanOrEqual(1120);
});

test("master add actions remain visible while extension menus contain only supporting actions", async ({
  page,
  context,
}) => {
  await wholesale(context, "demo.owner");
  await page.goto("/");
  for (const [name, add] of [
    ["商品档案", "新建商品 / 项目"],
    ["往来单位", "新建往来单位"],
    ["职员资料", "新增职员"],
    ["仓库与库位", "新增实体仓库"],
    ["数据字典", "＋ 新增字典值"],
  ]) {
    await nav(page, name);
    const layout = page.locator(".data-workspace:visible");
    await expect(
      layout.getByRole("button", { name: add, exact: true }),
    ).toBeEnabled();
    await expect(
      layout.getByRole("button", { name: "展开扩展功能", exact: true }),
    ).toHaveAttribute("aria-expanded", "false");
    await expect(layout.locator(".data-toolbox [data-tool]")).toHaveCount(0);
    await layout
      .getByRole("button", { name: "展开扩展功能", exact: true })
      .click();
    const menu = layout.locator(".data-toolbox");
    await expect(
      menu.locator(
        '[data-tool="new"], [data-tool="revise"], [data-tool="review"], [data-tool="location"], [data-tool="search"]',
      ),
    ).toHaveCount(0);
    await expect(
      layout.getByRole("button", { name: add, exact: true }),
    ).toBeVisible();
    await menu
      .getByRole("button", { name: "收起扩展功能", exact: true })
      .press("Escape");
    await expect(
      layout.getByRole("button", { name: "展开扩展功能", exact: true }),
    ).toBeFocused();
  }
});

test("functional and report pages have a single collapsed menu and retain their main actions", async ({
  page,
  context,
}) => {
  test.setTimeout(120000);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await wholesale(context, "demo.owner");
  await page.goto("/");
  for (const name of [
    "采购入库",
    "销售出库",
    "库存查询",
    "库存台账",
    "质量处置",
    "质量台账",
    "仓库调拨",
    "同仓移库",
    "库存盘点",
    "期初库存",
    "零售收银",
    "退货与退款",
    "销售应收",
    "往来结算",
    "审批流设计",
    "采购记录查询",
    "销售记录查询",
    "质量记录",
  ]) {
    await nav(page, name);
    await expect(page.locator(".data-toolbox:visible")).toHaveCount(1);
    await expect(
      page.getByRole("button", { name: "展开扩展功能", exact: true }),
    ).toHaveAttribute("aria-expanded", "false");
  }
  await nav(page, "采购记录查询");
  await expect(page.locator(".document-reports:visible")).toHaveCount(1);
  await expect(
    page
      .locator(".document-reports:visible")
      .getByRole("button", { name: "查询", exact: true }),
  ).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "导出全部筛选结果" }),
  ).toBeVisible();
  const query = page
    .locator(".document-reports:visible")
    .getByLabel("综合查询");
  await query.fill("保留尚未查询的筛选内容");
  await mkdir("../../outputs/extension-menu", { recursive: true });
  for (const width of [390, 768, 1050, 1440, 2560]) {
    await page.setViewportSize({ width, height: 1000 });
    await page
      .getByRole("button", { name: "展开扩展功能", exact: true })
      .click();
    const menu =
      width < 1280
        ? page.getByRole("dialog", {
            name: "采购记录查询扩展功能",
            exact: true,
          })
        : page.locator(".data-toolbox:visible");
    await expect(
      menu.getByRole("button", { name: "配置字段", exact: true }),
    ).toBeVisible();
    await menu.getByRole("button", { name: "配置字段", exact: true }).click();
    const fields = page.getByRole("dialog", {
      name: "配置查询字段",
      exact: true,
    });
    await expect(fields).toBeVisible();
    await expect
      .poll(() => fields.evaluate((el) => el.contains(document.activeElement)))
      .toBe(true);
    await page.keyboard.press("Escape");
    await expect(fields).not.toBeVisible();
    if (width >= 1280)
      await menu
        .getByRole("button", { name: "收起扩展功能", exact: true })
        .click();
    await expect(query).toHaveValue("保留尚未查询的筛选内容");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
    await page.screenshot({
      path: `../../outputs/extension-menu/采购查询-${width}.png`,
      fullPage: true,
    });
  }
  await page.getByRole("button", { name: "展开扩展功能", exact: true }).click();
  await page
    .locator(".data-toolbox:visible")
    .getByRole("button", { name: "显示与操作设置", exact: true })
    .click();
  await expect(
    page.getByRole("dialog", { name: "显示与操作设置", exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(query).toHaveValue("保留尚未查询的筛选内容");
  expect(errors).toEqual([]);
});
