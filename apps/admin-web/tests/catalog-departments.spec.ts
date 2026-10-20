import { test, expect } from "@playwright/test";
import { wholesale, read, command, nav } from "./business-helpers";
import { entryCsv } from "../src/entryFiles";
import type { CatalogDictionary } from "../src/catalogDictionaries";

test("single product code, department cards, quality scope and reference prices work together", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(120000);
  const owner = await wholesale(context, "demo.owner");
  const suffix = Date.now().toString(36).toUpperCase(),
    name = "部门卡片验收-" + suffix;
  const codePath = "/api/v1/catalog/code-rules/DEVICE";
  const originalRule = await read(context, codePath);
  const scopePath = "/api/v1/catalog/dictionaries/device_business_scope";
  const originalScope = (await read(context, scopePath)) as CatalogDictionary;
  const scope = "TEST-器械范围-" + suffix;
  const reviewer = await browser.newContext(),
    checker = await wholesale(reviewer, "demo.quality");
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    await command(context, owner, scopePath, {
      expected_version: originalScope.version,
      mode: "STRICT",
      items: [
        ...originalScope.items,
        { value: scope, active: true, order: 9999 },
      ],
      reason: "测试商品范围分类",
    });
    await page.goto("/");
    await nav(page, "商品字段设置");
    await page
      .getByRole("button", { name: "自动编码规则", exact: true })
      .click();
    const coding = page.getByRole("region", {
      name: "商品自动编码规则",
      exact: true,
    });
    await coding.getByLabel("启用自动编码").selectOption("true");
    await coding
      .getByLabel("编码前缀", { exact: true })
      .fill("UI" + suffix.slice(-6));
    await coding.getByLabel("流水位数", { exact: true }).fill("6");
    await coding.getByLabel("编码规则变更说明").fill("验收商品统一自动编码");
    await coding
      .getByRole("button", { name: "保存编码规则", exact: true })
      .click();
    await expect(coding.getByLabel("编码规则变更说明")).toHaveValue("");
    await page
      .getByRole("button", { name: "← 返回字段设置", exact: true })
      .click();
    await nav(page, "商品档案");
    await page.getByRole("button", { name: "新建商品", exact: true }).click();
    const form = page.getByRole("region", {
      name: "新建商品档案",
      exact: true,
    });
    await expect(
      form
        .locator(
          ".catalog-fields input, .catalog-fields select, .catalog-fields textarea",
        )
        .first(),
    ).toHaveAccessibleName("商品类别");
    await expect(form.getByLabel("SKU 编码", { exact: true })).toHaveCount(0);
    await expect(form.getByLabel("商品编码", { exact: true })).toHaveValue("");
    const year = new Date().getFullYear();
    for (const [label, value] of [
      ["名称", name],
      ["生产企业", "软件验收厂家"],
      ["规格型号", "测试规格"],
      ["批准 / 注册 / 备案编号", "TEST-ONLY"],
      ["资质生效日期", `${year}-01-01`],
      ["资质截止日期", `${year + 2}-12-31`],
    ])
      await form.getByLabel(label, { exact: true }).fill(value);
    await form
      .getByLabel("商品经营范围类别（器械）", { exact: true })
      .selectOption(scope);
    await form
      .getByLabel("质量管控备注", { exact: true })
      .fill("按测试档案核验");
    await form
      .getByRole("button", { name: "保存待审核资料", exact: true })
      .click();
    await expect(form).not.toBeVisible();
    const found = (
      await read(
        context,
        `/api/v1/catalog/products?search=${encodeURIComponent(name)}`,
      )
    ).items[0];
    expect(found.code).toMatch(new RegExp("^UI" + suffix.slice(-6)));
    expect(found.code).toBe(found.sku_code);
    await command(
      reviewer,
      checker,
      `/api/v1/catalog/products/${found.id}/review`,
      {
        expected_version: found.version,
        decision: "APPROVE",
        evidence_ref: "TEST-ONLY",
        reason: "独立核验测试资料",
      },
    );
    const list = page
      .getByRole("region", { name: "基础资料列表" })
      .filter({ visible: true });
    await list.getByLabel("查询资料", { exact: true }).fill(name);
    await list.getByRole("button", { name: "查询", exact: true }).click();
    await list
      .getByRole("row")
      .filter({ hasText: name })
      .getByRole("button", { name: "资料详情", exact: true })
      .click();
    const detail = page.getByRole("region", { name: "资料详情", exact: true });
    await expect(detail.getByRole("tab")).toHaveText([
      "基本信息",
      "经营信息",
      "质量管控",
      "价格信息",
      "医保信息",
      "追溯信息",
      "第三方对接",
      "变更记录",
    ]);
    const basic = detail.getByRole("tabpanel", {
      name: "基本信息",
      exact: true,
    });
    await expect(basic.getByTestId("packaging-panel")).toBeVisible();
    await expect(basic.getByRole("region", { name: "对接编码" })).toContainText(
      "商品条码",
    );
    await detail.getByRole("tab", { name: "质量管控", exact: true }).click();
    const quality = detail.getByRole("tabpanel", {
      name: "质量管控",
      exact: true,
    });
    await expect(quality).toContainText(scope);
    await expect(quality).toContainText("按测试档案核验");
    await detail.getByRole("tab", { name: "价格信息", exact: true }).click();
    const prices = detail.getByRole("region", { name: "价格信息卡片" });
    await prices.getByLabel("参考采购价", { exact: true }).fill("1.123456");
    await prices.getByLabel("建议零售价", { exact: true }).fill("2.50");
    await prices.getByLabel("价格变更说明").fill("维护参考含税价格");
    await detail.getByRole("tab", { name: "经营信息", exact: true }).click();
    await detail.getByRole("tab", { name: "价格信息", exact: true }).click();
    await expect(prices.getByLabel("参考采购价", { exact: true })).toHaveValue(
      "1.123456",
    );
    await expect(
      detail.getByRole("button", { name: "← 返回资料列表" }),
    ).toBeDisabled();
    await prices
      .getByRole("button", { name: "保存价格信息", exact: true })
      .click();
    await expect(prices).toContainText("参考价格已同步");
    expect(
      (await read(context, `/api/v1/catalog/products/${found.id}`)).record
        .approval_state,
    ).toBe("APPROVED");
    await page.evaluate(() => window.scrollTo(0, 0));
    await expect(page.locator(".el-message")).toHaveCount(0, { timeout: 8000 });
    await page.screenshot({
      path: "../../artifacts/catalog-department-prices.png",
      fullPage: true,
      animations: "disabled",
    });
    await detail.getByRole("tab", { name: "追溯信息", exact: true }).click();
    const trace = detail.getByRole("tabpanel", {
      name: "追溯信息",
      exact: true,
    });
    await trace.getByRole("button", { name: "新增编码", exact: true }).click();
    await expect(trace.getByLabel("编码类型").locator("option")).toHaveText([
      "UDI 产品标识（DI）",
      "码上放心",
    ]);
    await trace.getByRole("button", { name: "取消新增编码" }).click();
    await detail.getByRole("tab", { name: "第三方对接", exact: true }).click();
    const platforms = detail.getByRole("tabpanel", {
      name: "第三方对接",
      exact: true,
    });
    await platforms
      .getByRole("button", { name: "新增编码", exact: true })
      .click();
    await expect(platforms.getByLabel("编码类型").locator("option")).toHaveText(
      ["美团", "饿了么", "淘宝", "旺店通", "其他系统"],
    );
    await platforms.getByRole("button", { name: "取消新增编码" }).click();
    await detail.getByRole("tab", { name: "质量管控", exact: true }).click();
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: "../../artifacts/catalog-department-quality.png",
      fullPage: true,
      animations: "disabled",
    });
    expect(errors).toEqual([]);
  } finally {
    const current = await read(context, codePath);
    const desired = {
      expected_version: current.version,
      enabled: originalRule.enabled,
      prefix: originalRule.prefix,
      date_format: originalRule.date_format,
      digits: originalRule.digits,
      start_number: current.next_number,
      reason: "恢复原规则，保留已使用流水",
    };
    if (
      ["enabled", "prefix", "date_format", "digits"].some(
        (k) => originalRule[k] !== current[k],
      )
    )
      await command(context, owner, codePath, desired);
    const currentScope = (await read(context, scopePath)) as CatalogDictionary;
    if (currentScope.items.some((i) => i.value === scope && i.active))
      await command(context, owner, scopePath, {
        expected_version: currentScope.version,
        mode: originalScope.mode,
        items: currentScope.items.map(
          (i) =>
            originalScope.items.find((o) => o.value === i.value) || {
              ...i,
              active: false,
            },
        ),
        reason: "停用测试分类，保留测试档案历史值",
      });
    await reviewer.close();
  }
});

