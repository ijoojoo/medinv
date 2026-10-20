import { test, expect, type Locator } from "@playwright/test";
import {
  wholesale,
  nav,
  read,
  command,
  stockedCustomer,
} from "./business-helpers";
import { defaultEntryColumns } from "../src/documentEntry";

async function visibleControl(control: Locator) {
  await expect(control).toBeFocused();
  await expect
    .poll(() =>
      control.evaluate((el) => {
        const box = el.getBoundingClientRect();
        const grid = el.closest(".document-entry")!;
        const scroll = grid.querySelector(".entry-scroll")!;
        const viewport = scroll.getBoundingClientRect();
        const heading = grid.querySelector("thead")!.getBoundingClientRect();
        const summary = grid
          .querySelector(".entry-summary")!
          .getBoundingClientRect();
        const footer = el
          .closest(".document-editor")!
          .querySelector(".editor-actions")!
          .getBoundingClientRect();
        const chrome = document
          .querySelector(".workspace-chrome")!
          .getBoundingClientRect();
        const top = Math.max(viewport.top + heading.height, chrome.bottom, 0);
        const bottom = Math.min(
          viewport.top + scroll.clientHeight,
          summary.top,
          footer.top,
          innerHeight,
        );
        return {
          vertical: box.top >= top + 2 && box.bottom <= bottom - 2,
          horizontal:
            box.left >= viewport.left &&
            box.right <= viewport.left + scroll.clientWidth,
          uncovered: el.contains(
            document.elementFromPoint(box.x + box.width / 2, box.bottom - 1),
          ),
        };
      }),
    )
    .toEqual({ vertical: true, horizontal: true, uncovered: true });
}

test("paste textarea inserts Tab at the caret, replaces selections, and keeps a keyboard exit", async ({
  page,
  context,
}) => {
  await wholesale(context, "demo.owner");
  await page.goto("/");
  await nav(page, "采购入库");
  await page.getByRole("button", { name: "新建采购订单", exact: true }).click();
  const editor = page.getByRole("region", { name: "新建采购单", exact: true });
  await editor.getByRole("button", { name: "粘贴多行", exact: true }).click();
  const dialog = page.getByRole("dialog", {
    name: "粘贴多行商品",
    exact: true,
  });
  const input = dialog.getByLabel("多行商品数据", { exact: true });
  await input.fill("SKU-001");
  await input.press("End");
  await input.press("Tab");
  await expect(input).toBeFocused();
  await expect(input).toHaveValue("SKU-001\t");
  await input.pressSequentially("10");
  await input.press("Tab");
  await input.pressSequentially("12.50");
  await input.press("Enter");
  await input.pressSequentially("SKU-002");
  await input.press("Tab");
  await input.pressSequentially("2");
  await input.press("Tab");
  await input.pressSequentially("3");
  await expect(input).toHaveValue("SKU-001\t10\t12.50\nSKU-002\t2\t3");
  await input.evaluate((el: HTMLTextAreaElement) =>
    el.setSelectionRange(7, 10),
  );
  await input.press("Tab");
  await expect(input).toHaveValue("SKU-001\t\t12.50\nSKU-002\t2\t3");
  await expect
    .poll(() =>
      input.evaluate((el: HTMLTextAreaElement) => [
        el.selectionStart,
        el.selectionEnd,
      ]),
    )
    .toEqual([8, 8]);
  await input.dispatchEvent("keydown", {
    key: "Tab",
    isComposing: true,
    bubbles: true,
  });
  await expect(input).toHaveValue("SKU-001\t\t12.50\nSKU-002\t2\t3");
  await input.fill("x".repeat(30000));
  await input.press("End");
  await input.press("Tab");
  await expect(input).toBeFocused();
  expect((await input.inputValue()).length).toBe(30000);
  await input.press("Shift+Tab");
  await expect(input).not.toBeFocused();
  await dialog.getByRole("button", { name: "返回", exact: true }).click();
  await expect(editor.locator(".entry-row")).toHaveCount(1);
});

