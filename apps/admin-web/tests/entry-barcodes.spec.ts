import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";
import {
  wholesale,
  stockedCustomer,
  command,
  read,
  nav,
  chooseParty,
} from "./business-helpers";
import { defaultEntryColumns } from "../src/documentEntry";

for (const purchase of [true, false]) {
  test(`${purchase ? "purchase" : "sales"} barcode entry selects the exact unit, preserves rows and rejects unavailable mappings`, async ({
    page,
    context,
    browser,
  }) => {
    test.setTimeout(150000);
    const owner = await wholesale(context, "demo.owner"),
      reviewer = await browser.newContext();
    const channel = purchase ? "purchase" : "sale",
      templatePath = `/api/v1/entry-templates/${channel}`;
    const original = await read(context, templatePath);
    const template = {
      schema_version: 2,
      columns: defaultEntryColumns().map(({ required, editable, ...c }) => c),
    };
    await command(context, owner, templatePath, {
      expected_version: original.version,
      template,
    });
    const faults: string[] = [];
    page.on("pageerror", (e) => faults.push(e.message));
    try {
      const checker = await wholesale(reviewer, "demo.quality");
      const f = await stockedCustomer(context, owner, reviewer, checker);
      const path = `/api/v1/catalog/skus/${f.product.sku_id}`;
      const run = randomUUID().replaceAll("-", "");
      const baseCode = `00-${run}-BASE`,
        boxCode = `00-${run}-BOX`,
        oldCode = `00-${run}-OLD`,
        disabledCode = `00-${run}-DISABLED`;
      const pack = await command(context, owner, `${path}/packaging`, {
        expected_sku_version: (await read(context, `${path}/packaging`))
          .sku_version,
        unit_name: "箱",
        numerator: "10",
        denominator: "1",
        reason: "虚构条码开单验收包装",
      });
      await command(reviewer, checker, `${path}/packaging/${pack.id}/review`, {
        expected_version: pack.version,
        decision: "APPROVE",
        evidence_ref: "DEMO-ONLY",
        reason: "独立核对一箱十个",
      });
      const old = await command(context, owner, `${path}/packaging`, {
        expected_sku_version: (await read(context, `${path}/packaging`))
          .sku_version,
        unit_name: "旧包",
        numerator: "5",
        denominator: "1",
        reason: "虚构停用包装验收",
      });
      const approved = await command(
        reviewer,
        checker,
        `${path}/packaging/${old.id}/review`,
        {
          expected_version: old.version,
          decision: "APPROVE",
          evidence_ref: "DEMO-ONLY",
          reason: "独立核对旧包",
        },
      );
      for (const [external_code, packaging_id] of [
        [baseCode, null],
        [boxCode, pack.id],
        [oldCode, old.id],
        [disabledCode, null],
      ]) {
        const created = await command(context, owner, `${path}/codes`, {
          scheme: "BARCODE",
          external_code,
          packaging_id,
          evidence_ref: "DEMO-ONLY",
          reason: "虚构条码映射验收",
        });
        if (external_code === disabledCode)
          await command(context, owner, `${path}/codes/${created.id}/state`, {
            expected_version: created.version,
            state: "INACTIVE",
            reason: "停用测试条码",
          });
      }
      await command(reviewer, checker, `${path}/packaging/${old.id}/review`, {
        expected_version: approved.version,
        decision: "SUSPEND",
        evidence_ref: "DEMO-ONLY",
        reason: "停用测试包装",
      });
      await page.goto("/");
      await nav(page, purchase ? "采购入库" : "批发销售");
      const title = purchase ? "新建采购单" : "新建销售单";
      await page.getByRole("button", { name: title === "新建采购单" ? "新建采购订单" : title, exact: true }).click();
      const editor = page.getByRole("region", { name: title, exact: true }),
        rows = editor.locator(".entry-row"),
        first = rows.first();
      const search = first.getByRole("combobox", { name: "商品", exact: true });
      const qtyName = purchase ? "采购数量" : "销售数量",
        qty = first.getByLabel(qtyName, { exact: true }),
        price = first.getByLabel("含税单价（元）", { exact: true });
      const unit = first.getByRole("combobox", {
        name: "交易单位 1",
        exact: true,
      });
      const popup = page.locator(".entry-product-popup");

      // Keyboard-wedge scanners supply characters followed by Enter.
      await search.pressSequentially(boxCode);
      await search.press("Enter");
      await expect(qty).toBeFocused();
      await expect(unit).toBeEnabled();
      await expect(unit).toHaveValue(pack.id);
      await qty.fill("2");
      await price.fill("10");
      await search.fill(baseCode);
      await expect(popup).toContainText("基本单位条码");
      await expect(popup).toContainText("按 个 录入");
      await search.press("Enter");
      await expect(unit).toHaveValue("");
      await expect(qty).toHaveValue("2");
      await expect(price).toHaveValue("10");
      await search.fill(boxCode);
      await expect(popup).toContainText("1 箱 ＝ 10 个");
      await page.screenshot({
        path: `../../outputs/mdm-basic-fields/${purchase ? "采购" : "销售"}包装条码识别.png`,
        fullPage: true,
      });
      await search.press("Enter");
      await expect(unit).toBeEnabled();
      await expect(unit).toHaveValue(pack.id);
      await expect(
        editor.getByLabel("当前行信息", { exact: true }),
      ).toContainText("原数量和单价已保留");
      // A newly approved packaging unit is available without reopening the
      // editor; both the unit selector and reference hint must refresh.
      const fresh = await command(context, owner, `${path}/packaging`, {
        expected_sku_version: (await read(context, `${path}/packaging`))
          .sku_version,
        unit_name: "件",
        numerator: "30",
        denominator: "1",
        reason: "验证页面打开后新增包装",
      });
      await command(reviewer, checker, `${path}/packaging/${fresh.id}/review`, {
        expected_version: fresh.version,
        decision: "APPROVE",
        evidence_ref: "DEMO-ONLY",
        reason: "独立核对新包装",
      });
      await command(context, owner, `${path}/codes`, {
        scheme: "BARCODE",
        external_code: `00-${run}-FRESH`,
        packaging_id: fresh.id,
        evidence_ref: "DEMO-ONLY",
        reason: "验证新包装条码",
      });
      await search.fill(`00-${run}-FRESH`);
      await search.press("Enter");
      await expect(unit).toBeEnabled();
      await expect(unit).toHaveValue(fresh.id);
      await expect(
        editor.getByLabel("当前行单位与参考价格", { exact: true }),
      ).toContainText("1 件 ＝ 30 个");
      await expect(price).toHaveValue("10");
      await search.fill(boxCode);
      await search.press("Enter");
      await expect(unit).toHaveValue(pack.id);
      await editor
        .getByRole("button", { name: "添加商品行", exact: true })
        .click();
      const second = rows.nth(1),
        secondSearch = second.getByRole("combobox", {
          name: "商品",
          exact: true,
        });
      await secondSearch.fill(baseCode);
      await secondSearch.press("Enter");
      await expect(second.getByLabel(qtyName, { exact: true })).toBeFocused();
      await second.getByLabel(qtyName, { exact: true }).fill("3");
      await second.getByLabel("含税单价（元）", { exact: true }).fill("1.2");
      await expect(rows).toHaveCount(2);
      await expect(unit).toHaveValue(pack.id);

      for (const invalid of [disabledCode, oldCode, `UNKNOWN-${run}`]) {
        await secondSearch.fill(invalid);
        if (invalid === oldCode) {
          await expect(popup.getByRole("option")).toHaveAttribute(
            "aria-disabled",
            "true",
          );
          await expect(popup).toContainText("对应包装已不可用");
        } else await expect(popup).toContainText("没有匹配的可选商品");
        await secondSearch.press("Enter");
        await expect(secondSearch).toBeFocused();
        await expect(secondSearch).toHaveValue(invalid);
        await expect(
          second.getByRole("combobox", { name: "交易单位 2", exact: true }),
        ).toHaveValue("");
        await secondSearch.press("Escape");
        await expect(second.getByLabel(qtyName, { exact: true })).toHaveValue(
          "3",
        );
        await expect(
          second.getByLabel("含税单价（元）", { exact: true }),
        ).toHaveValue("1.2");
      }
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
          .fill("虚构条码验收地址");
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
      expect(sent.lines[0]).toMatchObject({
        packaging_id: pack.id,
        qty: "2",
        unit_price: "10",
      });
      expect(sent.lines[1].packaging_id).toBeUndefined();
      const result = (await response.json()).data;
      const detail = await read(context, `${endpoint}/${result.id}`);
      expect(detail.lines[0].qty).toBe("20.000000");
      expect(detail.lines[0].package_snapshot.packaging_id).toBe(pack.id);
      await expect(editor).not.toBeVisible();
      const editTitle = purchase ? "修改采购单" : "修改销售单";
      await page.getByRole("button", { name: editTitle, exact: true }).click();
      const edit = page.getByRole("region", { name: editTitle, exact: true });
      await expect(
        edit.getByRole("combobox", { name: "交易单位 1", exact: true }),
      ).toHaveValue(pack.id);
      await expect(
        edit.getByLabel(qtyName, { exact: true }).first(),
      ).toHaveValue("2.000000");
      expect(faults).toEqual([]);
    } finally {
      const latest = await read(context, templatePath);
      await command(context, owner, templatePath, {
        expected_version: latest.version,
        template: original.template || template,
      });
      await reviewer.close();
    }
  });
}
