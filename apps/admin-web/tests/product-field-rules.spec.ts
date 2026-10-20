import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { wholesale, read, command, nav, origin } from "./business-helpers";
import { headers } from "./helpers";
import {
  writableRules,
  type ProductFieldPolicy,
} from "../src/productFieldRules";

test("category policies control entry order, validation and class linkage, with server enforcement", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(120000);
  const owner = await wholesale(context, "demo.owner");
  const path = "/api/v1/catalog/field-rules/DEVICE";
  const original = (await read(context, path)) as ProductFieldPolicy;
  const qualityContext = await browser.newContext();
  const quality = await wholesale(qualityContext, "demo.quality");
  let changed = false;
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    await page.goto("/");
    await nav(page, "商品字段设置");
    await expect(
      page.getByText("器械证照联动 · 系统内置", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByLabel("批准 / 注册 / 备案编号 必填", { exact: true }),
    ).toBeDisabled();
    await page.getByLabel("器械管理类别 必填", { exact: true }).check();
    await page.getByLabel("器械型号 排序", { exact: true }).fill("1");
    await page
      .getByRole("button", { name: "配置 器械型号", exact: true })
      .click();
    const ruleDialog = page.getByRole("dialog", {
      name: "配置规则 · 器械型号",
      exact: true,
    });
    await ruleDialog.getByLabel("最少字符", { exact: true }).fill("3");
    await ruleDialog.getByLabel("最多字符", { exact: true }).fill("20");
    await ruleDialog.getByLabel("格式验证").selectOption("ASCII_CODE");
    await ruleDialog
      .getByRole("checkbox", { name: "第三类", exact: true })
      .check();
    await ruleDialog
      .getByRole("button", { name: "应用到当前设置", exact: true })
      .click();
    await page
      .getByLabel("修改说明", { exact: true })
      .fill("浏览器验收：字段顺序、格式与条件必填");
    await page
      .getByRole("button", { name: "保存字段设置", exact: true })
      .click();
    await expect(page.getByText("尚未保存", { exact: true })).toHaveCount(0);
    const stored = (await read(context, path)) as ProductFieldPolicy;
    changed = true;
    expect(stored.version).not.toBe(original.version);
    expect(stored.fields[0].key).toBe("device_model");
    await page.screenshot({
      path: "../../artifacts/product-field-settings.png",
      fullPage: true,
    });
    const denied = await qualityContext.request.post(origin + path, {
      headers: { ...headers(quality), "Idempotency-Key": randomUUID() },
      data: {
        expected_version: stored.version,
        fields: writableRules(stored.fields),
        reason: "越权尝试",
      },
    });
    expect(denied.status()).toBe(403);
    const qualityPage = await qualityContext.newPage();
    await qualityPage.goto("/");
    await expect(
      qualityPage.getByRole("button", { name: /商品字段设置/ }),
    ).toHaveCount(0);
    await nav(page, "商品档案");
    await page.getByRole("button", { name: "新建商品", exact: true }).click();
    const dialog = page.getByRole("region", {
      name: "新建商品档案",
      exact: true,
    });
    await expect(dialog.locator("[data-field]").first()).toHaveAttribute(
      "data-field",
      "device_model",
    );
    await expect(
      dialog.getByLabel("器械管理类别", { exact: true }),
    ).toHaveAttribute("required", "");
    await dialog.getByLabel("器械管理类别", { exact: true }).selectOption("I");
    await expect(
      dialog.getByLabel("产品备案号", { exact: true }),
    ).toHaveAttribute("required", "");
    await expect(
      dialog.getByLabel("器械型号", { exact: true }),
    ).not.toHaveAttribute("required", "");
    await dialog.getByLabel("产品备案号", { exact: true }).fill("TEST-FILING");
    await dialog.getByLabel("器械管理类别", { exact: true }).selectOption("II");
    await expect(dialog.getByLabel("注册证号", { exact: true })).toHaveValue(
      "",
    );
    await expect(
      dialog.getByLabel("注册证号", { exact: true }),
    ).toHaveAttribute("required", "");
    await dialog
      .getByLabel("器械管理类别", { exact: true })
      .selectOption("III");
    await expect(
      dialog.getByLabel("器械型号", { exact: true }),
    ).toHaveAttribute("required", "");
    const suffix = Date.now().toString(36),
      year = new Date().getFullYear();
    const values = [
      ["商品编码", "CF-" + suffix],
      ["名称", "字段配置验收-" + suffix],
      ["生产企业", "软件测试厂家"],
      ["规格型号", "测试规格"],
      ["注册证号", "DEMO-NOT-A-CERTIFICATE"],
      ["基本单位", "个"],
      ["资质生效日期", `${year}-01-01`],
      ["资质截止日期", `${year + 2}-12-31`],
    ];
    for (const [label, value] of values)
      await dialog.getByLabel(label, { exact: true }).fill(value);
    await dialog.getByLabel("器械型号", { exact: true }).fill("含 空格");
    expect(
      await dialog
        .getByLabel("器械型号", { exact: true })
        .evaluate((el: HTMLInputElement) => el.checkValidity()),
    ).toBe(false);
    const payload = {
      code: "API-" + suffix,
      name: "接口校验",
      category: "DEVICE",
      device_class: "III",
      manufacturer_name: "测试",
      specification: "单件",
      approval_number: "TEST",
      sku_code: "API-S-" + suffix,
      base_unit: "个",
      valid_from: `${year}-01-01`,
      valid_until: `${year + 2}-12-31`,
    };
    const rejected = await context.request.post(
      origin + "/api/v1/catalog/products",
      {
        headers: { ...headers(owner), "Idempotency-Key": randomUUID() },
        data: payload,
      },
    );
    expect(rejected.status()).toBe(422);
    expect((await rejected.json()).error.code).toBe("PRODUCT_FIELD_INVALID");
    await dialog.getByLabel("器械型号", { exact: true }).fill("MODEL-03");
    await dialog
      .getByRole("button", { name: "保存待审核资料", exact: true })
      .click();
    await expect(dialog).not.toBeVisible();
    const found = await read(
      context,
      `/api/v1/catalog/products?search=CF-${suffix}`,
    );
    expect(found.items[0].device_model).toBe("MODEL-03");
    expect(errors).toEqual([]);
  } finally {
    const current = (await read(context, path)) as ProductFieldPolicy;
    if (
      changed ||
      JSON.stringify(writableRules(current.fields)) !==
        JSON.stringify(writableRules(original.fields))
    )
      await command(context, owner, path, {
        expected_version: current.version,
        fields: writableRules(original.fields),
        reason: "验收结束恢复原企业设置",
      });
    await qualityContext.close();
  }
});
