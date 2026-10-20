import { test, expect, type Page } from "@playwright/test";
import { wholesale, nav } from "./business-helpers";
import { datePresets, presetRange } from "../src/datePresets";

const form = (page: Page) => page.getByRole("form", { name: "单据查询" });
const select = (page: Page) =>
  form(page).getByRole("combobox", { name: "快捷日期范围", exact: true });
const response = (page: Page, path: string) =>
  page.waitForResponse(
    (r) => new URL(r.url()).pathname === path && r.request().method() === "GET",
  );

test("compact preset dropdown preserves ranges, keyboard access and manual date validation", async ({
  page,
  context,
}) => {
  await wholesale(context, "demo.owner");
  await page.goto("/");
  await nav(page, "采购入库");
  const filters = form(page);
  await expect(
    filters.getByRole("button", { name: "查询单据", exact: true }),
  ).toBeEnabled();
  await expect(select(page)).toHaveValue("all");
  await expect(select(page).locator("option")).toHaveCount(15);
  await expect(filters.locator(".date-shortcuts")).toHaveCount(0);
  let reads = 0;
  page.on("request", (r) => {
    if (new URL(r.url()).pathname === "/api/v1/purchase-orders") reads++;
  });
  for (const [key] of datePresets) {
    await select(page).selectOption(key);
    const range = presetRange(key);
    await expect(
      filters.getByRole("textbox", { name: "创建日期从", exact: true }),
    ).toHaveValue(range.date_from);
    await expect(
      filters.getByRole("textbox", { name: "创建日期至", exact: true }),
    ).toHaveValue(range.date_to);
  }
  expect(reads).toBe(0);
  // Native select works with keyboard; it does not query while choosing a range.
  await select(page).focus();
  await select(page).press("Home");
  await select(page).press("ArrowDown");
  await select(page).press("Tab");
  await expect(select(page)).toHaveValue("all");
  const start = filters.getByRole("textbox", {
    name: "创建日期从",
    exact: true,
  });
  await start.fill("20260230");
  await start.press("Tab");
  const previousReads = reads;
  await filters.getByRole("button", { name: "查询单据", exact: true }).click();
  expect(reads).toBe(previousReads);
  // Choosing a preset replaces invalid uncommitted text as well as the bound model.
  await select(page).selectOption("today");
  const requested = response(page, "/api/v1/purchase-orders");
  await filters.getByRole("button", { name: "查询单据", exact: true }).click();
  expect((await requested).ok()).toBe(true);
  await start.fill("20200101");
  await start.press("Tab");
  await expect(select(page)).toHaveValue("custom");
  for (const width of [1440, 850, 480]) {
    await page.setViewportSize({ width, height: 950 });
    for (const control of [
      select(page),
      start,
      filters.getByRole("button", { name: "查询单据", exact: true }),
    ]) {
      const box = (await control.boundingBox())!;
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(width);
    }
    if (width === 1440) {
      expect((await filters.boundingBox())!.height).toBeLessThan(110);
      await page.screenshot({
        path: "../../outputs/query-filters/采购查询-紧凑日期.png",
        fullPage: true,
      });
    }
  }
});

