import { test, expect } from "@playwright/test";
import { packagingReferencePrice } from "../src/entryTrade";
import {
  wholesale,
  stockedCustomer,
  command,
  read,
  nav,
  chooseEntrySku,
  chooseParty,
} from "./business-helpers";

test("reference price conversion preserves zero, decimal precision, explicit rounding and document bounds", () => {
  expect(packagingReferencePrice(null)).toBeNull();
  expect(packagingReferencePrice("0.000000", "100")).toEqual({
    value: "0",
    rounded: false,
  });
  expect(packagingReferencePrice("1.123456", "100")).toEqual({
    value: "112.3456",
    rounded: false,
  });
  expect(packagingReferencePrice("1.123456", "1", "3")).toEqual({
    value: "0.374485",
    rounded: true,
  });
  expect(packagingReferencePrice("0.000001", "1", "2")).toEqual({
    value: "0.000001",
    rounded: true,
  });
  expect(packagingReferencePrice("0.000001", "1", "3")).toEqual({
    value: "0",
    rounded: true,
  });
  expect(packagingReferencePrice("99999999999999.999999")).toEqual({
    value: "99999999999999.999999",
    rounded: false,
  });
  expect(packagingReferencePrice("999999999999.999999", "1000")).toBeNull();
  expect(packagingReferencePrice("1e3")).toBeNull();
  expect(packagingReferencePrice("1", "1", "0")).toBeNull();
});

