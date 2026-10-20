import { read, command, entryTemplatePath } from "./entry-template-helpers";
import { test, expect } from "@playwright/test";
import {
  entryQuantity,
  lineIssue,
  newEntryLine,
  defaultEntryColumns,
  type EntryProduct,
} from "../src/documentEntry";
import { parseEntryFile, validateEntryFileRow } from "../src/entryFiles";
import {
  wholesale,
  stockedCustomer,
  nav,
  chooseEntrySku,
  chooseParty,
} from "./business-helpers";

const pack = (numerator: string, denominator = "1") => ({
  id: "pack",
  unit_name: "箱",
  numerator,
  denominator,
});
const product: EntryProduct = {
  id: "sku",
  code: "SKU001",
  name: "虚构验收商品",
  base_unit: "个",
  qty_scale: 0,
  eligible: true,
  specification: "",
  manufacturer_name: "",
};

test("quantity conversion accepts exact fractional packs and retains all six decimals", () => {
  expect(entryQuantity("0.01", 0, "个", pack("100"))).toEqual({
    micros: 1000000n,
  });
  expect(entryQuantity("0.5", 0, "个", pack("100"))).toEqual({
    micros: 50000000n,
  });
  expect(entryQuantity("3", 0, "个", pack("1", "3"))).toEqual({
    micros: 1000000n,
  });
  expect(entryQuantity("0.2", 1, "克", pack("3", "2"))).toEqual({
    micros: 300000n,
  });
  expect(entryQuantity("0.000001", 6, "克")).toEqual({ micros: 1n });
  expect(entryQuantity("99999999999999.999999", 6)).toEqual({
    micros: 99999999999999999999n,
  });
  expect(
    entryQuantity("90071992547409.123456", 6, "克", pack("1", "2")),
  ).toEqual({ micros: 45035996273704561728n });
});

test("quantity conversion rejects indivisible units, lost fractions, overflow and malformed metadata without rounding", () => {
  for (const [value, scale, p] of [
    ["0.005", 0, pack("100")],
    ["1", 0, pack("1", "3")],
    ["0.000001", 6, pack("1", "3")],
    ["0.2", 0, pack("3", "2")],
  ] as const)
    expect(entryQuantity(value, scale, "个", p).message).toContain(
      "不会自动四舍五入",
    );
  expect(entryQuantity("99999999999999", 0, "个", pack("2")).message).toContain(
    "超出上限",
  );
  for (const value of [
    "0",
    "-1",
    "1e2",
    "1,000",
    "1.0000001",
    "100000000000000",
  ])
    expect(entryQuantity(value, 6).micros).toBeUndefined();
  for (const p of [pack("0"), pack("1", "0"), pack("1e2"), pack("1", "0.5")])
    expect(entryQuantity("1", 0, "个", p).message).toContain("换算关系异常");
  expect(entryQuantity("1", 7).message).toContain("精度信息异常");
});

test("grid and import validation share base precision and overflow rules, without altering the entered line", () => {
  for (const qty of ["0.005", "99999999999999"]) {
    const packages = { base_unit: "个", items: [pack("100")] };
    const row = {
      ...newEntryLine(),
      sku_id: product.id,
      product,
      packaging_id: "pack",
      qty,
      unit_price: "1",
    };
    const before = { ...row };
    const issue = lineIssue(row, packages);
    expect(issue?.column).toBe("qty");
    const imported = parseEntryFile(
      `SKU编码,数量,含税单价,交易单位\nSKU001,${qty},1,箱`,
    )[0]!;
    validateEntryFileRow(imported, product, packages);
    expect(imported.errors).toEqual([issue!.message]);
    expect(row).toEqual(before);
  }
  expect(
    lineIssue({
      ...newEntryLine(),
      sku_id: product.id,
      product,
      qty: "0.5",
      unit_price: "1",
    })?.column,
  ).toBe("qty");
  expect(
    lineIssue(
      {
        ...newEntryLine(),
        sku_id: product.id,
        product,
        packaging_id: "missing",
        qty: "1",
        unit_price: "1",
      },
      { base_unit: "个", items: [] },
    )?.column,
  ).toBe("pack");
});

