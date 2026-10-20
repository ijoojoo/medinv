import { chooseParty } from "./business-helpers";
import { chooseEntrySku } from "./business-helpers";
import { test, expect, type Page } from "@playwright/test";
import {
  wholesale,
  stockedCustomer,
  command,
  read,
  nav,
} from "./business-helpers";

async function openProduct(page: Page, name: string) {
  await page.goto("/");
  await nav(page, "商品档案");
  await page.getByLabel("查询资料", { exact: true }).fill(name);
  await page.getByRole("button", { name: "查询", exact: true }).click();
  await page
    .getByRole("row")
    .filter({ hasText: name })
    .getByRole("button", { name: "资料详情", exact: true })
    .click();
  await page.getByRole("tab", { name: "基本信息", exact: true }).click();
  const panel = page.getByTestId("packaging-panel");
  await panel.locator("summary").click();
  return panel;
}

test("independently reviewed packaging drives purchase, wholesale and retail quantities and snapshots", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(180000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext();
  const quality = await reviewer.newPage();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  quality.on("pageerror", (e) => errors.push(e.message));
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    const f = await stockedCustomer(context, owner, reviewer, checker);
    let panel = await openProduct(page, f.name);
    await panel
      .getByRole("button", { name: "新增包装版本", exact: true })
      .click();
    await panel.getByLabel("包装单位名称", { exact: true }).fill("箱");
    await panel.getByLabel("基本单位数量（分子）", { exact: true }).fill("3");
    await panel
      .getByLabel("包装维护原因", { exact: true })
      .fill("核对一箱三个的测试包装");
    await panel
      .getByRole("button", { name: "保存待审包装", exact: true })
      .click();
    await expect(
      panel.getByRole("cell", { name: "待审核 尚未批准", exact: true }),
    ).toBeVisible();
    await panel.getByRole("button", { name: "审核包装", exact: true }).click();
    await expect(
      panel.getByLabel("包装审核结果").locator('option[value="APPROVE"]'),
    ).toHaveAttribute("disabled", "");
    panel = await openProduct(quality, f.name);
    await panel.getByRole("button", { name: "审核包装", exact: true }).click();
    await panel.getByLabel("包装核验凭据").fill("PACK-E2E");
    await panel.getByLabel("包装审核说明").fill("独立确认一箱三个");
    await panel
      .getByRole("button", { name: "保存包装审核", exact: true })
      .click();
    await expect(panel).toContainText("已启用");
    await quality.screenshot({
      path: "../../artifacts/packaging-reviewed.png",
      fullPage: true,
      animations: "disabled",
    });
    const path = `/api/v1/catalog/skus/${f.product.sku_id}/packaging`;
    const pack = (await read(context, path)).items[0];

    for (const [menu, action, partyLabel, amountLabel, save, endpoint] of [
      [
        "采购入库",
        "新建采购单",
        "供应商",
        "采购数量",
        "保存采购单",
        "/api/v1/purchase-orders",
      ],
      [
        "批发销售",
        "新建销售单",
        "客户",
        "销售数量",
        "保存销售单",
        "/api/v1/sales-orders",
      ],
    ]) {
      await page.goto("/");
      await nav(page, menu);
      await page.getByRole("button", { name: action === "新建采购单" ? "新建采购订单" : action, exact: true }).click();
      const dialog = page.locator(".document-editor:visible");
      await chooseParty(
        dialog.getByRole("combobox", { name: partyLabel, exact: true }),
        f.party.id,
      );
      await dialog
        .getByRole("combobox", {
          name: menu === "采购入库" ? "收货仓库" : "发货仓库",
          exact: true,
        })
        .selectOption(f.warehouse);
      if (menu === "批发销售")
        await dialog.getByLabel("收货地址").fill("包装验收收货点");
      await chooseEntrySku(dialog, f.product.sku_id);
      const unit = dialog.getByRole("combobox", { name: /^交易单位 1/ });
      await expect(unit).toBeEnabled();
      await unit.selectOption(pack.id);
      await dialog.getByLabel(amountLabel, { exact: true }).fill("2");
      await dialog.getByLabel("含税单价（元）", { exact: true }).fill("9");
      const response = page.waitForResponse(
        (r) => r.url().endsWith(endpoint) && r.request().method() === "POST",
      );
      await dialog.getByRole("button", { name: save, exact: true }).click();
      const created = await response;
      expect(created.status()).toBe(200);
      const doc = await read(
        context,
        `${endpoint}/${(await created.json()).data.id}`,
      );
      expect(doc.lines[0].qty).toBe("6.000000");
      expect(doc.lines[0].trade_qty).toBe("2.000000");
      expect(doc.lines[0].gross_amount).toBe("18.00");
      expect(doc.lines[0].package_snapshot.packaging_id).toBe(pack.id);
      await expect(dialog).not.toBeVisible();
      await expect(
        page.getByRole("row").filter({ hasText: f.name }).first(),
      ).toContainText("2 箱");
    }
    await page.goto("/");
    await nav(page, "零售收银");
    await page.getByRole("button", { name: "新建零售单", exact: true }).click();
    let dialog = page.getByRole("dialog");
    await dialog
      .getByRole("combobox", { name: "出货仓库", exact: true })
      .selectOption(f.warehouse);
    await dialog.getByLabel("搜索商品").fill(f.name);
    await dialog.getByRole("button", { name: "查找可售商品" }).click();
    await dialog
      .getByRole("row")
      .filter({ hasText: f.name })
      .getByRole("button", { name: "加入", exact: true })
      .click();
    const unit = dialog.getByRole("combobox", { name: /^零售单位 1/ });
    await expect(unit).toBeEnabled();
    await unit.selectOption(pack.id);
    await dialog.getByLabel("零售数量 1", { exact: true }).fill("1");
    await dialog.getByLabel("零售单价 1", { exact: true }).fill("9");
    const response = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/v1/retail/sales") &&
        r.request().method() === "POST",
    );
    await dialog
      .getByRole("button", { name: "建单并预占库存", exact: true })
      .click();
    const created = await response;
    expect(created.status()).toBe(200);
    const sid = (await created.json()).data.id;
    await expect(dialog).not.toBeVisible();
    const account = await command(
      context,
      owner,
      "/api/v1/settlements/accounts",
      {
        entity_id: f.entity,
        code: "PK-" + f.suffix,
        name: "包装零售验收现金",
        method: "CASH",
      },
    );
    await page
      .getByRole("button", { name: "登记现金收款", exact: true })
      .click();
    dialog = page.getByRole("dialog");
    await dialog
      .getByLabel("现金账户", { exact: true })
      .selectOption(account.id);
    await dialog.getByLabel("顾客交付现金（元）", { exact: true }).fill("10");
    await expect(dialog).toContainText("应找零：1.00 元");
    await dialog.getByLabel("我已核对现金实收与找零").check();
    await dialog.getByLabel("操作说明").fill("虚拟验收现金收款");
    await dialog.getByRole("button", { name: "确认保存", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await page
      .getByRole("button", { name: "核对并完成出库", exact: true })
      .click();
    dialog = page.getByRole("dialog");
    await expect(dialog).toContainText("应交付 3 个");
    await dialog.getByLabel("交付数量 1", { exact: true }).fill("3");
    await dialog.getByLabel("交付批号 1", { exact: true }).fill(f.lot);
    await dialog.getByLabel("操作说明").fill("一箱三件已交付");
    await dialog.getByRole("button", { name: "确认保存", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    expect(
      (await read(context, `/api/v1/retail/sales/${sid}`)).sale.state,
    ).toBe("COMPLETED");
    const stock = await read(
      context,
      `/api/v1/inventory/availability?entity_id=${f.entity}&warehouse_id=${f.warehouse}&sku_id=${f.product.sku_id}`,
    );
    expect(stock.items[0].on_hand).toBe("7.000000");
    expect(stock.items[0].reserved).toBe("0.000000");
    await expect(page.locator(".el-message")).toHaveCount(0, { timeout: 8000 });
    await page.screenshot({
      path: "../../artifacts/packaging-retail.png",
      fullPage: true,
      animations: "disabled",
    });
    expect(errors).toEqual([]);
  } finally {
    await reviewer.close();
  }
});