for (const purchase of [true, false]) {
  test(`${purchase ? "purchase" : "sales"} long entry keeps keyboard targets above totals and footer after resize`, async ({
    page,
    context,
    browser,
  }) => {
    test.setTimeout(150000);
    const owner = await wholesale(context, "demo.owner"),
      reviewer = await browser.newContext();
    const channel = purchase ? "purchase" : "sale";
    const path = `/api/v1/entry-templates/${channel}`;
    const original = await read(context, path);
    await command(context, owner, path, {
      expected_version: original.version,
      template: {
        schema_version: 2,
        columns: defaultEntryColumns().map(({ required, editable, ...c }) => c),
      },
    });
    try {
      const checker = await wholesale(reviewer, "demo.quality");
      const f = await stockedCustomer(context, owner, reviewer, checker);
      const lookup = await read(
        context,
        `/api/v1/catalog/entry-products?entity_id=${f.entity}&channel=${channel}&sku_id=${f.product.sku_id}`,
      );
      const code = lookup.items[0].code;
      await page.setViewportSize({ width: 1912, height: 962 });
      await page.goto("/");
      await nav(page, purchase ? "采购入库" : "批发销售");
      const action = purchase ? "新建采购单" : "新建销售单";
      await page.getByRole("button", { name: action === "新建采购单" ? "新建采购订单" : action, exact: true }).click();
      const editor = page.getByRole("region", { name: action, exact: true });
      await editor
        .getByRole("button", { name: "粘贴多行", exact: true })
        .click();
      const dialog = page.getByRole("dialog", {
        name: "粘贴多行商品",
        exact: true,
      });
      await dialog
        .getByLabel("多行商品数据", { exact: true })
        .fill(Array.from({ length: 49 }, () => `${code}\t2\t3`).join("\n"));
      await dialog
        .getByRole("button", { name: "校验并预览", exact: true })
        .click();
      await dialog
        .getByRole("button", { name: "确认填入 49 行", exact: true })
        .click();
      const rows = editor.locator(".entry-row");
      await expect(rows).toHaveCount(49);
      const quantity = (n: number) =>
        rows
          .nth(n)
          .getByLabel(purchase ? "采购数量" : "销售数量", { exact: true });
      await visibleControl(quantity(0));
      for (let i = 1; i < 49; i++) {
        await quantity(i - 1).press("Alt+ArrowDown");
        await visibleControl(quantity(i));
      }
      await quantity(48).press("Enter");
      const price = rows.nth(48).getByLabel("含税单价（元）", { exact: true });
      await visibleControl(price);
      await price.press("Enter");
      await expect(rows).toHaveCount(50);
      const lastProduct = rows
        .last()
        .getByRole("combobox", { name: "商品", exact: true });
      await visibleControl(lastProduct);
      await lastProduct.fill(code);
      await lastProduct.press("Enter");
      await visibleControl(quantity(49));
      await quantity(49).fill("4");
      await quantity(49).press("Enter");
      const lastPrice = rows
        .last()
        .getByLabel("含税单价（元）", { exact: true });
      await lastPrice.fill("5");
      for (const size of [
        { width: 1366, height: 768 },
        { width: 1024, height: 768 },
      ]) {
        await page.setViewportSize(size);
        await visibleControl(lastPrice);
        await expect
          .poll(() =>
            editor
              .locator(".editor-actions")
              .evaluate(
                (el) => el.getBoundingClientRect().bottom <= innerHeight,
              ),
          )
          .toBe(true);
        await expect
          .poll(() =>
            page.evaluate(
              () => document.documentElement.scrollHeight <= innerHeight + 1,
            ),
          )
          .toBe(true);
      }
      await lastPrice.press("Alt+ArrowUp");
      for (let i = 48; i > 0; i--) {
        const control = rows
          .nth(i)
          .getByLabel("含税单价（元）", { exact: true });
        await visibleControl(control);
        await control.press("Alt+ArrowUp");
      }
      await visibleControl(
        rows.first().getByLabel("含税单价（元）", { exact: true }),
      );
      // Returning to the bottom by mouse then focusing must work just like keyboard navigation.
      await quantity(49).click();
      await visibleControl(quantity(49));
      await page.screenshot({
        path: `../../artifacts/entry-visibility-${channel}.png`,
        animations: "disabled",
      });
      await editor
        .getByRole("button", { name: "保存草稿", exact: true })
        .click();
      await expect(editor.locator(".editor-actions")).toContainText(
        "草稿已保存",
      );
    } finally {
      const latest = await read(context, path);
      await command(context, owner, path, {
        expected_version: latest.version,
        template: original.template,
      });
      await reviewer.close();
    }
  });
}
