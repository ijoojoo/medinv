import { test, expect } from "@playwright/test";
import {
  wholesale,
  nav,
  read,
  command,
  stockedCustomer,
} from "./business-helpers";
import {
  parseDate,
  businessToday,
  moveDay,
  moveMonth,
  weekday,
} from "../src/dates";

test("strict calendar parsing, Gregorian boundaries, restrictions and Shanghai business day", () => {
  for (const [input, value] of [
    ["20261019", "2026-10-19"],
    [" 20260109 ", "2026-01-09"],
    ["2024-02-29", "2024-02-29"],
    ["20000229", "2000-02-29"],
    ["00010101", "0001-01-01"],
    ["99991231", "9999-12-31"],
  ])
    expect(parseDate(input)).toEqual({ value, error: "" });
  for (const input of [
    "20260230",
    "2026-02-29",
    "1900-02-29",
    "20260431",
    "20261301",
    "2026101",
    "2026-1-09",
    "26-10-19",
    "2026/10/19",
    "00000101",
    "202610190",
    "2026-10-19T00:00:00Z",
  ])
    expect(parseDate(input).error, input).not.toBe("");
  expect(parseDate("")).toEqual({ value: "", error: "" });
  expect(parseDate("", { required: true }).error).not.toBe("");
  expect(parseDate("20261019", { min: "20261020" }).error).not.toBe("");
  expect(parseDate("20261019", { max: "2026-10-18" }).error).not.toBe("");
  expect(
    parseDate("20261019", { disabledDate: (d) => d === "2026-10-19" }).error,
  ).not.toBe("");
  expect(businessToday(new Date("2026-10-18T16:00:00Z"))).toBe("2026-10-19");
  expect(moveDay("2024-02-28", 1)).toBe("2024-02-29");
  expect(moveMonth("2024-03-31", -1)).toBe("2024-02-29");
  expect(moveDay("0001-01-01", -1)).toBe("0001-01-01");
  expect(weekday("2026-10-19")).toBe(0);
});

