import { test, expect } from "@playwright/test";
import { wholesale, nav, read } from "./business-helpers";

test("hidden invalid qualification date prevents close-save; cancel, real save, discard preserve their meanings", async ({
  page,
  context,
}) => {
  const owner = await wholesale(context, "demo.owner");
  await page.goto("/");
  await nav(page, "往来单位");
  await page.getByRole("button", { name: "新建往来单位", exact: true }).click();
  const entry = page.getByRole("region", { name: "新建往来单位", exact: true });
  const name = "日期保存验收-" + Date.now();
  await entry.getByLabel("编码", { exact: true }).fill("DATE-" + Date.now());
  await entry.getByLabel("名称", { exact: true }).fill(name);
  await entry.getByLabel("统一社会信用代码").fill("TEST-" + Date.now());
  await entry.getByRole("tab", { name: "质量管控" }).click();
  await entry.getByLabel("经营许可 / 备案编号").fill("TEST-ONLY");
  const from = entry.getByRole("textbox", {
      name: "资质生效日期",
      exact: true,
    }),
    to = entry.getByRole("textbox", { name: "资质截止日期", exact: true });
  await from.fill("20260101");
  await to.fill("20260230");
  await entry.getByRole("tab", { name: "基本信息" }).click();
  const tabs = page.getByRole("navigation", { name: "工作标签" }),
    close = tabs.getByRole("button", { name: "关闭往来单位", exact: true });
  await close.click();
  const dialog = page.getByRole("dialog", { name: "关闭前保存修改" });
  await dialog.getByRole("button", { name: "取消关闭" }).click();
  await expect(entry).toBeVisible();
  let writes = 0;
  page.on("request", (r) => {
    if (
      new URL(r.url()).pathname === "/api/v1/catalog/parties" &&
      r.method() === "POST"
    )
      writes++;
  });
  await close.click();
  await dialog.getByRole("button", { name: "保存后关闭" }).click();
  await expect(to).toBeVisible();
  await expect(to).toHaveValue("20260230");
  await expect(to).toHaveAttribute("aria-invalid", "true");
  expect(writes).toBe(0);
  await expect(close).toBeVisible();
  await to.fill("20281231");
  await close.click();
  await dialog.getByRole("button", { name: "保存后关闭" }).click();
  await expect(close).toHaveCount(0);
  expect(writes).toBe(1);
  const rows = await read(
    context,
    `/api/v1/catalog/parties?entity_id=${owner.entities[0].id}&search=${encodeURIComponent(name)}`,
  );
  expect(rows.items).toHaveLength(1);
  expect(rows.items[0].valid_from).toBe("2026-01-01");
  expect(rows.items[0].valid_until).toBe("2028-12-31");
  await nav(page, "往来单位");
  await page.getByRole("button", { name: "新建往来单位", exact: true }).click();
  await entry.getByLabel("名称", { exact: true }).fill("未保存的当前会话");
  await close.click();
  await dialog.getByRole("button", { name: "放弃修改" }).click();
  await expect(close).toHaveCount(0);
  expect(writes).toBe(1);
});

test("query failure keeps last successful rows and distinguishes failure from empty results; current tabs scroll into view", async ({
  page,
  context,
}) => {
  await wholesale(context, "demo.owner");
  await page.goto("/");
  await nav(page, "采购入库");
  await expect(page.getByRole("button", { name: "查询单据" })).toBeEnabled();
  const rows = page.locator(".el-table__body-wrapper tr:visible");
  const old = await rows.allTextContents();
  await page.route("**/api/v1/purchasing/workflow?**", (r) =>
    r.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({
        error: {
          code: "TEST_UNAVAILABLE",
          message: "网络异常验收",
          retryable: false,
        },
      }),
    }),
  );
  await page.getByRole("button", { name: "查询单据" }).click();
  await expect(
    page.getByText("查询失败，已保留上次结果；请刷新后再办理。"),
  ).toBeVisible();
  expect(await rows.allTextContents()).toEqual(old);
  await page.unroute("**/api/v1/purchasing/workflow?**");
  for (const name of [
    "批发销售",
    "期初库存",
    "库存查询",
    "仓库调拨",
    "库存盘点",
    "商品字典",
    "商品档案",
    "往来单位",
  ])
    await nav(page, name);
  await page.setViewportSize({ width: 850, height: 900 });
  await page
    .getByRole("navigation", { name: "工作标签" })
    .getByRole("button", { name: "采购入库", exact: true })
    .click();
  const geometry = await page.locator(".work-tabs").evaluate((bar) => {
    const tab = bar
        .querySelector('[aria-current="page"]')!
        .getBoundingClientRect(),
      bounds = bar.getBoundingClientRect();
    return { left: tab.left, right: tab.right, width: bounds.width };
  });
  expect(geometry.left).toBeGreaterThanOrEqual(0);
  expect(geometry.right).toBeLessThanOrEqual(geometry.width);
});
