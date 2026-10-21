import { test, expect, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { mkdir } from "node:fs/promises";
import { wholesale, command, read, nav } from "./business-helpers";

async function openParty(page: Page, id: string, code: string) {
  await page.goto("/");
  await nav(page, "往来单位");
  const list = page.getByRole("region", { name: "基础资料列表" });
  await list.getByLabel("查询资料").fill(code);
  await list.getByLabel("查询资料").press("Enter");
  await list.locator(`button[data-catalog-record="${id}"]`).click();
  return page.getByRole("region", { name: "资料详情", exact: true });
}

test("scope tab, certificate linkage, one quality review and withdrawal preserve live data", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(180000);
  const owner = await wholesale(context, "demo.owner");
  const suffix = randomUUID().slice(0, 8),
    code = `UNIFIED-${suffix}`;
  const year = new Date().getFullYear();
  const party = await command(context, owner, "/api/v1/catalog/parties", {
    entity_id: owner.entities[0].id,
    code,
    name: `统一审核软件验收-${suffix}`,
    credit_code: code,
    kind: "SUPPLIER",
    categories: ["DEVICE"],
    organization: {},
  });
  const root = `/api/v1/catalog/parties/${party.id}`;
  const cert = await command(context, owner, `${root}/certificates`, {
    certificate_type: "BUSINESS_LICENSE",
    number: `TEST-BL-${suffix}`,
    holder_name: "软件验收单位",
    issuer: "软件验收机关",
    business_uses: ["SUPPLY"],
    validity_mode: "LONG_TERM",
    evidence_ref: "TEST-EVIDENCE",
    reason: "软件验收",
  });
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const detail = await openParty(page, party.id, code);
  await detail.getByRole("tab", { name: /经营范围与准入/ }).click();
  await page.locator("[data-core-action=revise]").click();
  const editing = page.getByRole("dialog", { name: "修订资料", exact: true });
  const scopes = editing.getByRole("region", {
    name: "经营范围与准入",
    exact: true,
  });
  await scopes.getByRole("button", { name: "新增经营范围" }).click();
  const dialog = page.getByRole("dialog", { name: "维护经营范围" });
  await dialog
    .getByLabel("范围名称", { exact: true })
    .fill("第一类器械 06 软件验收");
  await dialog.getByLabel("允许分类编码", { exact: true }).fill("06，08-01");
  await dialog.getByLabel("排除分类编码", { exact: true }).fill("06-02");
  await dialog.locator(".el-select").last().click();
  await page.getByRole("option", { name: new RegExp(cert.number) }).click();
  await dialog.getByLabel("范围名称", { exact: true }).click();
  await dialog.getByRole("button", { name: "应用到范围草稿" }).click();
  await scopes.getByLabel("范围变更说明").fill("依据软件验收资料登记");
  await scopes.getByRole("button", { name: "保存范围变更" }).click();
  await expect(scopes).toContainText("已配置条件满足");
  const pending = await read(context, `${root}/scopes`);
  expect(pending.payload.scopes[0].codes).toEqual(["06", "08-01"]);
  expect(pending.active_payload.scopes).toEqual([]);
  await mkdir("../../outputs/extension-menu/master/qualification", {
    recursive: true,
  });
  await page.screenshot({
    path: "../../outputs/extension-menu/master/qualification/party-scopes.png",
    fullPage: true,
  });

  const reviewer = await browser.newContext();
  try {
    await wholesale(reviewer, "demo.quality");
    const qualityPage = await reviewer.newPage();
    await openParty(qualityPage, party.id, code);
    await qualityPage
      .getByRole("button", { name: "展开扩展功能", exact: true })
      .click();
    await qualityPage.locator("[data-core-action=review]").click();
    const quality = qualityPage.getByRole("dialog", {
      name: "主体审核 / 停用",
      exact: true,
    });
    await expect(quality).toContainText("待审核变更");
    await quality
      .getByLabel("核验凭据编号", { exact: true })
      .fill("TEST-ONE-REVIEW");
    await quality
      .getByLabel("审核说明", { exact: true })
      .fill("统一核对证件和经营范围");
    await quality.getByRole("button", { name: "统一审核主体及变更" }).click();
    await expect(quality.locator(".master-change-summary")).toHaveCount(0);
    expect((await read(context, `${root}/certificates`)).items[0].state).toBe(
      "VERIFIED",
    );
    expect(
      (await read(context, `${root}/scopes`)).active_payload.scopes,
    ).toHaveLength(1);
    await qualityPage.screenshot({
      path: "../../outputs/extension-menu/master/qualification/unified-review.png",
      fullPage: true,
    });
  } finally {
    await reviewer.close();
  }
  await page.reload();
  const fresh = await openParty(page, party.id, code);
  await fresh.getByRole("tab", { name: /经营范围与准入/ }).click();
  await page.locator("[data-core-action=revise]").click();
  await scopes.getByRole("button", { name: "停用", exact: true }).click();
  await scopes.getByLabel("范围变更说明").fill("软件验收撤回操作");
  await scopes.getByRole("button", { name: "保存范围变更" }).click();
  await expect(scopes).toContainText("有待审核变更");
  expect(
    (await read(context, `${root}/scopes`)).active_payload.scopes[0].enabled,
  ).toBe(true);
  await editing
    .getByRole("button", { name: "关闭此对话框", exact: true })
    .click();
  await page.getByRole("button", { name: "展开扩展功能", exact: true }).click();
  await page.locator("[data-core-action=review]").click();
  const reviewTask = page.getByRole("dialog", {
    name: "主体审核 / 停用",
    exact: true,
  });
  await reviewTask.getByRole("button", { name: "查看变更对比" }).click();
  const comparison = page.getByRole("dialog", { name: "主体资料变更对比" });
  await expect(comparison).toBeVisible();
  await expect(comparison.locator("tbody tr")).toHaveCount(1);
  await expect(comparison).toContainText("第一类器械 06 软件验收 · 启用");
  await page.screenshot({
    path: "../../outputs/extension-menu/master/qualification/change-comparison.png",
    animations: "disabled",
  });
  await comparison.getByPlaceholder("撤回变更原因").fill("取消测试变更");
  await comparison.getByRole("button", { name: /撤回/ }).click();
  await expect(fresh.locator(".master-change-summary")).toHaveCount(0);
  await expect(comparison).not.toBeVisible();
  await reviewTask
    .getByRole("button", { name: "关闭此对话框", exact: true })
    .click();
  await page.locator(".data-toolbox [data-tool=history]").click();
  const historyTask = page.getByRole("dialog", {
    name: "变更记录",
    exact: true,
  });
  await expect(historyTask.getByTestId("catalog-history")).toContainText(
    "撤回主体待审核变更",
  );
  await expect(historyTask.getByTestId("catalog-history")).toContainText(
    "主体变更统一审核通过",
  );
  expect(errors).toEqual([]);
});

test("built-in legal rules can be edited and source links remain visible", async ({
  page,
  context,
}) => {
  await wholesale(context, "demo.owner");
  await page.goto("/");
  await nav(page, "企业设置");
  await page.getByRole("button", { name: "质量与准入", exact: true }).click();
  const rules = page.locator(".qualification-settings");
  await expect(rules).toContainText("第三类器械经营许可");
  const row = rules
    .locator("tbody tr")
    .filter({ hasText: "第三类器械经营许可" });
  await expect(row.getByRole("checkbox")).toBeEnabled();
  await row.getByRole("button", { name: "编辑" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel("规则名称", { exact: true })).toBeEditable();
  await dialog
    .getByLabel("规则名称", { exact: true })
    .fill("测试可修改内置规则");
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await mkdir("../../outputs/extension-menu/master/qualification", {
    recursive: true,
  });
  await page.screenshot({
    path: "../../outputs/extension-menu/master/qualification/editable-rules.png",
    fullPage: true,
  });
  for (const width of [3440, 768, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    await expect
      .poll(() =>
        page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      )
      .toBe(true);
  }
});
