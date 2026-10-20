import {
  test,
  expect,
  type Browser,
  type BrowserContext,
  type Page,
} from "@playwright/test";
import { randomUUID } from "node:crypto";
import { wholesale, stockedCustomer, read, nav } from "./business-helpers";
import type { EntryProduct } from "../src/documentEntry";

async function setup(page: Page, context: BrowserContext, browser: Browser) {
  const owner = await wholesale(context, "demo.owner"),
    reviewer = await browser.newContext();
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    const f = await stockedCustomer(context, owner, reviewer, checker);
    const lookup = await read(
      context,
      `/api/v1/catalog/entry-products?entity_id=${f.entity}&channel=purchase&sku_id=${f.product.sku_id}`,
    );
    await page.goto("/");
    await nav(page, "采购入库");
    await page.getByRole("button", { name: "新建采购订单", exact: true }).click();
    const editor = page.getByRole("region", {
      name: "新建采购单",
      exact: true,
    });
    return {
      editor,
      product: lookup.items[0] as EntryProduct,
      input: editor.getByRole("combobox", { name: "商品", exact: true }),
    };
  } finally {
    await reviewer.close();
  }
}

test("product candidates show comparison columns, literal highlights and keyboard batches without losing the entry row", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(90000);
  const { editor, product, input } = await setup(page, context, browser);
  const query = "销售 软件测试";
  const candidates = Array.from({ length: 25 }, (_, i) =>
    i === 20
      ? product
      : {
          ...product,
          id: randomUUID(),
          code: `${product.code}-${i + 1}`,
          name: `${product.name} 同系列${i + 1}`,
          specification: `单件 · 规格${i + 1}`,
        },
  );
  let failSecond = true;
  const offsets: number[] = [];
  await page.route("**/api/v1/catalog/entry-products?**", async (route) => {
    const params = new URL(route.request().url()).searchParams;
    if (params.get("search") !== query) return route.continue();
    const offset = Number(params.get("offset") || 0);
    offsets.push(offset);
    if (offset === 20 && failSecond) {
      failSecond = false;
      return route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ error: { message: "模拟读取失败" } }),
      });
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: {
          items: candidates.slice(offset, offset + 20),
          has_more: offset === 0,
        },
      }),
    });
  });
  await input.fill(query);
  const popup = page.locator(".entry-product-popup");
  await expect(popup.getByRole("option")).toHaveCount(20);
  await expect(popup.locator(".picker-columns")).toContainText("商品编码");
  await expect(popup.locator(".picker-columns")).toContainText("规格型号");
  await expect(popup.locator(".picker-columns")).toContainText("生产企业");
  await expect(
    popup.getByRole("option").first().locator("mark").first(),
  ).toHaveText("销售");
  const before = await page.evaluate(() => window.scrollY);
  for (let i = 0; i < 19; i++) await input.press("ArrowDown");
  const active = popup.getByRole("option", { selected: true });
  await expect(active).toHaveAttribute("data-sku", candidates[19]!.id);
  expect(
    await active.evaluate((el) => {
      const r = el.getBoundingClientRect(),
        scroll = el.closest(".picker-results")!;
      const v = scroll.getBoundingClientRect(),
        h = scroll.querySelector(".picker-columns")!.getBoundingClientRect();
      return r.top >= h.bottom - 1 && r.bottom <= v.bottom + 1;
    }),
  ).toBe(true);
  expect(await page.evaluate(() => window.scrollY)).toBe(before);
  await input.press("PageDown");
  await expect(popup.getByRole("alert")).toContainText("查询失败");
  // Enter retries the failed batch, and confirms its first product once.
  await input.press("Enter");
  await expect(editor.getByLabel("采购数量", { exact: true })).toBeFocused();
  await expect(input).toHaveValue(`${product.name} · ${product.code}`);
  expect(offsets).toEqual([0, 20, 20]);
  await editor.getByLabel("采购数量", { exact: true }).fill("3");
  await editor.getByLabel("含税单价（元）", { exact: true }).fill("2.50");
  await input.fill(query);
  await expect(popup.getByRole("option")).toHaveCount(20);
  await input.press("PageDown");
  await expect(popup.getByRole("option")).toHaveCount(5);
  await expect(popup.locator(".picker-range")).toHaveText("第 21–25 条");
  await expect(
    popup.getByRole("button", { name: "下一批", exact: true }),
  ).toBeDisabled();
  await input.press("PageUp");
  await expect(popup.getByRole("option", { selected: true })).toHaveAttribute(
    "data-sku",
    candidates[19]!.id,
  );
  await input.press("ArrowDown");
  await expect(popup.getByRole("option", { selected: true })).toHaveAttribute(
    "data-sku",
    candidates[0]!.id,
  );
  await page.screenshot({
    path: "../../artifacts/entry-search-desktop.png",
    animations: "disabled",
  });
  await input.press("Escape");
  await expect(input).toHaveValue(`${product.name} · ${product.code}`);
  await expect(editor.getByLabel("采购数量", { exact: true })).toHaveValue("3");
  await expect(
    editor.getByLabel("含税单价（元）", { exact: true }),
  ).toHaveValue("2.50");
  await page.setViewportSize({ width: 390, height: 620 });
  await input.fill(query);
  await expect(popup.getByRole("option")).toHaveCount(20);
  const box = (await popup.boundingBox())!;
  await expect
    .poll(() =>
      input.evaluate((el) => {
        const r = el.getBoundingClientRect();
        return el.contains(
          document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2),
        );
      }),
    )
    .toBe(true);
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(390);
  expect(box.y).toBeGreaterThanOrEqual(0);
  expect(box.y + box.height).toBeLessThanOrEqual(620);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "../../artifacts/entry-search-mobile.png",
    animations: "disabled",
  });
  await input.press("Escape");
});