test("six stock and quality lists submit real combined filters and clear collapsed conditions", async ({
  page,
  context,
}) => {
  test.setTimeout(120000);
  await wholesale(context, "demo.owner");
  await page.goto("/");
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  for (const [label, path, state] of [
    ["期初库存", "inventory-openings", "DRAFT"],
    ["仓库调拨", "transfers", "DRAFT"],
    ["同仓移库", "location-moves", "DRAFT"],
    ["库存盘点", "stock-counts", "COUNTING"],
    ["质量处置", "quality-dispositions", "DRAFT"],
    ["库存台账", "inventory/ledger", "RECEIPT_QUALIFIED"],
  ]) {
    await nav(page, label);
    const filters = form(page);
    const query = filters.getByRole("button", {
      name: "查询单据",
      exact: true,
    });
    await expect(query).toBeEnabled();
    await expect(
      filters.getByRole("combobox", {
        name: label === "库存台账" ? "库存动作" : "单据状态",
        exact: true,
      }),
    ).toBeHidden();
    await filters
      .getByRole("button", { name: "更多条件", exact: true })
      .click();
    await filters
      .getByRole("combobox", {
        name: label === "库存台账" ? "库存动作" : "单据状态",
        exact: true,
      })
      .selectOption(state);
    if (label === "质量处置")
      await filters
        .getByRole("combobox", { name: "处置方式", exact: true })
        .selectOption("WRITE_OFF");
    await filters.getByRole("button", { name: /^收起条件/ }).click();
    await expect(
      filters.getByRole("button", { name: /^更多条件/ }),
    ).toContainText(label === "质量处置" ? "2" : "1");
    await select(page).selectOption("lastMonth");
    const keyword = filters.getByRole("searchbox", {
      name: "查找单据",
      exact: true,
    });
    await keyword.fill("NO-MATCH-" + crypto.randomUUID());
    const requested = response(page, "/api/v1/" + path);
    await keyword.press("Enter");
    const result = await requested;
    expect(result.ok()).toBe(true);
    const params = new URL(result.url()).searchParams;
    expect(params.get("state")).toBe(state);
    expect(params.get("date_from")).toBe(presetRange("lastMonth").date_from);
    expect(params.get("date_to")).toBe(presetRange("lastMonth").date_to);
    expect(params.get("offset")).toBe("0");
    if (label === "质量处置") expect(params.get("kind")).toBe("WRITE_OFF");
    expect((await result.json()).data.items).toEqual([]);
    if (label === "质量处置")
      await page.screenshot({
        path: "../../outputs/query-filters/质量处置-组合查询.png",
        fullPage: true,
      });
    await expect(query).toBeEnabled();
    const cleared = response(page, "/api/v1/" + path);
    await filters
      .getByRole("button", { name: "清除筛选", exact: true })
      .click();
    const cleanParams = new URL((await cleared).url()).searchParams;
    for (const key of ["search", "state", "date_from", "date_to", "kind"])
      expect(cleanParams.has(key)).toBe(false);
    await expect(select(page)).toHaveValue("all");
    await expect(
      filters.getByRole("button", { name: "更多条件", exact: true }),
    ).toBeVisible();
    if (label === "库存台账")
      await expect(
        filters.getByRole("textbox", { name: "入账日期从", exact: true }),
      ).toBeVisible();
  }
  expect(errors).toEqual([]);
});

test("inventory list filters reset pagination and preserve previous data on failed queries", async ({
  page,
  context,
}) => {
  await wholesale(context, "demo.owner");
  const requests: URL[] = [];
  let fail = false;
  await page.route("**/api/v1/stock-counts?**", (route) => {
    requests.push(new URL(route.request().url()));
    if (fail) return route.abort("failed");
    return route.fulfill({
      json: {
        data: {
          items: [
            {
              id: "preview-count",
              number: "查询状态保留示例",
              warehouse_name: "示例仓",
              state: "COUNTING",
              reason: "只读请求夹具",
            },
          ],
          has_more: true,
        },
      },
    });
  });
  await page.goto("/");
  await nav(page, "库存盘点");
  await page.getByRole("button", { name: "下一页", exact: true }).click();
  await expect
    .poll(() => requests.at(-1)?.searchParams.get("offset"))
    .toBe("50");
  const filters = form(page);
  await filters.getByRole("searchbox", { name: "查找单据" }).fill("新的关键词");
  fail = true;
  await filters.getByRole("button", { name: "查询单据", exact: true }).click();
  await expect(
    page.getByText("查询失败，当前保留上次成功结果；请重试查询。", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.locator(".el-table__body-wrapper:visible")).toContainText(
    "查询状态保留示例",
  );
  expect(requests.at(-1)?.searchParams.get("offset")).toBe("0");
  fail = false;
  await filters.getByRole("button", { name: "查询单据", exact: true }).click();
  await expect(
    page.getByText("查询失败，当前保留上次成功结果；请重试查询。", {
      exact: true,
    }),
  ).toHaveCount(0);
  expect(requests.at(-1)?.searchParams.get("search")).toBe("新的关键词");
});
