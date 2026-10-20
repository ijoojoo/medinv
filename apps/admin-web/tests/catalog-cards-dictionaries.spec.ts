import { test, expect, type Page } from "@playwright/test";
import {
  wholesale,
  stockedCustomer,
  command,
  read,
  nav,
} from "./business-helpers";
import type { CatalogDictionary } from "../src/catalogDictionaries";

async function openRecord(page: Page, menu: string, name: string) {
  await nav(page, menu);
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
  return page
    .getByRole("region", { name: "资料详情", exact: true })
    .filter({ visible: true });
}

test("product and party cards retain edits, preserve approval and isolate platform mappings", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(120000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext(),
    checker = await wholesale(reviewer, "demo.quality");
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    const f = await stockedCustomer(context, owner, reviewer, checker);
    await page.goto("/");
    for (const [kind, menu, name, id] of [
      ["products", "商品档案", f.name, f.product.id],
      ["parties", "往来单位", f.customer, f.party.id],
    ]) {
      const detail = await openRecord(page, menu, name);
      await detail.getByRole("tab", { name: "经营信息", exact: true }).click();
      const ops = detail.getByRole("region", { name: "经营信息卡片" });
      await ops
        .getByLabel("推荐级别", { exact: true })
        .selectOption("重点推荐");
      await ops
        .getByLabel("ABC 分类 / 第一维", { exact: true })
        .selectOption("A");
      await ops
        .getByLabel("双 ABC / 第二维", { exact: true })
        .selectOption("B");
      await ops
        .getByLabel("检索别名 / 助记码", { exact: true })
        .fill("CARD-" + f.suffix);
      if (kind === "parties")
        await ops.getByLabel("联系人", { exact: true }).fill("日常联系人");
      await ops.getByLabel("变更说明").fill("经营分类维护，不重新审核");
      await detail.getByRole("tab", { name: "医保信息", exact: true }).click();
      await expect(
        detail.getByRole("tabpanel", { name: "医保信息" }),
      ).toContainText("对接编码");
      await detail.getByRole("tab", { name: /^经营信息/ }).click();
      await expect(
        ops.getByLabel("ABC 分类 / 第一维", { exact: true }),
      ).toHaveValue("A");
      await expect(
        detail.getByRole("button", { name: "← 返回资料列表" }),
      ).toBeDisabled();
      await ops
        .getByRole("button", { name: "保存经营信息", exact: true })
        .click();
      await expect(ops).toContainText("已与当前档案同步");
      const record = (await read(context, `/api/v1/catalog/${kind}/${id}`))
        .record;
      expect(record.approval_state || record.state).toBe("APPROVED");
      expect(record.revision_editors).toEqual([]);
      expect(record.abc_class).toBe("A");
      expect(record.abc_secondary_class).toBe("B");
      await detail.getByRole("tab", { name: "追溯信息", exact: true }).click();
      const msfx = detail.getByRole("tabpanel", {
        name: "追溯信息",
        exact: true,
      });
      await msfx.getByRole("button", { name: "新增编码", exact: true }).click();
      await msfx.getByLabel("编码类型").selectOption("MSFX");
      await expect(msfx.getByLabel("编码类型")).toHaveValue("MSFX");
      await expect(msfx.getByLabel("编码类型").locator("option")).toHaveCount(
        kind === "products" ? 2 : 1,
      );
      const external = "000" + kind + f.suffix;
      await msfx.getByLabel("店铺 / 账号 / 系统标识").fill("TEST-MSFX");
      await msfx.getByLabel("外部编码", { exact: true }).fill(external);
      await msfx.getByLabel("编码来源凭据").fill("TEST-ONLY");
      await msfx.getByLabel("登记原因").fill("卡片隔离验收");
      await detail
        .getByRole("tab", { name: "第三方对接", exact: true })
        .click();
      await expect(
        detail.getByRole("tabpanel", { name: "第三方对接", exact: true }),
      ).not.toContainText(external);
      await detail.getByRole("tab", { name: "追溯信息", exact: true }).click();
      await expect(msfx.getByLabel("外部编码", { exact: true })).toHaveValue(
        external,
      );
      await msfx.getByRole("button", { name: "保存编码", exact: true }).click();
      await expect(
        msfx.getByRole("form", { name: "新增对接编码" }),
      ).not.toBeVisible();
      await expect(
        msfx.getByRole("row").filter({ hasText: external }),
      ).toHaveCount(1);
      await detail.getByRole("tab", { name: "变更记录", exact: true }).click();
      await expect(detail.getByTestId("catalog-history")).toContainText(
        "经营信息更新（无需审核）",
      );
      await detail.getByRole("tab", { name: "经营信息", exact: true }).click();
      await page.evaluate(() => window.scrollTo(0, 0));
      await expect(page.locator(".el-message")).toHaveCount(0, {
        timeout: 8000,
      });
      await page.screenshot({
        path: `../../artifacts/catalog-cards-${kind}.png`,
        fullPage: true,
        animations: "disabled",
      });
      await page.setViewportSize({ width: 390, height: 760 });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      ).toBe(true);
      await page.setViewportSize({ width: 1440, height: 1000 });
    }
    const options = await read(
      context,
      `/api/v1/purchasing/options?entity_id=${f.entity}`,
    );
    expect(options.skus.some((r: any) => r.id === f.product.sku_id)).toBe(true);
    expect(options.suppliers.some((r: any) => r.id === f.party.id)).toBe(true);
    expect(errors).toEqual([]);
  } finally {
    await reviewer.close();
  }
});