test("rapid query replacement, blur, IME and repeated confirmation cannot apply late candidates", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(90000);
  const { editor, product, input } = await setup(page, context, browser);
  let release: () => void = () => {};
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  let releaseFresh: () => void = () => {};
  const freshHeld = new Promise<void>((resolve) => {
    releaseFresh = resolve;
  });
  let lateFinished = false,
    confirmCalls = 0;
  await page.route("**/api/v1/catalog/entry-products?**", async (route) => {
    const params = new URL(route.request().url()).searchParams;
    const query = params.get("search");
    if (query === "SLOW") {
      await held;
      await route
        .fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            data: { items: [{ ...product, code: "OLD" }], has_more: false },
          }),
        })
        .catch(() => {});
      lateFinished = true;
      return;
    }
    if (query === "FRESH" || query === "中文") {
      confirmCalls++;
      if (confirmCalls === 1) await freshHeld;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: { items: [product], has_more: false } }),
      });
    }
    return route.continue();
  });
  try {
    const requested = page.waitForRequest(
      (r) => new URL(r.url()).searchParams.get("search") === "SLOW",
    );
    await input.fill("SLOW");
    await requested;
    await input.fill("FRESH");
    const freshRequested = page.waitForRequest(
      (r) => new URL(r.url()).searchParams.get("search") === "FRESH",
    );
    await input.press("Enter");
    await freshRequested;
    await input.press("Enter");
    releaseFresh();
    await expect(editor.getByLabel("采购数量", { exact: true })).toBeFocused();
    expect(confirmCalls).toBe(1);
    release();
    await expect.poll(() => lateFinished).toBe(true);
    await expect(input).toHaveValue(`${product.name} · ${product.code}`);
    await expect(page.locator(".entry-product-popup")).toHaveCount(0);
    await input.fill("");
    await expect(input).toHaveValue("");
    await input.press("Tab");
    await expect(input).toBeFocused();
    await input.press("Escape");
    await expect(input).toHaveValue(`${product.name} · ${product.code}`);
    await input.dispatchEvent("compositionstart");
    await input.fill("中文");
    await input.dispatchEvent("keydown", {
      key: "Enter",
      isComposing: true,
      bubbles: true,
    });
    expect(confirmCalls).toBe(1);
    await expect(input).toBeFocused();
    await input.dispatchEvent("compositionend");
    await input.press("Enter");
    await expect(editor.getByLabel("采购数量", { exact: true })).toBeFocused();
    await expect(input).toHaveValue(`${product.name} · ${product.code}`);
    expect(confirmCalls).toBe(2);
    await input.fill("FRESH");
    await editor.getByLabel("草稿备注", { exact: true }).focus();
    await expect(page.locator(".entry-product-popup")).toHaveCount(0);
    await expect(input).toHaveValue("FRESH");
  } finally {
    release();
    releaseFresh();
  }
});
