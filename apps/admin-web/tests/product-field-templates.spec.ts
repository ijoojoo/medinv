import { test, expect } from "@playwright/test";
import { wholesale, read, command, nav } from "./business-helpers";
import {
  writableRules,
  type ProductFieldPolicy,
} from "../src/productFieldRules";

test("field templates keep aliases, card ownership and category names consistent", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(120000);
  const owner = await wholesale(context, "demo.owner");
  const path = "/api/v1/catalog/field-rules/DEVICE";
  const original = (await read(context, path)) as ProductFieldPolicy;
  const reviewer = await browser.newContext();
  const quality = await wholesale(reviewer, "demo.quality");
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    await page.goto("/");
    await nav(page, "商品字段设置");
    await expect(page.getByText("器械基础模板", { exact: true })).toBeVisible();
    await page
      .getByLabel("所属卡片", { exact: true })
      .selectOption("operations");
    await expect(
      page.getByRole("button", { name: "配置 检索别名 / 助记码", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "配置 器械型号", exact: true }),
    ).toHaveCount(0);
    await page.getByLabel("所属卡片", { exact: true }).selectOption("");
    for (const [label, alias] of [
      ["器械型号", "业务型号"],
      ["检索别名 / 助记码", "快捷检索词"],
    ]) {
      await page.getByLabel("查找字段", { exact: true }).fill(label);
      await page
        .getByRole("button", { name: `配置 ${label}`, exact: true })
        .click();
      const editor = page.getByRole("dialog", {
        name: `配置规则 · ${label}`,
        exact: true,
      });
      await expect(editor).toContainText(
        label === "器械型号" ? "修改后重新审核" : "直接保存 · 留痕",
      );
      await editor.getByLabel("企业显示名称", { exact: true }).fill(alias);
      if (label === "检索别名 / 助记码")
        await editor
          .getByRole("checkbox", { name: "第二类", exact: true })
          .check();
      await editor
        .getByRole("button", { name: "应用到当前设置", exact: true })
        .click();
    }
    await page.getByLabel("查找字段", { exact: true }).fill("registrant_name");
    await page
      .getByRole("button", { name: "配置 注册人 / 备案人", exact: true })
      .click();
    const locked = page.getByRole("dialog", {
      name: "配置规则 · 注册人 / 备案人",
      exact: true,
    });
    await expect(
      locked.getByLabel("企业显示名称", { exact: true }),
    ).toHaveCount(0);
    await expect(locked).toContainText("分类联动控制");
    await locked.getByRole("button", { name: "取消", exact: true }).click();
    await page.getByLabel("查找字段", { exact: true }).fill("");
    await page
      .getByLabel("修改说明", { exact: true })
      .fill("验收字段模板与企业显示名称");
    await page
      .getByRole("button", { name: "保存字段设置", exact: true })
      .click();
    await expect(page.getByText("尚未保存", { exact: true })).toHaveCount(0);
    const stored = (await read(context, path)) as ProductFieldPolicy;
    expect(
      stored.fields.find((f) => f.key === "device_model")?.display_name,
    ).toBe("业务型号");
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
    await page.screenshot({
      path: "../../artifacts/mdm-field-templates.png",
      fullPage: true,
    });

    await nav(page, "商品档案");
    await page.getByRole("button", { name: "新建商品", exact: true }).click();
    const entry = page.getByRole("region", {
      name: "新建商品档案",
      exact: true,
    });
    await entry.getByLabel("器械管理类别", { exact: true }).selectOption("I");
    await expect(entry.getByLabel("备案人", { exact: true })).toBeVisible();
    await entry.getByLabel("器械管理类别", { exact: true }).selectOption("II");
    await expect(entry.getByLabel("注册人", { exact: true })).toBeVisible();
    await expect(entry.getByLabel("备案人", { exact: true })).toHaveCount(0);
    const suffix = Date.now().toString(36),
      year = new Date().getFullYear();
    for (const [label, value] of [
      ["商品编码", `TM-${suffix}`],
      ["名称", `模板验收-${suffix}`],
      ["生产企业", "模板验收虚拟企业"],
      ["规格型号", "单件"],
      ["基本单位", "个"],
      ["资质生效日期", `${year}-01-01`],
      ["资质截止日期", `${year + 2}-12-31`],
      ["注册证号", "DEMO-TEMPLATE"],
      ["业务型号", "T-001"],
      ["快捷检索词", `MDM-${suffix}`],
    ])
      await entry.getByLabel(label, { exact: true }).fill(value);
    await entry
      .getByRole("button", { name: "保存待审核资料", exact: true })
      .click();
    await expect(entry).not.toBeVisible();
    await page.getByLabel("查询资料", { exact: true }).fill(`TM-${suffix}`);
    await page.getByLabel("查询资料", { exact: true }).press("Enter");
    const found = await read(
      context,
      `/api/v1/catalog/products?search=TM-${suffix}`,
    );
    const id = found.items[0].id;
    await command(reviewer, quality, `/api/v1/catalog/products/${id}/review`, {
      expected_version: "1",
      decision: "APPROVE",
      evidence_ref: "TEST-TEMPLATE",
      reason: "模板测试审核",
    });
    await page
      .getByRole("row")
      .filter({ hasText: `模板验收-${suffix}` })
      .getByRole("button", { name: "资料详情", exact: true })
      .click();
    const detail = page.getByRole("region", { name: "资料详情", exact: true });
    await expect(
      detail.getByRole("tabpanel", { name: "基本信息", exact: true }),
    ).toContainText("业务型号");
    await expect(detail).toContainText("第二类医疗器械");
    await detail.getByRole("tab", { name: "经营信息", exact: true }).click();
    await expect(
      detail.getByLabel("快捷检索词", { exact: true }),
    ).toHaveAttribute("required", "");
    await detail
      .getByLabel("快捷检索词", { exact: true })
      .fill(`FAST-${suffix}`);
    await detail.getByLabel("变更说明", { exact: true }).fill("调整快速检索词");
    await detail
      .getByRole("button", { name: "保存经营信息", exact: true })
      .click();
    await expect(
      detail.getByText("已与当前档案同步", { exact: true }),
    ).toBeVisible();
    const updated = await read(context, `/api/v1/catalog/products/${id}`);
    expect(updated.record.approval_state).toBe("APPROVED");
    expect(updated.record.search_alias).toBe(`FAST-${suffix}`);
    await detail.getByRole("tab", { name: "质量管控", exact: true }).click();
    await detail.getByRole("button", { name: "修订资料", exact: true }).click();
    const revision = page.getByRole("region", {
      name: "修订资料",
      exact: true,
    });
    await expect(revision.getByLabel("业务型号", { exact: true })).toHaveValue(
      "T-001",
    );
    await expect(
      revision.getByLabel("快捷检索词", { exact: true }),
    ).toHaveCount(0);
    expect(errors).toEqual([]);
  } finally {
    const current = (await read(context, path)) as ProductFieldPolicy;
    if (
      JSON.stringify(writableRules(current.fields)) !==
      JSON.stringify(writableRules(original.fields))
    )
      await command(context, owner, path, {
        expected_version: current.version,
        fields: writableRules(original.fields),
        reason: "恢复验收前字段模板配置",
      });
    await reviewer.close();
  }
});