for (const purchase of [true, false]) {
  test(`${purchase ? "purchase" : "sales"} validates multiple package rows, locates the first error and checks hidden units after draft restore`, async ({
    page,
    context,
    browser,
  }) => {
    test.setTimeout(150000);
    const owner = await wholesale(context, "demo.owner");
    const reviewer = await browser.newContext();
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
      const path = `/api/v1/catalog/skus/${f.product.sku_id}`;
      const ids: string[] = [];
      for (const [unit_name, numerator, denominator] of [
        ["箱", "100", "1"],
        ["袋", "1", "3"],
      ]) {
        const info = await read(context, `${path}/packaging`);
        const created = await command(context, owner, `${path}/packaging`, {
          expected_sku_version: info.sku_version,
          unit_name,
          numerator,
          denominator,
          reason: "虚构数量精度验收",
        });
        await command(
          reviewer,
          checker,
          `${path}/packaging/${created.id}/review`,
          {
            expected_version: "1",
            decision: "APPROVE",
            evidence_ref: "DEMO-ONLY",
            reason: "独立核对数量换算",
          },
        );
        ids.push(created.id);
      }
      await page.goto("/");
      await nav(page, purchase ? "采购入库" : "批发销售");
      const title = purchase ? "新建采购单" : "新建销售单";
      await page
        .getByRole("button", {
          name: title === "新建采购单" ? "新建采购订单" : title,
          exact: true,
        })
        .click();
      const editor = page.getByRole("region", { name: title, exact: true });
      const rows = editor.locator(".entry-row");
      const qtyName = purchase ? "采购数量" : "销售数量";
      const firstQty = rows.first().getByLabel(qtyName, { exact: true });
      const firstPrice = rows
        .first()
        .getByLabel("含税单价（元）", { exact: true });
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
      await chooseEntrySku(rows.first(), f.product.sku_id);
      await rows
        .first()
        .getByRole("combobox", { name: "交易单位 1", exact: true })
        .selectOption(ids[0]!);
      await firstQty.fill("0.005");
      await firstQty.press("Enter");
      await expect(firstQty).toBeFocused();
      await expect(firstQty).toHaveAttribute("aria-invalid", "true");
      await expect(editor.getByRole("alert")).toContainText("第 1 行");
      await expect(editor.getByRole("alert")).toContainText("必须为整数");
      await expect(firstQty).toHaveValue("0.005");
      await firstQty.fill("0.01");
      await firstQty.press("Tab");
      await expect(firstPrice).toBeFocused();
      await expect(editor.getByRole("alert")).toHaveCount(0);
      await firstPrice.fill("10");
      await editor
        .getByRole("button", { name: "复制当前商品行", exact: true })
        .click();
      await expect(rows).toHaveCount(2);
      const secondQty = rows.nth(1).getByLabel(qtyName, { exact: true });
      await rows
        .nth(1)
        .getByRole("combobox", { name: "交易单位 2", exact: true })
        .selectOption(ids[1]!);
      await secondQty.fill("1");
      // Hiding the unit column must not bypass package validation on restore.
      await editor.getByRole("button", { name: "列设置", exact: true }).click();
      const settings = page.getByRole("dialog", {
        name: "配置单据明细列",
        exact: true,
      });
      await settings
        .getByRole("checkbox", { name: "交易单位", exact: true })
        .uncheck();
      await settings
        .getByRole("button", {
          name: "保存配置",
          exact: true,
        })
        .click();
      await expect(settings).not.toBeVisible();
      await editor
        .getByRole("button", { name: "保存草稿", exact: true })
        .click();
      await expect(editor.locator(".editor-actions")).toContainText(
        "草稿已保存",
      );
      const endpoint = purchase
        ? "/api/v1/purchase-orders"
        : "/api/v1/sales-orders";
      let submitted = 0;
      page.on("request", (r) => {
        if (r.url().endsWith(endpoint) && r.method() === "POST") submitted++;
      });
      const save = editor.getByRole("button", {
        name: purchase ? "保存采购单" : "保存销售单",
        exact: true,
      });
      if (purchase)
        await page.route(`**${path}/packaging?*`, (route) =>
          route.fulfill({
            status: 503,
            contentType: "application/json",
            body: JSON.stringify({ error: { message: "测试包装读取失败" } }),
          }),
        );
      await page.reload();
      await expect(rows).toHaveCount(2);
      await expect(
        editor.getByRole("combobox", { name: "交易单位 1", exact: true }),
      ).toHaveCount(0);
      if (purchase) {
        const retry = editor.getByRole("button", {
          name: "重新校验包装",
          exact: true,
        });
        await expect(retry).toBeVisible();
        await save.click();
        await expect(firstQty).toBeFocused();
        expect(submitted).toBe(0);
        await page.unroute(`**${path}/packaging?*`);
        await retry.click();
        await expect(retry).toHaveCount(0);
      }
      // Wait for hydration and precision feedback before attempting save.
      await expect(secondQty).toHaveAttribute("aria-invalid", "true");
      await save.click();
      await expect(secondQty).toBeFocused();
      await expect(secondQty).toHaveValue("1");
      await expect(editor.getByRole("alert")).toContainText("第 2 行");
      expect(submitted).toBe(0);
      await page.screenshot({
        path: `../../outputs/mdm-basic-fields/${purchase ? "采购" : "销售"}包装数量校验.png`,
        fullPage: true,
      });
      await secondQty.fill("3");
      await secondQty.press("Enter");
      await expect(
        rows.nth(1).getByLabel("含税单价（元）", { exact: true }),
      ).toBeFocused();
      await expect(editor.getByRole("alert")).toHaveCount(0);
      const saving = page.waitForResponse(
        (r) => r.url().endsWith(endpoint) && r.request().method() === "POST",
      );
      await save.click();
      const response = await saving;
      expect(response.status(), await response.text()).toBe(200);
      expect(
        response
          .request()
          .postDataJSON()
          .lines.map((r: any) => [r.packaging_id, r.qty, r.unit_price]),
      ).toEqual([
        [ids[0], "0.01", "10"],
        [ids[1], "3", "10"],
      ]);
      expect(submitted).toBe(1);
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