test("dictionary editing, strict field selection, disabled historical values and readonly access", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(120000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext(),
    checker = await wholesale(reviewer, "demo.quality");
  const path = "/api/v1/catalog/dictionaries/trade_name";
  const original = (await read(context, path)) as CatalogDictionary;
  const value = "字典品牌-" + Date.now().toString(36);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    const f = await stockedCustomer(context, owner, reviewer, checker);
    await page.goto("/");
    await nav(page, "商品字典");
    const dictionary = page.getByRole("region", {
      name: "商品字典",
      exact: true,
    });
    await dictionary.getByRole("button", { name: /商品名 \/ 品牌/ }).click();
    await dictionary.getByLabel("选值规则").selectOption("STRICT");
    await dictionary
      .getByRole("button", { name: "新增字典值", exact: true })
      .click();
    const index = original.items.length + 1;
    await dictionary.getByLabel(`字典值 ${index}`, { exact: true }).fill(value);
    await dictionary.getByLabel(`排序 ${index}`, { exact: true }).fill("0");
    await dictionary.getByLabel("变更说明").fill("维护可选品牌");
    await nav(page, "商品档案");
    await nav(page, "商品字典");
    await expect(
      dictionary.getByLabel(`字典值 ${index}`, { exact: true }),
    ).toHaveValue(value);
    await dictionary
      .getByRole("button", { name: "保存字典", exact: true })
      .click();
    await expect(dictionary.getByLabel("变更说明")).toHaveValue("");
    const saved = (await read(context, path)) as CatalogDictionary;
    expect(saved.mode).toBe("STRICT");
    expect(saved.items.some((i) => i.value === value && i.order === 0)).toBe(
      true,
    );
    await page.evaluate(() => window.scrollTo(0, 0));
    await expect(page.locator(".el-message")).toHaveCount(0, { timeout: 8000 });
    await page.screenshot({
      path: "../../artifacts/catalog-dictionary.png",
      fullPage: true,
      animations: "disabled",
    });
    let detail = await openRecord(page, "商品档案", f.name);
    await detail.getByRole("button", { name: "修订资料", exact: true }).click();
    const edit = page.getByRole("region", { name: "修订资料", exact: true });
    await edit
      .getByRole("combobox", { name: "商品名 / 品牌", exact: true })
      .selectOption(value);
    await edit.getByLabel("修订原因").fill("按标签使用字典品牌");
    await edit
      .getByRole("button", { name: "保存修订并重新送审", exact: true })
      .click();
    await expect(edit).not.toBeVisible();
    const record = (
      await read(context, `/api/v1/catalog/products/${f.product.id}`)
    ).record;
    expect(record.approval_state).toBe("DRAFT");
    expect(record.trade_name).toBe(value);
    await command(
      reviewer,
      checker,
      `/api/v1/catalog/products/${f.product.id}/review`,
      {
        expected_version: record.version,
        decision: "APPROVE",
        evidence_ref: "TEST",
        reason: "核对标签",
      },
    );
    await nav(page, "商品字典");
    const indexNow = saved.items.findIndex((i) => i.value === value) + 1;
    await dictionary.getByLabel(`启用 ${indexNow}`, { exact: true }).uncheck();
    await dictionary.getByLabel("变更说明").fill("停用测试品牌，保留历史");
    await dictionary
      .getByRole("button", { name: "保存字典", exact: true })
      .click();
    await expect(dictionary.getByLabel("变更说明")).toHaveValue("");
    await nav(page, "商品档案");
    detail = page.getByRole("region", { name: "资料详情", exact: true });
    await detail
      .getByRole("button", { name: "刷新资料详情", exact: true })
      .click();
    await expect(detail.locator(".catalog-identity")).toContainText("已审核");
    await detail.getByRole("button", { name: "修订资料", exact: true }).click();
    await expect(
      edit.getByRole("combobox", { name: "商品名 / 品牌", exact: true }),
    ).toHaveValue(value);
    await expect(
      edit.getByRole("option", { name: value + "（当前历史值）", exact: true }),
    ).toHaveJSProperty("disabled", true);
    await edit.getByLabel("规格型号", { exact: true }).fill("保留历史品牌");
    await edit.getByLabel("修订原因").fill("历史值不被字典停用改写");
    await edit
      .getByRole("button", { name: "保存修订并重新送审", exact: true })
      .click();
    await expect(edit).not.toBeVisible();
    expect(
      (await read(context, `/api/v1/catalog/products/${f.product.id}`)).record
        .trade_name,
    ).toBe(value);
    const viewer = await browser.newContext();
    try {
      await wholesale(viewer, "demo.viewer");
      const p = await viewer.newPage();
      await p.goto("/");
      await nav(p, "商品字典");
      await expect(
        p.getByRole("button", { name: "保存字典", exact: true }),
      ).toHaveCount(0);
      await expect(p.getByLabel("选值规则")).toBeDisabled();
    } finally {
      await viewer.close();
    }
    expect(errors).toEqual([]);
  } finally {
    const current = (await read(context, path)) as CatalogDictionary;
    const items = current.items.map(
      (i) =>
        original.items.find((o) => o.value === i.value) || {
          ...i,
          active: false,
        },
    );
    if (
      JSON.stringify(items) !== JSON.stringify(current.items) ||
      current.mode !== original.mode
    )
      await command(context, owner, path, {
        expected_version: current.version,
        mode: original.mode,
        items,
        reason: "验收后恢复原配置，测试值保留为停用",
      });
    await reviewer.close();
  }
});