test("CSV product import previews duplicates, exports errors and creates unified-code draft records", async ({
  page,
  context,
}) => {
  test.setTimeout(90000);
  await wholesale(context, "demo.owner");
  const suffix = Date.now().toString(36),
    year = new Date().getFullYear();
  const headers = [
    "商品类别",
    "商品编码",
    "名称",
    "生产企业",
    "规格型号",
    "基本单位",
    "批准注册备案编号",
    "资质生效日期",
    "资质截止日期",
  ];
  const row = (name: string, code: string) => [
    "医疗器械",
    code,
    name,
    "导入验收厂家",
    "测试规格",
    "个",
    "TEST-ONLY",
    `${year}-01-01`,
    `${year + 2}-12-31`,
  ];
  await page.goto("/");
  await nav(page, "商品档案");
  await page.getByRole("button", { name: "批量导入商品", exact: true }).click();
  const panel = page.getByRole("region", {
    name: "商品资料批量导入",
    exact: true,
  });
  const upload = panel.getByLabel("选择商品CSV文件");
  const duplicate = entryCsv(headers, [
    row("重复验收-" + suffix, "DUP-" + suffix),
    row("重复验收-" + suffix, "DUP-" + suffix),
  ]);
  await upload.setInputFiles({
    name: "duplicates.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(duplicate),
  });
  await expect(panel).toContainText("文件内商品编码重复");
  await expect(
    panel.getByRole("button", { name: "确认导入为待审核资料" }),
  ).toBeDisabled();
  const download = page.waitForEvent("download");
  await panel.getByRole("button", { name: "下载待修正清单" }).click();
  expect((await download).suggestedFilename()).toContain("待修正");
  const good = entryCsv(headers, [
    row("导入甲-" + suffix, "IMP-A-" + suffix),
    row("导入乙-" + suffix, "000" + suffix),
  ]);
  await upload.setInputFiles({
    name: "products.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(good),
  });
  await expect(
    panel.getByRole("button", { name: "确认导入为待审核资料" }),
  ).toBeEnabled();
  await nav(page, "往来单位");
  await nav(page, "商品档案");
  await expect(panel).toBeVisible();
  await panel.getByRole("button", { name: "确认导入为待审核资料" }).click();
  await expect(panel).toContainText("已导入 2 条商品，等待独立审核");
  const created = (
    await read(context, `/api/v1/catalog/products?search=IMP-A-${suffix}`)
  ).items[0];
  expect(created.code).toBe(created.sku_code);
  expect(created.approval_state).toBe("DRAFT");
  await page.evaluate(() => window.scrollTo(0, 0));
  await expect(page.locator(".el-message")).toHaveCount(0, { timeout: 8000 });
  await page.screenshot({
    path: "../../artifacts/catalog-product-import.png",
    fullPage: true,
    animations: "disabled",
  });
  await upload.setInputFiles({
    name: "again.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(good),
  });
  await expect(panel).toContainText("已存在");
  await expect(
    panel.getByRole("button", { name: "确认导入为待审核资料" }),
  ).toBeDisabled();
  await panel.getByRole("button", { name: "清空导入预览" }).click();
});