for (const purchase of [true, false]) {
  test(`${purchase ? "purchase" : "sales"} reference hints follow rows, preserve manual prices and save actual package prices`, async ({
    page,
    context,
    browser,
  }) => {
    test.setTimeout(150000);
    const owner = await wholesale(context, "demo.owner"),
      reviewer = await browser.newContext();
    const faults: string[] = [];
    page.on("pageerror", (error) => faults.push(error.message));
    try {
      const checker = await wholesale(reviewer, "demo.quality");
      const f = await stockedCustomer(context, owner, reviewer, checker);
      const path = `/api/v1/catalog/skus/${f.product.sku_id}`;
      await command(context, owner, `${path}/prices`, {
        expected_version: "0",
        purchase_price: "1.123456",
        wholesale_price: "2.5",
        retail_price: "8",
        note: "虚构验收参考价格",
        reason: "验证单据价格提示",
      });
      const packs: string[] = [];
      for (const [unit_name, numerator, denominator] of [
        ["箱", "100", "1"],
        ["袋", "1", "3"],
      ]) {
        const current = await read(context, `${path}/packaging`);
        const created = await command(context, owner, `${path}/packaging`, {
          expected_sku_version: current.sku_version,
          unit_name,
          numerator,
          denominator,
          reason: "虚构验收包装换算",
        });
        await command(
          reviewer,
          checker,
          `${path}/packaging/${created.id}/review`,
          {
            expected_version: "1",
            decision: "APPROVE",
            evidence_ref: "DEMO-ONLY",
            reason: "独立核对换算关系",
          },
        );
        packs.push(created.id);
      }
      await page.goto("/");
      await nav(page, purchase ? "采购入库" : "批发销售");
      const title = purchase ? "新建采购单" : "新建销售单";
      await page.getByRole("button", { name: title === "新建采购单" ? "新建采购订单" : title, exact: true }).click();
      const editor = page.getByRole("region", { name: title, exact: true });
      const rows = editor.locator(".entry-row"),
        first = rows.first();
      await chooseEntrySku(first, f.product.sku_id);
      const hint = editor.getByLabel("当前行单位与参考价格", { exact: true });
      const price = first.getByLabel("含税单价（元）", { exact: true });
      const qty = first.getByLabel(purchase ? "采购数量" : "销售数量", {
        exact: true,
      });
      const base = purchase ? "1.123456" : "2.5",
        casePrice = purchase ? "112.3456" : "250";
      await expect(hint).toContainText(
        `商品资料 · 参考${purchase ? "采购" : "批发"}价 ¥${base}／个（含税）`,
      );
      await expect(price).toHaveValue("");
      await hint
        .getByRole("button", { name: "填入参考价", exact: true })
        .click();
      await expect(price).toHaveValue(base);
      await expect(price).toBeFocused();
      await qty.fill("2");
      await first
        .getByRole("combobox", { name: "交易单位 1", exact: true })
        .selectOption(packs[0]!);
      await expect(price).toHaveValue(base);
      await expect(hint).toContainText("1 箱 ＝ 100 个");
      await expect(hint).toContainText("折合 200 个");
      await expect(hint).toContainText(`换算 ¥${casePrice}／箱`);
      await expect(
        editor.getByLabel("当前行信息", { exact: true }),
      ).toContainText("原数量和单价已保留");
      await expect(
        hint.getByRole("button", { name: "填入参考价", exact: true }),
      ).toBeDisabled();
      await price.fill("");
      await hint
        .getByRole("button", { name: "填入参考价", exact: true })
        .click();
      await expect(price).toHaveValue(casePrice);
      await editor
        .getByRole("button", { name: "复制当前商品行", exact: true })
        .click();
      await expect(rows).toHaveCount(2);
      const second = rows.nth(1),
        secondPrice = second.getByLabel("含税单价（元）", { exact: true });
      await second
        .getByRole("combobox", { name: "交易单位 2", exact: true })
        .selectOption(packs[1]!);
      await second
        .getByLabel(purchase ? "采购数量" : "销售数量", { exact: true })
        .fill("3");
      await expect(hint).toContainText("3 袋 ＝ 1 个");
      await expect(hint).toContainText("折合 1 个");
      await expect(hint).toContainText("四舍五入至6位小数");
      await secondPrice.fill("");
      await hint
        .getByRole("button", { name: "填入参考价", exact: true })
        .click();
      await expect(secondPrice).toHaveValue(purchase ? "0.374485" : "0.833333");
      await secondPrice.fill("9.99");
      await qty.focus();
      await expect(hint).toContainText("1 箱 ＝ 100 个");
      await expect(price).toHaveValue(casePrice);
      await expect(secondPrice).toHaveValue("9.99");

      if (purchase) {
        // A delayed response for the previous row must not replace the active
        // row's reference snapshot, even when both rows contain the same SKU.
        let release!: () => void, started!: () => void;
        const gate = new Promise<void>((resolve) => {
          release = resolve;
        });
        const requested = new Promise<void>((resolve) => {
          started = resolve;
        });
        let hold = true;
        await page.route(`**${path}/prices`, async (route) => {
          if (!hold) return route.continue();
          hold = false;
          started();
          await gate;
          await route
            .fulfill({
              status: 200,
              contentType: "application/json",
              body: JSON.stringify({
                data: {
                  base_unit: "个",
                  values: { purchase_price: "777", wholesale_price: "888" },
                },
              }),
            })
            .catch(() => {});
        });
        await hint
          .getByRole("button", { name: "刷新参考信息", exact: true })
          .click();
        await requested;
        await secondPrice.focus();
        await expect(hint).toContainText("参考采购价 ¥1.123456／个");
        release();
        await page.unrouteAll({ behavior: "wait" });
        await expect(hint).not.toContainText("¥777");
        await expect(secondPrice).toHaveValue("9.99");
        await qty.focus();
        await expect(hint).toContainText("1 箱 ＝ 100 个");
      }

      // Refresh is read-only even if master prices have changed to zero / blank.
      await command(context, owner, `${path}/prices`, {
        expected_version: "1",
        purchase_price: "0",
        wholesale_price: null,
        retail_price: "8",
        note: "更新测试参考价",
        reason: "验证已录入单价不被刷新覆盖",
      });
      await hint
        .getByRole("button", { name: "刷新参考信息", exact: true })
        .click();
      await expect(hint).toContainText(
        purchase ? "参考采购价 ¥0／个" : "参考批发价未设置",
      );
      await expect(price).toHaveValue(casePrice);
      if (purchase) {
        await price.fill("");
        await hint
          .getByRole("button", { name: "填入参考价", exact: true })
          .click();
        await expect(price).toHaveValue("0");
        await price.fill(casePrice);
      } else
        await expect(
          hint.getByRole("button", { name: "填入参考价", exact: true }),
        ).toHaveCount(0);

      // A reference lookup failure must leave the edited document usable.
      await page.route(`**${path}/prices`, (route) =>
        route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ error: { message: "测试读取失败" } }),
        }),
      );
      await hint
        .getByRole("button", { name: "刷新参考信息", exact: true })
        .click();
      await expect(hint).toContainText("参考价读取失败");
      await expect(price).toHaveValue(casePrice);
      await page.unroute(`**${path}/prices`);
      await hint
        .getByRole("button", { name: "刷新参考信息", exact: true })
        .click();
      await expect(hint).not.toContainText("参考价读取失败");
      await expect(hint).not.toContainText("正在读取参考信息");
      await page.setViewportSize({ width: 480, height: 900 });
      await expect
        .poll(() =>
          page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth,
          ),
        )
        .toBe(true);
      await page.setViewportSize({ width: 1440, height: 1000 });
      await page.screenshot({
        path: `../../outputs/mdm-basic-fields/${purchase ? "采购" : "销售"}单位与参考价格.png`,
        fullPage: true,
      });
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
          .fill("虚构验收地址");
      const endpoint = purchase
        ? "/api/v1/purchase-orders"
        : "/api/v1/sales-orders";
      const saving = page.waitForResponse(
        (r) => r.url().endsWith(endpoint) && r.request().method() === "POST",
      );
      await editor
        .getByRole("button", {
          name: purchase ? "保存采购单" : "保存销售单",
          exact: true,
        })
        .click();
      const response = await saving;
      expect(response.status(), await response.text()).toBe(200);
      const sent = response.request().postDataJSON();
      expect(
        sent.lines.map((line: any) => [
          line.packaging_id,
          line.qty,
          line.unit_price,
        ]),
      ).toEqual([
        [packs[0], "2", casePrice],
        [packs[1], "3", "9.99"],
      ]);
      expect(faults).toEqual([]);
    } finally {
      await reviewer.close();
    }
  });
}
