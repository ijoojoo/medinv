import { test, expect } from "@playwright/test";
import { wholesale, command, read, nav } from "./business-helpers";

test("party identity reuse, validation across tabs, dual roles and unsaved card close protection", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(120000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext();
  const checker = await wholesale(reviewer, "demo.quality");
  const suffix = Date.now().toString(36);
  const name = `机构关联验收-${suffix}`;
  const code = `ORG-${suffix}`;
  const org = await command(
    context,
    owner,
    "/api/v1/catalog/master/organizations",
    { name, legal_code: code },
  );
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    await page.goto("/");
    await nav(page, "往来单位");
    const list = page
      .getByRole("region", { name: "基础资料列表" })
      .filter({ visible: true });
    await list
      .getByRole("button", { name: "新建往来单位", exact: true })
      .click();
    const entry = page.getByRole("region", {
      name: "新建往来单位",
      exact: true,
    });
    await entry.getByLabel("编码", { exact: true }).fill(`PM-${suffix}`);
    await entry.getByLabel("名称", { exact: true }).fill(name);
    await expect(
      page.locator(".party-candidate").filter({ hasText: name }),
    ).toBeVisible();
    await entry.getByLabel("名称", { exact: true }).press("ArrowDown");
    await entry.getByLabel("名称", { exact: true }).press("Enter");
    await expect(entry.getByLabel("统一社会信用代码")).toHaveValue(code);
    await expect(entry).toContainText(`已匹配：${name}`);
    await entry.getByRole("button", { name: "保存待审核资料" }).click();
    await expect(entry.getByRole("tab", { name: "质量管控" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await expect(
      entry.getByLabel("资质生效日期", { exact: true }),
    ).toBeFocused();
    const year = new Date().getFullYear();
    await entry.getByLabel("经营许可 / 备案编号").fill("TEST-ONLY");
    await entry
      .getByLabel("资质生效日期", { exact: true })
      .fill(`${year}-01-01`);
    await entry
      .getByLabel("资质截止日期", { exact: true })
      .fill(`${year + 2}-12-31`);
    await entry.getByRole("tab", { name: "联系信息" }).click();
    await entry.getByLabel("联系人", { exact: true }).fill("业务联系人");
    await entry.getByRole("tab", { name: "基本信息" }).click();
    await page.screenshot({
      path: "../../outputs/party-mdm/分组建档.png",
      fullPage: true,
    });
    await entry.getByRole("button", { name: "保存待审核资料" }).click();
    await expect(entry).not.toBeVisible();
    const results = await read(
      context,
      `/api/v1/catalog/parties?entity_id=${owner.entities[0].id}&search=${code}`,
    );
    const party = results.items[0];
    expect(results.items).toHaveLength(1);
    expect(party.organization_id).toBe(org.id);
    expect(party.contact_name).toBe("业务联系人");
    const review = (version: string) =>
      command(reviewer, checker, `/api/v1/catalog/parties/${party.id}/review`, {
        expected_version: version,
        decision: "APPROVE",
        evidence_ref: "TEST-ONLY",
        reason: "软件验收核对",
      });
    await review("1");
    await list.getByLabel("查询资料").fill(name);
    await list.getByRole("button", { name: "查询", exact: true }).click();
    await list
      .getByRole("row")
      .filter({ hasText: name })
      .getByRole("button", { name: "资料详情" })
      .click();
    const detail = page.getByRole("region", { name: "资料详情", exact: true });
    await expect(detail).toContainText(`共享机构：${name}`);
    await detail.getByRole("button", { name: "修订资料", exact: true }).click();
    const edit = page.getByRole("region", { name: "修订资料", exact: true });
    await edit.getByLabel("单位类型").selectOption("BOTH");
    await edit.getByLabel("修订原因").fill("该单位也向本企业采购");
    await edit.getByRole("button", { name: "保存修订并重新送审" }).click();
    await expect(edit).not.toBeVisible();
    await expect(detail.locator(".catalog-identity")).toContainText("待审核");
    await review("3");
    await detail
      .getByRole("button", { name: "刷新资料详情", exact: true })
      .click();
    for (const [path, key] of [
      ["purchasing", "suppliers"],
      ["selling", "customers"],
    ]) {
      const options = await read(
        context,
        `/api/v1/${path}/options?entity_id=${owner.entities[0].id}`,
      );
      expect(options[key].some((p: any) => p.id === party.id)).toBe(true);
    }
    await detail.getByRole("tab", { name: "经营信息", exact: true }).click();
    const operations = detail.getByRole("region", { name: "经营信息卡片" });
    await operations.getByLabel("联系人", { exact: true }).fill("未保存联系人");
    await page
      .getByRole("button", { name: "关闭往来单位", exact: true })
      .click();
    await page
      .getByRole("dialog", { name: "关闭前保存修改" })
      .getByRole("button", { name: "取消关闭" })
      .click();
    await expect(operations.getByLabel("联系人", { exact: true })).toHaveValue(
      "未保存联系人",
    );
    await operations.getByLabel("变更说明").fill("日常联系资料更新");
    await operations.getByRole("button", { name: "保存经营信息" }).click();
    await expect(operations).toContainText("已与当前档案同步");
    await expect(operations).toContainText("已审核");
    await detail.getByRole("tab", { name: "第三方对接" }).click();
    await detail.getByRole("button", { name: "新增编码" }).click();
    await page
      .getByRole("button", { name: "关闭往来单位", exact: true })
      .click();
    await page
      .getByRole("dialog", { name: "关闭前保存修改" })
      .getByRole("button", { name: "取消关闭" })
      .click();
    await detail.getByRole("button", { name: "取消新增编码" }).click();
    await detail.getByRole("tab", { name: "基本信息", exact: true }).click();
    await expect(page.locator(".el-message")).toHaveCount(0, { timeout: 8000 });
    await page.screenshot({
      path: "../../outputs/party-mdm/统一机构详情.png",
      fullPage: true,
    });
    await detail.getByRole("button", { name: "← 返回资料列表" }).click();
    await list
      .getByRole("button", { name: "新建往来单位", exact: true })
      .click();
    await entry.getByLabel("名称", { exact: true }).fill(code);
    const existing = page
      .locator(".party-candidate")
      .filter({ hasText: "已建档" })
      .filter({ hasText: name });
    await expect(existing).toBeVisible();
    await existing.click();
    await page
      .getByRole("dialog", { name: "使用已有资料" })
      .getByRole("button", { name: "打开已有资料" })
      .click();
    await expect(
      detail.getByRole("heading", { name, exact: true }),
    ).toBeVisible();
    expect(errors).toEqual([]);
  } finally {
    await reviewer.close();
  }
});
