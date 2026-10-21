import { test, expect, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { wholesale, nav } from "./business-helpers";

async function centered(page: Page, maximum: number) {
  const main = page.locator(".workspace-content");
  await expect
    .poll(async () => {
      const shell = (await page.locator(".main-shell").boundingBox())!;
      const content = (await main.boundingBox())!;
      return Math.max(
        Math.abs(content.width - Math.min(shell.width, maximum)),
        Math.abs(content.x - shell.x - (shell.width - content.width) / 2),
      );
    })
    .toBeLessThanOrEqual(1);
}
async function screenshot(page: Page, name: string) {
  await expect(page.locator(".el-loading-mask:visible")).toHaveCount(0);
  await page.locator(".main-shell").evaluate((el) => el.scrollTo(0, 0));
  await page.screenshot({
    path: `../../outputs/page-layout/${name}.png`,
    animations: "disabled",
  });
}

test("page widths follow the module and remain centered on desktop, ultrawide and sidebar layouts", async ({
  page,
  context,
}) => {
  test.setTimeout(120000);
  await wholesale(context, "demo.owner");
  await mkdir("../../outputs/page-layout", { recursive: true });
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto("/");
  for (const [name, width] of [
    ["数据字典", 1440],
    ["商品档案", 1440],
    ["往来单位", 1440],
    ["职员资料", 1120],
    ["企业设置", 1120],
    ["商品字段设置", 1440],
    ["质量记录", 2560],
    ["采购入库", 2560],
    ["销售出库", 2560],
  ] as const) {
    await nav(page, name);
    await centered(page, width);
    await expect(page.locator("main")).toHaveCount(1);
    if (name === "企业设置") await screenshot(page, "企业设置-1920");
  }
  await nav(page, "数据字典");
  await expect(page.locator(".dictionary-grid")).toBeVisible();
  const tabBottom = (await page.locator(".work-tabs").boundingBox())!;
  const grid = (await page.locator(".dictionary-grid").boundingBox())!;
  expect(grid.y - tabBottom.y - tabBottom.height).toBeLessThanOrEqual(20);
  await expect(page.locator(".dictionary-pagehead")).toHaveCount(0);
  await screenshot(page, "数据字典-1920");

  await page.setViewportSize({ width: 3440, height: 1440 });
  await centered(page, 1440);
  await nav(page, "销售出库");
  await centered(page, 2560);
  await screenshot(page, "销售出库-3440");
  await nav(page, "界面与操作");
  await page.getByRole("button", { name: /侧栏工作区/ }).click();
  await nav(page, "数据字典");
  await centered(page, 1440);
  await screenshot(page, "数据字典-侧栏3440");
  await nav(page, "采购入库");
  await centered(page, 2560);
});

test("narrow pages avoid outer overflow and relocated dictionary actions retain search, shortcut and editing", async ({
  page,
  context,
}) => {
  test.setTimeout(120000);
  await wholesale(context, "demo.owner");
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  for (const width of [1366, 768, 390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    for (const name of [
      "数据字典",
      "商品档案",
      "往来单位",
      "企业设置",
      "职员资料",
      "消息和提醒",
      "采购入库",
      "质量记录",
    ]) {
      await nav(page, name);
      await expect(page.locator(".el-loading-mask:visible")).toHaveCount(0);
      await expect
        .poll(
          () =>
            page.evaluate(
              () => document.documentElement.scrollWidth - innerWidth,
            ),
          { message: `${name}, ${width}px` },
        )
        .toBeLessThanOrEqual(1);
      const shell = (await page.locator(".main-shell").boundingBox())!;
      const content = (await page.locator(".workspace-content").boundingBox())!;
      expect(content.width).toBeLessThanOrEqual(shell.width + 1);
      await expect
        .poll(
          () =>
            page
              .locator(".workspace-content")
              .evaluate((el) => el.scrollWidth - el.clientWidth),
          { message: `${name} content, ${width}px` },
        )
        .toBeLessThanOrEqual(1);
      if (width === 390 && ["商品档案", "往来单位", "企业设置"].includes(name))
        await screenshot(page, name + "-390");
    }
  }
  await nav(page, "数据字典");
  const dictionary = page.getByRole("region", {
    name: "数据字典",
    exact: true,
  });
  await dictionary
    .getByRole("button", { name: /搜索全部字典 \/ 字典值/ })
    .click();
  const search = page.getByRole("dialog", {
    name: "搜索全部字典 / 字典值",
    exact: true,
  });
  await expect(search).toBeVisible();
  await search
    .getByRole("textbox", { name: "搜索全部字典", exact: true })
    .fill("基本单位");
  await search.getByRole("button", { name: "搜索", exact: true }).click();
  await search
    .getByRole("button", { name: /基本单位/ })
    .first()
    .click();
  await expect(search).not.toBeVisible();
  await page.keyboard.press("Control+k");
  await expect(search).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(search).not.toBeVisible();
  await dictionary
    .getByRole("button", { name: "＋ 新增字典值", exact: true })
    .click();
  const drawer = page.locator(".dictionary-drawer:visible");
  await expect(
    drawer.getByRole("button", { name: "应用到配置", exact: true }),
  ).toBeVisible();
  const drawerBox = (await drawer.boundingBox())!;
  expect(drawerBox.width).toBeLessThanOrEqual(320);
  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 390, height: 844 });
  await mkdir("../../outputs/page-layout", { recursive: true });
  await screenshot(page, "数据字典-390");
  expect(errors).toEqual([]);
});
