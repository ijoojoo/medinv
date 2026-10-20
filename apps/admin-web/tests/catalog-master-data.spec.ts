import { test, expect } from "@playwright/test";
import { wholesale, read, nav } from "./business-helpers";

test("party full-page profile, preserved editing, filtering, external codes and revision audit", async ({
  page,
  context,
}) => {
  test.setTimeout(90000);
  const owner = await wholesale(context, "demo.owner");
  const suffix = Date.now().toString(36),
    name = "基础资料验收-" + suffix,
    year = new Date().getFullYear();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await nav(page, "往来单位");
  let list = page
    .getByRole("region", { name: "基础资料列表" })
    .filter({ visible: true });
  await list.getByLabel("业务身份").selectOption("CUSTOMER");
  await list.getByRole("button", { name: "新建往来单位", exact: true }).click();
  let form = page.getByRole("region", { name: "新建往来单位", exact: true });
  await expect(
    form.getByRole("combobox", { name: "单位类型", exact: true }),
  ).toHaveValue("CUSTOMER");
  await form
    .getByRole("combobox", { name: "单位类型", exact: true })
    .selectOption("BOTH");
  for (const [label, value] of [
    ["编码", "MD-" + suffix],
    ["名称", name],
    ["统一社会信用代码", "TEST-" + suffix],
    ["经营许可 / 备案编号", "DEMO-QUAL"],
    ["资质生效日期", `${year}-01-01`],
    ["资质截止日期", `${year + 2}-12-31`],
    ["单位简称", "简称-" + suffix],
    ["检索别名 / 助记码", "ALIAS-" + suffix],
    ["联系人", "测试联系人"],
    ["联系电话", "010-12345678 转 9"],
    ["注册地址", "测试注册地址"],
    ["经营地址", "测试经营地址"],
    ["默认收发货地址", "测试收发货地址"],
    ["资质档案编号", "FILE-" + suffix],
    ["授权委托档案编号", "AUTH-" + suffix],
    ["质量协议编号", "QA-" + suffix],
    ["许可 / 备案经营范围原文", "按核验原件登记，仅供软件测试"],
  ]) {
    const qualityFields = [
      "经营许可 / 备案编号",
      "资质生效日期",
      "资质截止日期",
      "经营地址",
      "资质档案编号",
      "授权委托档案编号",
      "质量协议编号",
      "许可 / 备案经营范围原文",
    ];
    const contactFields = ["联系人", "联系电话", "默认收发货地址"];
    await form
      .getByRole("tab", {
        name: qualityFields.includes(label)
          ? "质量管控"
          : contactFields.includes(label)
            ? "联系信息"
            : "基本信息",
        exact: true,
      })
      .click();
    await form.getByLabel(label, { exact: true }).fill(value);
  }
  await form.getByRole("tab", { name: "基本信息", exact: true }).click();
  await form.getByLabel("机构性质").selectOption("WHOLESALER");
  await nav(page, "商品档案");
  await nav(page, "往来单位");
  await expect(form.getByLabel("名称", { exact: true })).toHaveValue(name);
  await page.getByRole("button", { name: "退出", exact: true }).click();
  const leaving = page.getByRole("dialog", {
    name: "保留未保存的录入",
    exact: true,
  });
  await expect(leaving).toBeVisible();
  await leaving.getByRole("button", { name: "继续编辑", exact: true }).click();
  await expect(leaving).not.toBeVisible();
  await expect(form.getByLabel("名称", { exact: true })).toHaveValue(name);
  await form.getByRole("button", { name: "取消", exact: true }).click();
  await page.getByRole("button", { name: "继续编辑", exact: true }).click();
  await expect(
    page.getByRole("dialog", { name: "离开资料编辑", exact: true }),
  ).not.toBeVisible();
  await expect(form).toBeVisible();
  const box = await form.boundingBox();
  expect(box!.width).toBeGreaterThan(1100);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: "../../artifacts/catalog-master-edit.png",
    fullPage: true,
    animations: "disabled",
  });
  await page.setViewportSize({ width: 390, height: 760 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "../../artifacts/catalog-master-edit-mobile.png",
    fullPage: true,
    animations: "disabled",
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await form
    .getByRole("button", { name: "保存待审核资料", exact: true })
    .click();
  await expect(form).not.toBeVisible();
  list = page
    .getByRole("region", { name: "基础资料列表" })
    .filter({ visible: true });
  await list.getByLabel("查询资料", { exact: true }).fill("alias-" + suffix);
  await list.getByRole("button", { name: "查询", exact: true }).click();
  const row = list.getByRole("row").filter({ hasText: name });
  await expect(row).toHaveCount(1);
  await expect(row).toContainText("测试联系人");
  await row.getByRole("button", { name: "资料详情" }).click();
  let detail = page.getByRole("region", { name: "资料详情", exact: true });
  await detail.getByRole("tab", { name: "质量管控", exact: true }).click();
  await expect(detail).toContainText("FILE-" + suffix);
  await detail.getByRole("tab", { name: "第三方对接", exact: true }).click();
  const codes = detail.getByRole("region", { name: "对接编码", exact: true });
  await codes.getByRole("button", { name: "新增编码" }).click();
  const codeForm = codes.getByRole("form", { name: "新增对接编码" });
  await codeForm.getByLabel("编码类型").selectOption("CUSTOM");
  await codeForm.getByLabel("店铺 / 账号 / 系统标识").fill("TEST-ERP");
  await codeForm.getByLabel("外部编码", { exact: true }).fill("000" + suffix);
  await codeForm.getByLabel("编码来源凭据").fill("TEST-MAPPING");
  await codeForm.getByLabel("登记原因").fill("测试登记外部客户编码");
  await expect(
    detail.getByRole("button", { name: "← 返回资料列表" }),
  ).toBeDisabled();
  await codeForm.getByRole("button", { name: "保存编码" }).click();
  await expect(codeForm).not.toBeVisible();
  const mapping = codes.getByRole("row").filter({ hasText: "000" + suffix });
  await expect(mapping).toContainText("TEST-ERP");
  await mapping.getByRole("button", { name: "停用", exact: true }).click();
  await codes.getByLabel("状态调整原因").fill("软件测试停用");
  await codes.getByRole("button", { name: "确认调整编码" }).click();
  await expect(
    mapping.getByRole("button", { name: "启用", exact: true }),
  ).toBeVisible();
  await mapping.getByRole("button", { name: "启用", exact: true }).click();
  await codes.getByLabel("状态调整原因").fill("软件测试重新核对");
  await codes.getByRole("button", { name: "确认调整编码" }).click();
  await expect(
    mapping.getByRole("button", { name: "停用", exact: true }),
  ).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await expect(page.locator(".el-message")).toHaveCount(0, { timeout: 8000 });
  await page.screenshot({
    path: "../../artifacts/catalog-master-party.png",
    fullPage: true,
    animations: "disabled",
  });
  await detail.getByRole("button", { name: "← 返回资料列表" }).click();
  await expect(list.getByLabel("业务身份")).toHaveValue("CUSTOMER");
  await expect(list.getByLabel("查询资料", { exact: true })).toHaveValue(
    "alias-" + suffix,
  );
  await list.getByLabel("查询资料", { exact: true }).fill("000" + suffix);
  await list.getByRole("button", { name: "查询", exact: true }).click();
  await expect(row).toHaveCount(1);
  await row.getByRole("button", { name: "资料详情" }).click();
  await detail.getByRole("tab", { name: "经营信息", exact: true }).click();
  form = page.getByRole("region", { name: "经营信息卡片", exact: true });
  await form.getByLabel("联系人", { exact: true }).fill("新联系人");
  await form.getByLabel("变更说明").fill("核对联系人");
  await form.getByRole("button", { name: "保存经营信息" }).click();
  await expect(form).toContainText("已与当前档案同步");
  detail = page.getByRole("region", { name: "资料详情", exact: true });
  await detail.getByRole("tab", { name: "变更记录", exact: true }).click();
  await expect(detail.getByTestId("catalog-history")).toContainText("新联系人");
  await detail.getByRole("tab", { name: "基本信息", exact: true }).click();
  await detail.getByRole("tab", { name: "质量管控", exact: true }).click();
  await expect(detail.getByRole("button", { name: "提交审核" })).toBeDisabled();
  expect(errors).toEqual([]);
  const found = await read(
    context,
    `/api/v1/catalog/parties?entity_id=${owner.entities[0].id}&search=000${suffix}`,
  );
  expect(found.items[0].contact_name).toBe("新联系人");
});

