import { chooseWarehouse } from "./business-helpers";
import { test, expect } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import {
  wholesale,
  stockedCustomer,
  read,
  nav,
  chooseEntrySku,
} from "./business-helpers";

for (const purchase of [true, false]) {
  test(`${purchase ? "purchase" : "sale"} header Enter advances safely and maintenance returns to saved draft`, async ({
    page,
    context,
    browser,
  }) => {
    test.setTimeout(120000);
    const reviewer = await browser.newContext();
    const owner = await wholesale(context, "demo.owner");
    try {
      const quality = await wholesale(reviewer, "demo.quality");
      const f = await stockedCustomer(context, owner, reviewer, quality);
      const errors: string[] = [],
        submissions: string[] = [];
      page.on("pageerror", (e) => errors.push(e.message));
      page.on("request", (r) => {
        if (
          r.method() === "POST" &&
          /\/api\/v1\/(purchase|sales)-orders$/.test(new URL(r.url()).pathname)
        )
          submissions.push(r.url());
      });
      await page.goto("/");
      await nav(page, purchase ? "采购入库" : "销售出库");
      await page
        .getByRole("button", {
          name: purchase ? "新建采购订单" : "新建销售单",
          exact: true,
        })
        .click();
      const editor = page.locator(".document-editor:visible"),
        party = editor.getByRole("combobox", {
          name: purchase ? "供应商" : "客户",
          exact: true,
        }),
        warehouse = editor.getByRole("combobox", {
          name: purchase ? "收货仓库" : "发货仓库",
          exact: true,
        }),
        title = editor.getByLabel("草稿备注", { exact: true });
      await expect(party).toBeEnabled();
      const id = new URL(page.url()).pathname.split("/").pop()!;
      await party.fill(f.customer);
      await party.press("Enter");
      await expect(party).toHaveValue(f.customer);
      await expect(page.locator(".party-lookup-popover:visible")).toHaveCount(
        0,
      );
      await party.press("Enter");
      await expect(warehouse).toBeFocused();
      await chooseWarehouse(warehouse, f.warehouse);
      await warehouse.press("Enter");
      await expect(title).toBeFocused();
      const note = `表头验收-${f.suffix}`;
      await title.fill(note);
      await title.press("Shift+Enter");
      await expect(warehouse).toBeFocused();
      await warehouse.press("Escape");
      await warehouse.press("Enter");
      await title.dispatchEvent("keydown", {
        key: "Enter",
        code: "Enter",
        isComposing: true,
      });
      await expect(title).toBeFocused();
      await title.press("Enter");
      if (!purchase) {
        const address = editor.getByLabel("收货地址", { exact: true }),
          shelf = editor.getByLabel("最低剩余效期（天）", { exact: true }),
          credit = editor.getByLabel("账期（天）", { exact: true });
        await expect(address).toBeFocused();
        await address.fill("软件测试地址");
        await address.press("Enter");
        await expect(shelf).toBeFocused();
        await shelf.fill("-1");
        await shelf.press("Enter");
        await expect(shelf).toBeFocused();
        await shelf.fill("45");
        await address.focus();
        await address.press("Tab");
        await expect(shelf).toBeFocused();
        await page.keyboard.type("7");
        await expect(shelf).toHaveValue("7");
        await shelf.press("Enter");
        await expect(credit).toBeFocused();
        await page.keyboard.type("8");
        await expect(credit).toHaveValue("8");
        await credit.press("Enter");
      }
      const row = editor.locator(".entry-row").first(),
        sku = row.getByRole("combobox", { name: "商品", exact: true }),
        qty = row.getByLabel(purchase ? "采购数量" : "销售数量", {
          exact: true,
        });
      await expect(sku).toBeFocused();
      await chooseEntrySku(row, f.product.sku_id);
      await qty.fill("2");
      await editor
        .getByRole("button", { name: "维护往来单位", exact: true })
        .click();
      await expect(page).toHaveURL(/\/workspace\/parties\?/);
      const saved = await read(context, `/api/v1/document-drafts/${id}`);
      expect(saved.title).toBe(note);
      expect(saved.content.lines[0].qty).toBe("2");
      expect(saved.content[purchase ? "supplier_id" : "customer_id"]).toBe(
        f.party.id,
      );
      const returnTab = page
        .locator(".editor-tab")
        .getByRole("button", { name: new RegExp(`单 · ${id.slice(0, 4)}`) });
      await returnTab.click();
      await expect(title).toHaveValue(note);
      await expect(qty).toHaveValue("2");
      await expect(party).toHaveValue(f.customer);
      if (purchase) {
        const endpoint = `**/api/v1/document-drafts/${id}`;
        await page.route(endpoint, async (route) => {
          if (route.request().method() !== "POST") return route.continue();
          await route.fulfill({
            status: 503,
            contentType: "application/json",
            body: JSON.stringify({
              error: { code: "TEST_FAILURE", message: "测试草稿暂不可保存" },
            }),
          });
        });
        await title.fill(note + "-保留");
        await editor
          .getByRole("button", { name: "维护商品资料", exact: true })
          .click();
        await expect(editor).toBeVisible();
        await expect(editor.getByRole("alert")).toContainText(
          "测试草稿暂不可保存",
        );
        await expect(title).toHaveValue(note + "-保留");
        await page.unroute(endpoint);
      }
      await editor
        .getByRole("button", { name: "维护商品资料", exact: true })
        .click();
      await expect(page).toHaveURL(/\/workspace\/products\?/);
      await returnTab.click();
      await expect(qty).toHaveValue("2");
      await page.reload();
      await expect(qty).toHaveValue("2");
      await expect(title).toHaveValue(note + (purchase ? "-保留" : ""));
      expect(submissions).toEqual([]);
      expect(errors).toEqual([]);
      await mkdir(resolve("../../outputs/entry-header"), { recursive: true });
      await expect(page.locator(".el-message")).toHaveCount(0, {
        timeout: 8000,
      });
      await page.screenshot({
        path: resolve(
          `../../outputs/entry-header/${purchase ? "采购" : "销售"}-表头与维护入口.png`,
        ),
        fullPage: true,
      });
      await page.setViewportSize({ width: 390, height: 844 });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      await page.screenshot({
        path: resolve(
          `../../outputs/entry-header/${purchase ? "采购" : "销售"}-窄屏.png`,
        ),
        fullPage: true,
      });
    } finally {
      await reviewer.close();
    }
  });
}
