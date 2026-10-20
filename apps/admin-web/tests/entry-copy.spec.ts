import { read, command, entryTemplatePath } from "./entry-template-helpers";
import { chooseParty } from "./business-helpers";
import { test, expect } from "@playwright/test";
import {
  wholesale,
  stockedCustomer,
  nav,
  chooseEntrySku,
} from "./business-helpers";
import { defaultEntryColumns } from "../src/documentEntry";

for (const purchase of [true, false]) {
  test(`${purchase ? "purchase" : "sales"} copy preserves hidden values, independent edits, safe undo and saved rows`, async ({
    page,
    context,
    browser,
  }) => {
    test.setTimeout(150000);
    const owner = await wholesale(context, "demo.owner");
    const reviewer = await browser.newContext();
    const channel = purchase ? "purchase" : "sale";
    const templatePath = entryTemplatePath(purchase, owner.entities[0].id);
    const original = await read(context, templatePath);
    const defaults = {
      schema_version: 2,
      columns: defaultEntryColumns().map(({ required, editable, ...c }) => c),
    };
    const faults: string[] = [];
    page.on("pageerror", (e) => faults.push(e.message));
    try {
      await command(context, owner, templatePath, {
        expected_version: original.version,
        template: defaults,
      });
      const checker = await wholesale(reviewer, "demo.quality");
      const f = await stockedCustomer(context, owner, reviewer, checker);
      const path = `/api/v1/catalog/skus/${f.product.sku_id}/packaging`;
      const choices = await read(context, path);
      const pack = await command(context, owner, path, {
        expected_sku_version: choices.sku_version,
        unit_name: "箱",
        numerator: "3",
        denominator: "1",
        reason: "复制行虚拟验收",
      });
      await command(reviewer, checker, `${path}/${pack.id}/review`, {
        expected_version: pack.version,
        decision: "APPROVE",
        evidence_ref: "COPY-PACK",
        reason: "独立核对包装",
      });
      await page.setViewportSize({ width: 1366, height: 768 });
      await page.goto("/");
      await nav(page, purchase ? "采购入库" : "批发销售");
      const action = purchase ? "新建采购单" : "新建销售单";
      const qtyLabel = purchase ? "采购数量" : "销售数量";
      const endpoint = purchase
        ? "/api/v1/purchase-orders"
        : "/api/v1/sales-orders";
      await page
        .getByRole("button", {
          name: action === "新建采购单" ? "新建采购订单" : action,
          exact: true,
        })
        .click();
      const editor = page.getByRole("region", { name: action, exact: true });
      const rows = editor.locator(".entry-row");
      const first = rows.first();
      const copy = editor.getByRole("button", {
        name: "复制当前商品行",
        exact: true,
      });
      const undo = editor.getByRole("button", {
        name: "撤销复制",
        exact: true,
      });
      const qty = first.getByLabel(qtyLabel, { exact: true });
      await expect(copy).toBeDisabled();
      await chooseEntrySku(first, f.product.sku_id);
      await first
        .getByRole("combobox", { name: "交易单位 1", exact: true })
        .selectOption(pack.id);
      await qty.fill("2.000000");
      await first
        .getByLabel("含税单价（元）", { exact: true })
        .fill("0.335001");
      await first.getByLabel("税率（小数）", { exact: true }).fill("0.060000");
      const sourceKey = await first.getAttribute("data-row-key");
      await copy.click();
      await expect(rows).toHaveCount(2);
      const second = rows.nth(1);
      await expect(second.getByLabel(qtyLabel, { exact: true })).toBeFocused();
      expect(await second.getAttribute("data-row-key")).not.toBe(sourceKey);
      await expect(second.getByLabel(qtyLabel, { exact: true })).toHaveValue(
        "2.000000",
      );
      await expect(
        second.getByRole("combobox", { name: "交易单位 2", exact: true }),
      ).toHaveValue(pack.id);
      await expect(
        second.getByLabel("含税单价（元）", { exact: true }),
      ).toHaveAttribute("title", "原值：0.335001");
      await expect(editor.locator(".entry-summary")).toContainText("1.34");
      await undo.click();
      await expect(rows).toHaveCount(1);
      await expect(qty).toBeFocused();
      await qty.press("Alt+Shift+Insert");
      await expect(rows).toHaveCount(2);
      await second.getByLabel(qtyLabel, { exact: true }).fill("3.000000");
      await second
        .getByLabel("含税单价（元）", { exact: true })
        .fill("1.005001");
      await expect(undo).toBeDisabled();
      await expect(qty).toHaveAttribute("title", "原值：2.000000");
      await expect(
        first.getByLabel("含税单价（元）", { exact: true }),
      ).toHaveAttribute("title", "原值：0.335001");
      await expect(editor.locator(".entry-summary")).toContainText("3.69");
      const search = first.getByRole("combobox", { name: "商品", exact: true });
      await search.fill("尚未确认的商品文字");
      await copy.click();
      await expect(editor.getByRole("alert")).toContainText("请先确认商品");
      await expect(rows).toHaveCount(2);
      await search.press("Escape");

      // Hidden columns must still be copied, even when their controls are unmounted.
      await editor.getByRole("button", { name: "列设置", exact: true }).click();
      const settings = page.getByRole("dialog", {
        name: "配置单据明细列",
        exact: true,
      });
      await settings
        .getByRole("checkbox", { name: "交易单位", exact: true })
        .uncheck();
      await settings
        .getByRole("checkbox", {
          name: "税率",
          exact: true,
        })
        .uncheck();
      await settings
        .getByRole("button", {
          name: "保存配置",
          exact: true,
        })
        .click();
      await expect(settings).not.toBeVisible();
      await qty.focus();
      await copy.click();
      await expect(rows).toHaveCount(3);
      await expect(second.getByLabel(qtyLabel, { exact: true })).toBeFocused();
      await expect(editor.locator(".entry-summary")).toContainText(
        "使用包装单位",
      );
      await expect(editor.locator(".entry-summary")).toContainText(
        "税率 0.060000",
      );
      // Undo targets its own row and preserves later edits to other rows.
      await rows.last().getByLabel(qtyLabel, { exact: true }).fill("4.000000");
      await undo.click();
      await expect(rows).toHaveCount(2);
      await expect(
        second.getByLabel(qtyLabel, { exact: true }),
      ).toHaveAttribute("title", "原值：4.000000");
      await chooseParty(
        editor.getByRole("combobox", {
          name: purchase ? "供应商" : "客户",
          exact: true,
        }),
        f.party.id,
      );
      if (!purchase)
        await editor
          .getByLabel("收货地址", { exact: true })
          .fill("复制行虚拟验收点");
      await editor
        .getByRole("button", { name: "保存草稿", exact: true })
        .click();
      await expect(editor.locator(".editor-actions")).toContainText(
        "草稿已保存",
      );
      const keys = await rows.evaluateAll((rs) =>
        rs.map((r) => r.getAttribute("data-row-key")),
      );
      expect(new Set(keys).size).toBe(2);
      await page.reload();
      await expect(rows).toHaveCount(2);
      expect(
        await rows.evaluateAll((rs) =>
          rs.map((r) => r.getAttribute("data-row-key")),
        ),
      ).toEqual(keys);
      await expect(undo).toHaveCount(0); // Ephemeral undo must not remove a restored draft row.
      await expect(
        editor.getByRole("button", {
          name: purchase ? "保存采购单" : "保存销售单",
          exact: true,
        }),
      ).toBeEnabled(); // Options may still be loading just after the draft rows render.
      const saved = page.waitForResponse(
        (r) => r.url().endsWith(endpoint) && r.request().method() === "POST",
      );
      await qty.press("Control+Enter");
      const response = await saved;
      expect(response.status(), await response.text()).toBe(200);
      const payload = response.request().postDataJSON();
      expect(payload.lines).toEqual([
        {
          sku_id: f.product.sku_id,
          packaging_id: pack.id,
          qty: "2.000000",
          unit_price: "0.335001",
          tax_rate: "0.060000",
        },
        {
          sku_id: f.product.sku_id,
          packaging_id: pack.id,
          qty: "4.000000",
          unit_price: "1.005001",
          tax_rate: "0.060000",
        },
      ]);
      const created = (await response.json()).data;
      const doc = await read(context, `${endpoint}/${created.id}`);
      expect(doc.lines).toHaveLength(2);
      expect(doc.lines[0].id).not.toBe(doc.lines[1].id);
      await expect(editor).not.toBeVisible();

      // The same copy behavior applies to revisions, including the 50-row bound.
      const reviseTitle = purchase ? "修改采购单" : "修改销售单";
      await page
        .getByRole("button", { name: reviseTitle, exact: true })
        .click();
      const revision = page.getByRole("region", {
        name: reviseTitle,
        exact: true,
      });
      const revisionRows = revision.locator(".entry-row");
      await expect(revisionRows).toHaveCount(2);
      const count = purchase ? 50 : 3;
      for (let i = 2; i < count; i++)
        await revisionRows
          .last()
          .getByLabel(qtyLabel, { exact: true })
          .press("Alt+Shift+Insert");
      await expect(revisionRows).toHaveCount(count);
      if (purchase) {
        await expect(
          revision.getByRole("button", { name: "复制当前商品行", exact: true }),
        ).toBeDisabled();
        await revisionRows
          .last()
          .getByLabel(qtyLabel, { exact: true })
          .press("Alt+Shift+Insert");
        await expect(revisionRows).toHaveCount(50);
        await expect(revision.getByRole("alert")).toContainText("最多50行");
        await revision
          .getByRole("button", { name: "撤销复制", exact: true })
          .click();
        await expect(revisionRows).toHaveCount(49);
        await revisionRows
          .last()
          .getByLabel(qtyLabel, { exact: true })
          .press("Alt+Shift+Insert");
        await expect(revisionRows).toHaveCount(50);
        await page.setViewportSize({ width: 1024, height: 768 });
      }
      const lastQty = revisionRows.last().getByLabel(qtyLabel, { exact: true });
      await expect(lastQty).toBeFocused();
      await expect
        .poll(async () => {
          const input = await lastQty.boundingBox();
          const footer = await revision
            .locator(".editor-actions")
            .boundingBox();
          return !!input && !!footer && input.y + input.height <= footer.y;
        })
        .toBeTruthy();
      const revisionKeys = await revisionRows.evaluateAll((rs) =>
        rs.map((r) => r.getAttribute("data-row-key")),
      );
      expect(new Set(revisionKeys).size).toBe(count);
      await page.screenshot({
        path: `../../artifacts/entry-copy-${channel}.png`,
        animations: "disabled",
      });
      await revision
        .getByLabel("变更原因", { exact: true })
        .fill("复制行虚拟验收");
      const revised = page.waitForResponse(
        (r) =>
          r.url().endsWith(`${endpoint}/${created.id}/revise`) &&
          r.request().method() === "POST",
      );
      await lastQty.press("Control+Enter");
      const result = await revised;
      expect(result.status(), await result.text()).toBe(200);
      const after = await read(context, `${endpoint}/${created.id}`);
      expect(after.lines).toHaveLength(count);
      expect(new Set(after.lines.map((r: { id: string }) => r.id)).size).toBe(
        count,
      );
      // The document keeps trading quantities alongside converted base quantities.
      expect(after.lines[0].trade_qty).toBe("2.000000");
      expect(after.lines[0].qty).toBe("6.000000");
      expect(after.lines.at(-1).trade_qty).toBe("4.000000");
      expect(after.lines.at(-1).qty).toBe("12.000000");
      expect(after.lines.at(-1).unit_price).toBe("1.005001");
      expect(faults).toEqual([]);
    } finally {
      const latest = await read(context, templatePath);
      await command(context, owner, templatePath, {
        expected_version: latest.version,
        template: original.template || defaults,
      });
      await reviewer.close();
    }
  });
}