test("product code mapping validates category, preserves leading zeros and rejects duplicates through UI", async ({
  page,
  context,
}) => {
  await wholesale(context, "demo.owner");
  await page.goto("/");
  await nav(page, "商品档案");
  const list = page.getByRole("region", { name: "基础资料列表" });
  await list
    .getByRole("combobox", { name: "商品类别", exact: true })
    .selectOption("DEVICE");
  await list
    .getByRole("button", { name: "资料详情", exact: true })
    .first()
    .click();
  const detail = page.getByRole("region", { name: "资料详情", exact: true });
  await detail.getByRole("tab", { name: "基本信息", exact: true }).click();
  const codes = detail
    .getByRole("region", { name: "对接编码", exact: true })
    .first();
  const value = "000" + Date.now().toString();
  for (let i = 0; i < 2; i++) {
    await codes.getByRole("button", { name: "新增编码" }).click();
    const form = codes.getByRole("form", { name: "新增对接编码" });
    await expect(
      form.getByLabel("编码类型").locator('option[value="NHSA_DRUG"]'),
    ).toHaveCount(0);
    await form.getByLabel("外部编码", { exact: true }).fill(value);
    await form.getByLabel("编码来源凭据").fill("TEST-LABEL");
    await form.getByLabel("登记原因").fill("测试条码");
    await form.getByRole("button", { name: "保存编码" }).click();
    if (i === 0) await expect(form).not.toBeVisible();
    else {
      await expect(
        page.getByRole("alert").filter({ hasText: "已有启用映射" }),
      ).toBeVisible();
      await expect(form).toBeVisible();
      await form.getByRole("button", { name: "取消新增编码" }).click();
    }
  }
  await expect(codes.getByRole("row").filter({ hasText: value })).toHaveCount(
    1,
  );
  await page.evaluate(() => window.scrollTo(0, 0));
  await expect(page.locator(".el-message")).toHaveCount(0, { timeout: 8000 });
  await page.screenshot({
    path: "../../artifacts/catalog-master-product.png",
    fullPage: true,
    animations: "disabled",
  });
});