test("horizontal disclosure navigation, geometry, timers, mixed keyboard and touch", async ({
  page,
  context,
  browser,
}) => {
  await wholesale(context, "demo.owner");
  await page.goto("/");
  const primary = page.getByRole("navigation", {
    name: "主要功能",
    exact: true,
  });
  const purchase = primary.getByRole("button", { name: "采购", exact: true });
  const sales = primary.getByRole("button", { name: "销售", exact: true });
  await expect(page.locator(".module-popup")).toHaveCount(0);
  const before = await page.locator(".main-shell").boundingBox();
  await purchase.hover();
  const popup = page.getByRole("navigation", { name: "采购功能", exact: true });
  await expect(popup).toBeVisible();
  expect(await page.locator(".main-shell").boundingBox()).toEqual(before);
  await purchase.click();
  await expect(popup).toBeVisible();
  await purchase.click();
  await expect(popup).toHaveCount(0);
  await sales.hover();
  await purchase.hover();
  await popup.hover();
  await page.waitForTimeout(280);
  await expect(popup).toBeVisible();
  await popup.getByRole("button", { name: "采购入库", exact: true }).focus();
  await page.mouse.move(5, 650);
  await page.waitForTimeout(280);
  await expect(popup).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(purchase).toBeFocused();
  await expect(popup).toHaveCount(0);
  await purchase.press("Enter");
  await purchase.press("Tab");
  await expect(
    popup.getByRole("button", { name: "采购入库", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("heading", { name: "采购单", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "查询单据", exact: true }),
  ).toBeEnabled();
  await page.screenshot({
    path: "../../outputs/ui-dates/采购工作区.png",
    fullPage: true,
  });
  for (const width of [1280, 850, 480]) {
    await page.setViewportSize({ width, height: 850 });
    await purchase.click();
    const box = await popup.boundingBox();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(width);
    await page.keyboard.press("Escape");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  }
  const touch = await browser.newContext({
    hasTouch: true,
    viewport: { width: 850, height: 900 },
    storageState: await context.storageState(),
  });
  const tp = await touch.newPage();
  await tp.goto("/");
  await tp
    .getByRole("navigation", { name: "主要功能" })
    .getByRole("button", { name: "采购", exact: true })
    .tap();
  await expect(tp.getByRole("navigation", { name: "采购功能" })).toBeVisible();
  await tp.locator(".work-tabs").tap();
  await expect(tp.locator(".module-popup")).toHaveCount(0);
  await touch.close();
});

for (const profile of [
  { locale: "zh-CN", timezoneId: "Asia/Shanghai" },
  { locale: "en-US", timezoneId: "America/Los_Angeles" },
])
  test(`query input, current request value, calendar and tab retention ${profile.locale}`, async ({
    browser,
  }) => {
    const context = await browser.newContext({
      ...profile,
      viewport: { width: 1440, height: 1000 },
    });
    const page = await context.newPage();
    await wholesale(context, "demo.owner");
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto("/");
    await nav(page, "采购入库");
    const form = page.getByRole("form", { name: "单据查询" }),
      from = form.getByRole("textbox", { name: "创建日期从", exact: true }),
      to = form.getByRole("textbox", { name: "创建日期至", exact: true });
    await expect(form.getByRole("button", { name: "查询单据" })).toBeEnabled();
    const requests: string[] = [];
    page.on("request", (r) => {
      if (
        new URL(r.url()).pathname === "/api/v1/purchase-orders" &&
        r.method() === "GET"
      )
        requests.push(r.url());
    });
    await from.fill("20261019");
    await expect(from).toHaveValue("20261019");
    const response = page.waitForResponse(
      (r) => new URL(r.url()).pathname === "/api/v1/purchase-orders",
    );
    await from.press("Enter");
    await response;
    await expect(from).toHaveValue("2026-10-19");
    expect(requests).toHaveLength(1);
    expect(new URL(requests[0]!).searchParams.get("date_from")).toBe(
      "2026-10-19",
    );
    for (const bad of [
      "20260230",
      "2026-02-29",
      "20260431",
      "20261301",
      "2026101",
      "2026-1-09",
      "26-10-19",
      "2026/10/19",
    ]) {
      await from.fill(bad);
      await form.evaluate((f) => (f as HTMLFormElement).requestSubmit());
      await expect(from).toHaveAttribute("aria-invalid", "true");
      await expect(from).toHaveValue(bad);
      expect(requests).toHaveLength(1);
    }
    await from.fill("20261019");
    await to.fill("20261018");
    await to.press("Enter");
    await expect(to).toHaveAttribute("aria-invalid", "true");
    expect(requests).toHaveLength(1);
    await to.fill("");
    await from.dispatchEvent("compositionstart");
    await from.press("Enter");
    expect(requests).toHaveLength(1);
    await from.dispatchEvent("compositionend");
    await from.fill("bad-date");
    await from.press("Tab");
    await nav(page, "批发销售");
    await page
      .getByRole("navigation", { name: "工作标签" })
      .getByRole("button", { name: "采购入库", exact: true })
      .click();
    await expect(from).toHaveValue("bad-date");
    await expect(from).toHaveAttribute("aria-invalid", "true");
    await from.fill("20261019");
    await form
      .getByRole("button", { name: "创建日期从，打开日历", exact: true })
      .click();
    const calendar = page.getByRole("dialog", {
      name: "创建日期从日历",
      exact: true,
    });
    await expect(calendar).toContainText("2026年10月");
    await expect(calendar.getByRole("columnheader").first()).toHaveText("一");
    await calendar.getByRole("button", { name: "下一月", exact: true }).click();
    await expect(calendar).toContainText("2026年11月");
    await expect(from).toHaveValue("2026-10-19");
    await page.mouse.move(5, 700);
    await page.waitForTimeout(280);
    await expect(calendar).toBeVisible();
    await calendar.getByRole("button", { name: "今天", exact: true }).focus();
    await page.keyboard.press("PageUp");
    await expect(
      calendar.getByRole("button", { name: "2026-10-19", exact: true }),
    ).toBeFocused();
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("Enter");
    await expect(from).toHaveValue("2026-10-20");
    await expect(calendar).not.toBeVisible();
    expect(requests).toHaveLength(1);
    await form.evaluate((f) => (f as HTMLFormElement).requestSubmit());
    await expect.poll(() => requests.length).toBe(2);
    expect(new URL(requests[1]!).searchParams.get("date_from")).toBe(
      "2026-10-20",
    );
    await page.screenshot({
      path: `../../outputs/ui-dates/日期查询-${profile.locale}.png`,
      fullPage: true,
    });
    await page.setViewportSize({ width: 480, height: 680 });
    await form
      .getByRole("button", { name: "创建日期至，打开日历", exact: true })
      .click();
    const edgeCalendar = page.getByRole("dialog", {
      name: "创建日期至日历",
      exact: true,
    });
    await expect(edgeCalendar).toBeVisible();
    const box = await edgeCalendar.boundingBox();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(480);
    expect(box!.y).toBeGreaterThanOrEqual(0);
    expect(box!.y + box!.height).toBeLessThanOrEqual(680);
    await edgeCalendar
      .getByRole("button", { name: "关闭", exact: true })
      .click();
    expect(errors).toEqual([]);
    await context.close();
  });

test("receipt dates use current raw values, block invalid save, keep distinct record tabs and save real API values", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(120000);
  const owner = await wholesale(context, "demo.owner"),
    entity = owner.entities[0].id;
  const reviewer = await browser.newContext(),
    checker = await wholesale(reviewer, "demo.quality");
  const fixture = await stockedCustomer(context, owner, reviewer, checker);
  const make = () =>
    command(context, owner, "/api/v1/purchase-orders", {
      entity_id: entity,
      warehouse_id: fixture.warehouse,
      supplier_id: fixture.party.id,
      lines: [
        {
          sku_id: fixture.product.sku_id,
          qty: "2",
          unit_price: "2",
          tax_rate: "0.13",
        },
      ],
    });
  const first = await make(),
    second = await make();
  await command(
    reviewer,
    checker,
    `/api/v1/purchase-orders/${first.id}/approve`,
    { expected_version: "1", reason: "日期控件软件验收" },
  );
  const order = await read(context, `/api/v1/purchase-orders/${first.id}`);
  await page.goto(
    `/workspace/purchasing?tenant=${owner.tenant_id}&entity=${entity}&order=${first.id}`,
  );
  await expect(
    page.getByRole("button", { name: "← 返回采购列表", exact: true }),
  ).toBeVisible();
  const tabs = page.getByRole("navigation", { name: "工作标签" });
  await expect(tabs.locator(".record-tab")).toHaveCount(1);
  await page
    .getByRole("button", { name: "← 返回采购列表", exact: true })
    .click();
  await page
    .getByRole("form", { name: "单据查询" })
    .getByLabel("查找单据")
    .fill(second.number);
  await page.getByRole("button", { name: "查询单据" }).click();
  await page
    .getByRole("button", { name: "查看采购单", exact: true })
    .first()
    .click();
  await expect(tabs.locator(".record-tab")).toHaveCount(2);
  await tabs
    .getByRole("button", { name: order.order.number, exact: true })
    .click();
  await page.getByRole("button", { name: "登记到货", exact: true }).click();
  const dialog = page.getByRole("dialog").filter({ visible: true });
  await dialog.getByLabel("随货单号").fill("DATE-CONTROL-TEST");
  await dialog.getByLabel("运输及随货核查").fill("软件日期输入验收");
  await dialog.getByLabel("生产批号").fill("DATE-" + Date.now());
  await dialog.getByLabel("到货数量").fill("2");
  const mfg = dialog.getByRole("textbox", { name: "生产日期", exact: true }),
    expiry = dialog.getByRole("textbox", { name: "有效期至", exact: true });
  await mfg.fill("20260109");
  await expiry.fill("20281231");
  let posts = 0;
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().includes("/receipts")) posts++;
  });
  await mfg.fill("99991231");
  await mfg.press("Enter");
  await expect(mfg).toHaveAttribute("aria-invalid", "true");
  expect(posts).toBe(0);
  await dialog
    .getByRole("button", { name: "生产日期，打开日历", exact: true })
    .click();
  const manufactureCalendar = page.getByRole("dialog", {
    name: "生产日期日历",
    exact: true,
  });
  await expect(
    manufactureCalendar.getByRole("button", {
      name: "9999-12-31",
      exact: true,
    }),
  ).toHaveAttribute("aria-disabled", "true");
  await manufactureCalendar
    .getByRole("button", { name: "今天", exact: true })
    .click();
  await expect(mfg).toHaveValue(businessToday());
  await mfg.fill("20260109");
  await expiry.press("Enter");
  expect(posts).toBe(0);
  await dialog
    .getByRole("button", { name: "有效期至，打开日历", exact: true })
    .click();
  const calendar = page.getByRole("dialog", {
    name: "有效期至日历",
    exact: true,
  });
  await expect(
    calendar.getByRole("button", { name: "2028-12-31", exact: true }),
  ).toBeFocused();
  await calendar.getByRole("button", { name: "上一月", exact: true }).click();
  await page.keyboard.press("PageDown");
  await expect(
    calendar.getByRole("button", { name: "2028-12-30", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(expiry).toBeFocused();
  expect(posts).toBe(0);
  await expiry.fill("20260230");
  await dialog
    .locator("form")
    .evaluate((f) => (f as HTMLFormElement).requestSubmit());
  await expect(expiry).toHaveAttribute("aria-invalid", "true");
  expect(posts).toBe(0);
  await expect(dialog).toBeVisible();
  await page.screenshot({
    path: "../../outputs/ui-dates/到货日期校验.png",
    fullPage: true,
  });
  await expiry.fill("20281231");
  const request = page.waitForRequest(
    (r) => r.method() === "POST" && r.url().includes("/receipts"),
  );
  await dialog
    .locator("form")
    .evaluate((f) => (f as HTMLFormElement).requestSubmit());
  const body = (await request).postDataJSON();
  expect(body.lines[0].mfg_date).toBe("2026-01-09");
  expect(body.lines[0].expiry_date).toBe("2028-12-31");
  await expect(dialog).not.toBeVisible();
  expect(posts).toBe(1);
  await reviewer.close();
});
