import { read, command, entryTemplatePath } from "./entry-template-helpers";
import { chooseParty } from "./business-helpers";
import { test, expect } from "@playwright/test";
import { wholesale, stockedCustomer, nav } from "./business-helpers";
import {
  defaultEntryColumns,
  newEntryLine,
  lineCents,
  centsText,
  parseEntryPaste,
} from "../src/documentEntry";

test("entry totals preserve exact decimal cents and spreadsheet limits", () => {
  expect(
    centsText(lineCents({ ...newEntryLine(), qty: "3", unit_price: "0.335" })!),
  ).toBe("1.01");
  expect(
    centsText(
      lineCents({
        ...newEntryLine(),
        qty: "90071992547409",
        unit_price: "0.01",
      })!,
    ),
  ).toBe("900719925474.09");
  expect(
    lineCents({ ...newEntryLine(), qty: "1e3", unit_price: "1" }),
  ).toBeNull();
  const fifty = Array.from({ length: 50 }, () => "SKU\t2\t0.105").join("\r\n");
  expect(parseEntryPaste(fifty)).toHaveLength(50);
  expect(() => parseEntryPaste(fifty + "\nSKU\t1\t1")).toThrow("50");
});

for (const purchase of [true, false]) {
  test(`${purchase ? "purchase 50" : "sales 12"} independent rows: keyboard, paste, undo, column preferences and revision`, async ({
    page,
    context,
    browser,
  }) => {
    test.setTimeout(180000);
    const owner = await wholesale(context, "demo.owner"),
      reviewer = await browser.newContext();
    const faults: string[] = [];
    page.on("pageerror", (e) => faults.push(e.message));
    page.on("console", (message) => {
      if (message.text().includes("Failed to resolve component"))
        faults.push(message.text());
    });
    const templatePath = entryTemplatePath(purchase, owner.entities[0].id);
    const original = await read(context, templatePath);
    await command(context, owner, templatePath, {
      expected_version: original.version,
      template: {
        schema_version: 2,
        columns: defaultEntryColumns().map(({ required, editable, ...c }) => c),
      },
    });
    try {
      const checker = await wholesale(reviewer, "demo.quality");
      const f = await stockedCustomer(context, owner, reviewer, checker);
      const channel = purchase ? "purchase" : "sale",
        endpoint = purchase
          ? "/api/v1/purchase-orders"
          : "/api/v1/sales-orders";
      const menu = purchase ? "采购入库" : "批发销售",
        action = purchase ? "新建采购单" : "新建销售单",
        qtyName = purchase ? "采购数量" : "销售数量";
      const lookup = await read(
        context,
        `/api/v1/catalog/entry-products?entity_id=${f.entity}&channel=${channel}&sku_id=${f.product.sku_id}`,
      );
      const code = lookup.items[0].code;
      await page.goto("/");
      await nav(page, menu);
      await page
        .getByRole("button", {
          name: action === "新建采购单" ? "新建采购订单" : action,
          exact: true,
        })
        .click();
      const form = page.getByRole("region", { name: action, exact: true });
      if (!purchase)
        await form
          .getByLabel("收货地址", { exact: true })
          .fill("键盘连续录入验收点");
      const rows = form.locator(".entry-row");
      const first = rows.nth(0),
        search = first.getByRole("combobox", { name: "商品", exact: true });
      // Enter while a debounced search is still pending must select the fresh result.
      await search.fill(code);
      await search.press("Enter");
      const qty = first.getByLabel(qtyName, { exact: true });
      await expect(qty).toBeFocused();
      await qty.fill("3");
      await qty.press("Enter");
      await expect(
        first.getByLabel("含税单价（元）", { exact: true }),
      ).toBeFocused();
      await first.getByLabel("含税单价（元）", { exact: true }).fill("0.335");
      await first.getByLabel("含税单价（元）", { exact: true }).press("Tab");
      await expect(rows).toHaveCount(2);
      await first.getByLabel("税率（小数）", { exact: true }).selectText();
      await first
        .getByLabel("税率（小数）", { exact: true })
        .pressSequentially("0.06");
      await expect(
        first.getByLabel("税率（小数）", { exact: true }),
      ).not.toHaveAttribute("list");
      await first.getByLabel("税率（小数）", { exact: true }).press("Enter");
      await expect(rows).toHaveCount(2);
      const second = rows.nth(1);
      // Check Enter navigation before deliberately moving focus to the header.
      await expect(
        second.getByRole("combobox", { name: "商品", exact: true }),
      ).toBeFocused();
      await chooseParty(
        form.getByRole("combobox", {
          name: purchase ? "供应商" : "客户",
          exact: true,
        }),
        f.party.id,
      );
      await second
        .getByRole("combobox", { name: "商品", exact: true })
        .fill(code);
      await second
        .getByRole("combobox", { name: "商品", exact: true })
        .press("Enter");
      await expect(second.getByLabel(qtyName, { exact: true })).toBeFocused();
      await second.getByLabel(qtyName, { exact: true }).fill("0");
      await second.getByLabel(qtyName, { exact: true }).press("Enter");
      await expect(second.getByLabel(qtyName, { exact: true })).toBeFocused();
      await expect(form.getByRole("alert")).toContainText("第 2 行");
      await second.getByLabel(qtyName, { exact: true }).fill("2");
      await second.getByLabel("含税单价（元）", { exact: true }).fill("0.105");
      await expect(first).toContainText("第 2 行同商品");
      await expect(form.locator(".entry-summary")).toContainText("1.22");
      await first
        .getByRole("button", { name: "选择常用税率", exact: true })
        .click();
      await page
        .getByRole("group", { name: "常用税率", exact: true })
        .getByRole("button", { name: "9%", exact: true })
        .click();
      await expect(
        first.getByLabel("税率（小数）", { exact: true }),
      ).toHaveValue("0.09");
      await second.getByLabel(qtyName, { exact: true }).press("Alt+ArrowUp");
      await expect(qty).toBeFocused();
      await qty.press("Alt+Insert");
      await expect(rows).toHaveCount(3);
      await expect(
        rows.nth(1).getByRole("combobox", { name: "商品", exact: true }),
      ).toBeFocused();
      await rows
        .nth(1)
        .getByRole("combobox", { name: "商品", exact: true })
        .press("Alt+Delete");
      await expect(rows).toHaveCount(2);
      await first.getByLabel(qtyName, { exact: true }).press("Alt+Delete");
      await expect(rows).toHaveCount(1);
      await expect(
        rows.nth(0).getByLabel(qtyName, { exact: true }),
      ).toHaveValue("2");
      await form.getByRole("button", { name: "撤销删除", exact: true }).click();
      await expect(rows).toHaveCount(2);
      await expect(qty).toHaveValue("3");
      await expect(
        first.getByLabel("含税单价（元）", { exact: true }),
      ).toHaveValue("0.335");

      await search.fill("尚未确认的商品文字");
      await search.press("Tab");
      await expect(search).toBeFocused();
      await search.press("Escape");
      await expect(form).toBeVisible();
      await expect(search).toHaveValue(`${f.name} · ${code}`);
      await expect(qty).toHaveValue("3");
      await qty.focus();
      await qty.dispatchEvent("keydown", {
        key: "Enter",
        code: "Enter",
        isComposing: true,
        bubbles: true,
      });
      await expect(qty).toBeFocused();
      await form.getByRole("button", { name: "列设置", exact: true }).click();
      const settings = page.getByRole("dialog", {
        name: "配置单据明细列",
        exact: true,
      });
      await expect(
        settings.getByRole("checkbox", { name: "商品", exact: true }),
      ).toBeDisabled();
      await settings
        .getByRole("checkbox", { name: "规格型号", exact: true })
        .check();
      await settings
        .getByRole("checkbox", { name: "生产企业", exact: true })
        .check();
      await settings
        .getByRole("button", { name: "数量 前移", exact: true })
        .click();
      await settings
        .getByRole("button", {
          name: "税率 前移",
          exact: true,
        })
        .click();
      await settings
        .getByRole("checkbox", { name: "交易单位 回车停留", exact: true })
        .check();
      await settings
        .getByRole("button", {
          name: "保存配置",
          exact: true,
        })
        .click();
      await expect(settings).not.toBeVisible();
      await qty.focus();
      await qty.press("Tab");
      await expect(
        first.getByRole("combobox", { name: "交易单位 1", exact: true }),
      ).toBeFocused();
      await first.getByLabel("税率（小数）", { exact: true }).fill("0.03");
      await first.getByLabel("税率（小数）", { exact: true }).press("Enter");
      await expect(
        first.getByLabel("含税单价（元）", { exact: true }),
      ).toBeFocused();

      const pasteData = Array.from(
        { length: 10 },
        () => `${code}\t1\t1.005\t0.13`,
      ).join("\n");
      await form.getByRole("button", { name: "粘贴多行", exact: true }).click();
      let paste = page.getByRole("dialog", {
        name: "粘贴多行商品",
        exact: true,
      });
      await paste
        .getByLabel("多行商品数据")
        .fill(`${code}\t1\t1\nNO-SUCH-SKU\t2\t3`);
      await paste
        .getByRole("button", { name: "校验并预览", exact: true })
        .click();
      await expect(paste).toContainText("SKU编码未唯一匹配");
      await expect(
        paste.getByRole("button", { name: "确认填入 2 行", exact: true }),
      ).toBeDisabled();
      await expect(rows).toHaveCount(2);
      // Ctrl+Enter in a nested paste dialog must not save its parent document.
      await paste.getByLabel("多行商品数据").press("Control+Enter");
      await expect(form).toBeVisible();
      await paste.getByLabel("多行商品数据").fill(pasteData);
      await paste
        .getByRole("button", { name: "校验并预览", exact: true })
        .click();
      await paste
        .getByRole("button", { name: "确认填入 10 行", exact: true })
        .click();
      await expect(paste).not.toBeVisible();
      await expect(rows).toHaveCount(12);
      await expect(form.locator(".entry-summary")).toContainText("11.32");
      // Compact rows can all fit at 1000px tall. Exercise scrolling in a smaller work window.
      await page.setViewportSize({ width: 1366, height: 768 });
      const scroll = form.locator(".entry-scroll");
      expect(
        await scroll.evaluate((el) => el.scrollHeight > el.clientHeight),
      ).toBeTruthy();
      await rows.nth(6).getByLabel(qtyName, { exact: true }).focus();
      await page.screenshot({
        path: `../../artifacts/document-entry-${channel}.png`,
        animations: "disabled",
      });
      let expectedRows = 12;
      if (purchase) {
        await form
          .getByRole("button", { name: "粘贴多行", exact: true })
          .click();
        paste = page.getByRole("dialog", { name: "粘贴多行商品", exact: true });
        await paste
          .getByLabel("多行商品数据")
          .fill(
            Array.from({ length: 39 }, () => `${code}\t1\t1.005`).join("\n"),
          );
        await paste
          .getByRole("button", { name: "校验并预览", exact: true })
          .click();
        await expect(paste.getByRole("alert")).toContainText(
          "最多还能添加 38 行",
        );
        await paste
          .getByLabel("多行商品数据")
          .fill(
            Array.from({ length: 38 }, () => `${code}\t1\t1.005`).join("\n"),
          );
        await paste
          .getByRole("button", { name: "校验并预览", exact: true })
          .click();
        await paste
          .getByRole("button", { name: "确认填入 38 行", exact: true })
          .click();
        await expect(rows).toHaveCount(50);
        expectedRows = 50;
        await expect(
          form.getByRole("button", { name: "添加商品行", exact: true }),
        ).toBeDisabled();
      } else {
        await form
          .getByRole("button", { name: "添加商品行", exact: true })
          .click();
        await expect(rows).toHaveCount(13); // Empty tail is deliberately omitted on save.
      }
      const saved = page.waitForResponse(
        (r) => r.url().endsWith(endpoint) && r.request().method() === "POST",
      );
      await rows
        .nth(0)
        .getByLabel(qtyName, { exact: true })
        .press("Control+Enter");
      const response = await saved;
      expect(response.status(), await response.text()).toBe(200);
      const payload = response.request().postDataJSON();
      expect(payload.lines).toHaveLength(expectedRows);
      expect(payload.lines[0]).toEqual({
        sku_id: f.product.sku_id,
        qty: "3",
        unit_price: "0.335",
        tax_rate: "0.03",
      });
      expect(payload.lines[1].unit_price).toBe("0.105");
      expect(payload.lines[expectedRows - 1].unit_price).toBe("1.005");
      const created = (await response.json()).data;
      const doc = await read(context, `${endpoint}/${created.id}`);
      expect(doc.lines).toHaveLength(expectedRows);
      expect(doc.lines[0].qty).toBe("3.000000");
      expect(doc.lines[0].unit_price).toBe("0.335000");
      if (!purchase) expect(doc.lines[0].gross_amount).toBe("1.01");
      await expect(form).not.toBeVisible();

      let releaseHydration!: () => void;
      const hydrationGate = new Promise<void>(
        (resolve) => (releaseHydration = resolve),
      );
      let hydrationReads = 0;
      await page.route("**/api/v1/catalog/entry-products?**", async (route) => {
        if (
          new URL(route.request().url()).searchParams.get("sku_id") !==
          f.product.sku_id
        )
          return route.continue();
        hydrationReads++;
        const result = await route.fetch();
        await hydrationGate;
        await route.fulfill({ response: result });
      });
      const editTitle = purchase ? "修改采购单" : "修改销售单";
      await page.getByRole("button", { name: editTitle, exact: true }).click();
      const edit = page.getByRole("region", { name: editTitle, exact: true });
      await expect(edit.locator(".entry-row")).toHaveCount(expectedRows);
      try {
        await expect.poll(() => hydrationReads).toBe(1);
        await expect(
          edit.getByRole("button", { name: "保存变更并待审", exact: true }),
        ).toBeDisabled();
        await expect(edit.locator(".editor-actions")).toContainText(
          "正在读取商品资料",
        );
      } finally {
        releaseHydration();
      }
      await expect(
        edit.getByRole("button", { name: "保存变更并待审", exact: true }),
      ).toBeEnabled();
      expect(hydrationReads).toBe(1);
      await page.unroute("**/api/v1/catalog/entry-products?**");
      const headings = await edit
        .locator(".entry-table thead th")
        .allTextContents();
      expect(headings.indexOf("数量")).toBeLessThan(
        headings.indexOf("交易单位"),
      );
      await expect(
        edit.getByRole("columnheader", { name: "生产企业", exact: true }),
      ).toBeVisible();
      await edit
        .getByLabel("变更原因", { exact: true })
        .fill("多行键盘改单验收");
      const last = edit.locator(".entry-row").last();
      await last.getByLabel("含税单价（元）", { exact: true }).fill("2.005");
      await expect(
        edit.getByRole("button", { name: "保存并新建", exact: true }),
      ).toHaveCount(0);
      // Rows can render before the asynchronously loaded header choices are ready.
      await expect(
        edit.getByRole("button", { name: "保存变更并待审", exact: true }),
      ).toBeEnabled();
      const revised = page.waitForResponse(
        (r) =>
          r.url().endsWith(`${endpoint}/${created.id}/revise`) &&
          r.request().method() === "POST",
      );
      await last
        .getByLabel("含税单价（元）", { exact: true })
        .press("Control+Enter");
      const revision = await revised;
      expect(revision.status(), await revision.text()).toBe(200);
      const changed = await read(context, `${endpoint}/${created.id}`);
      expect(changed.lines).toHaveLength(expectedRows);
      expect(changed.lines[expectedRows - 1].unit_price).toBe("2.005000");
      expect(changed.lines[0].unit_price).toBe("0.335000");
      if (!purchase) {
        expect(changed.lines[expectedRows - 1].gross_amount).toBe("2.01");
        expect(changed.lines[0].gross_amount).toBe("1.01");
      }
      expect(faults).toEqual([]);
    } finally {
      const latest = await read(context, templatePath);
      await command(context, owner, templatePath, {
        expected_version: latest.version,
        template: original.template || {
          schema_version: 2,
          columns: defaultEntryColumns().map(
            ({ required, editable, ...c }) => c,
          ),
        },
      });
      await reviewer.close();
    }
  });
}
