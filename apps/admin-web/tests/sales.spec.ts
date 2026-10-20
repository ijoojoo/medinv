import { chooseParty } from "./business-helpers";
import { chooseEntrySku } from "./business-helpers";
import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { headers } from "./helpers";
import {
  wholesale,
  nav,
  stockedCustomer,
  read,
  origin,
} from "./business-helpers";

test("wholesale credit, independent review, partial shipment, receivable and closing in real UI", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(150000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext();
  const quality = await reviewer.newPage();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  quality.on("pageerror", (e) => errors.push(e.message));
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    const {
      suffix,
      year,
      name,
      customer,
      lot,
      entity,
      product,
      party,
      warehouse,
    } = await stockedCustomer(context, owner, reviewer, checker);

    await page.goto("/");
    await nav(page, "批发销售");
    await chooseParty(
      page.getByRole("combobox", { name: "客户信用账户", exact: true }),
      { label: customer },
    );
    await page
      .getByRole("button", { name: "设置客户额度", exact: true })
      .click();
    let dialog = page.getByRole("dialog");
    await dialog.getByLabel("授信额度（元）").fill("1000");
    await dialog.getByLabel("调整依据").fill("演示额度审批凭据");
    const validUntil = dialog.getByRole("textbox", {
      name: "额度有效期",
      exact: true,
    });
    let creditWrites = 0;
    const creditPath = `/api/v1/customer-credit/${party.id}`;
    page.on("request", (r) => {
      if (r.method() === "POST" && new URL(r.url()).pathname === creditPath)
        creditWrites++;
    });
    for (const value of ["", "20260230"]) {
      await validUntil.fill(value);
      await dialog
        .locator("form")
        .evaluate((f: HTMLFormElement) => f.requestSubmit());
      await expect(validUntil).toHaveAttribute("aria-invalid", "true");
      await expect(validUntil).toBeFocused();
      expect(creditWrites).toBe(0);
    }
    await validUntil.fill(`${year + 1}1231`);
    let release!: () => void;
    const pending = new Promise<void>((resolve) => (release = resolve));
    await page.route(`**${creditPath}?**`, async (route) => {
      if (route.request().method() !== "POST") return route.continue();
      const response = await route.fetch();
      await pending;
      await route.fulfill({ response });
    });
    const saving = page.waitForRequest(
      (r) => r.method() === "POST" && new URL(r.url()).pathname === creditPath,
    );
    await dialog.getByRole("button", { name: "保存客户额度" }).click();
    const savedRequest = await saving;
    try {
      await expect(validUntil).toBeDisabled();
      await expect(dialog.getByLabel("调整依据")).toBeDisabled();
      expect(savedRequest.postDataJSON().valid_until).toBe(`${year + 1}-12-31`);
    } finally {
      release();
    }
    await expect(dialog).not.toBeVisible();
    await page.unroute(`**${creditPath}?**`);
    expect(creditWrites).toBe(1);
    await expect(page.locator(".credit-grid")).toContainText("1000.00");
    await page.getByRole("button", { name: "新建销售单", exact: true }).click();
    dialog = page.getByRole("region", { name: "新建销售单", exact: true });
    await chooseParty(
      dialog.getByRole("combobox", { name: "客户", exact: true }),
      { label: customer },
    );
    await dialog.getByLabel("收货地址").fill("软件验收收货点");
    await dialog.getByLabel("最低剩余效期（天）").fill("30");
    await chooseEntrySku(dialog, { code: `SS-${suffix}` });
    await dialog.getByLabel("销售数量").fill("4");
    await dialog.getByLabel("含税单价（元）").fill("20");
    await dialog.getByRole("button", { name: "保存销售单" }).click();
    await expect(dialog).not.toBeVisible();
    const number = await page.locator(".document-number").first().innerText();
    await expect(
      page.getByRole("button", { name: "审核销售单", exact: true }),
    ).toBeDisabled();
    await quality.goto("/");
    await nav(quality, "批发销售");
    await quality
      .getByRole("row")
      .filter({ hasText: number })
      .getByRole("button", { name: "查看销售单" })
      .click();
    await quality
      .getByRole("button", { name: "审核销售单", exact: true })
      .click();
    dialog = quality.getByRole("dialog");
    await dialog.getByLabel("操作说明").fill("核查客户资质和销售约定");
    await dialog.getByRole("button", { name: "确认审核", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await page.getByRole("button", { name: "刷新销售详情" }).click();
    await page
      .getByRole("button", { name: "分配发货批次", exact: true })
      .click();
    dialog = page.getByRole("dialog");
    await dialog.getByLabel("本次发货数量").fill("3");
    await dialog.getByRole("button", { name: "确认分配", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await expect(page.locator(".shipment-card")).toContainText(lot);
    await page.getByRole("button", { name: "登记拣货", exact: true }).click();
    dialog = page.getByRole("dialog");
    await dialog.getByLabel("实物批号").fill(lot);
    await dialog.getByLabel("实物数量").fill("3");
    await dialog.getByLabel("核查说明").fill("实物逐行拣货");
    await dialog.getByRole("button", { name: "保存拣货", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await expect(
      page.getByRole("button", { name: "出库复核", exact: true }),
    ).toBeDisabled();
    await quality.getByRole("button", { name: "刷新销售详情" }).click();
    await quality
      .getByRole("button", { name: "出库复核", exact: true })
      .click();
    dialog = quality.getByRole("dialog");
    await dialog.getByLabel("实物批号").fill(lot);
    await dialog.getByLabel("实物数量").fill("3");
    await dialog.getByLabel("核查说明").fill("独立复核合格");
    await dialog.getByLabel("物流 / 交接凭据").fill("WAYBILL-" + suffix);
    await dialog.getByRole("button", { name: "确认复核", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await quality
      .getByRole("button", { name: "销售出库", exact: true })
      .click();
    dialog = quality.getByRole("dialog");
    await expect(dialog).toContainText("3 个");
    await dialog.getByRole("button", { name: "确认出库", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await expect(quality.locator(".shipment-card")).toContainText("已出库");
    await expect(quality.locator(".el-message")).toHaveCount(0);
    await quality.evaluate(() => window.scrollTo(0, 0));
    await quality.screenshot({
      path: "../../artifacts/sales-partial-shipment.png",
      fullPage: true,
      animations: "disabled",
    });
    await nav(quality, "销售应收");
    const arRow = quality.getByRole("row").filter({ hasText: customer });
    await expect(arRow).toHaveCount(1);
    await expect(arRow).toContainText("60.00");
    await arRow.getByRole("button", { name: "应收详情" }).click();
    dialog = quality.getByRole("dialog");
    await expect(dialog).toContainText("53.10 / 6.90 元");
    await expect(dialog).toContainText("34.512 元");
    await quality.evaluate(() => window.scrollTo(0, 0));
    await quality.screenshot({
      path: "../../artifacts/sales-receivable.png",
      fullPage: true,
      animations: "disabled",
    });
    await dialog.locator(".el-dialog__headerbtn").click();
    await nav(quality, "库存台账");
    const issue = quality
      .getByRole("row")
      .filter({ hasText: name })
      .filter({ hasText: "-3 个" });
    await issue.getByRole("button", { name: "查看过账应收" }).click();
    dialog = quality.getByRole("dialog");
    await expect(dialog).toContainText("60.00 元");
    await dialog.locator(".el-dialog__headerbtn").click();
    await page.getByRole("button", { name: "刷新销售详情" }).click();
    await page
      .getByRole("button", { name: "关闭订单余量", exact: true })
      .click();
    dialog = page.getByRole("dialog");
    await dialog.getByLabel("操作说明").fill("客户取消剩余 1 件");
    await dialog.getByRole("button", { name: "确认关闭" }).click();
    await expect(dialog).not.toBeVisible();
    await expect(
      page.locator(".panel-heading .el-tag:visible").first(),
    ).toHaveText("余量已关闭");
    await page.getByRole("button", { name: "返回销售列表" }).click();
    await chooseParty(
      page.getByRole("combobox", { name: "客户信用账户", exact: true }),
      { label: customer },
    );
    const credit = page.locator(".credit-grid");
    await expect(credit.locator("b").nth(1)).toHaveText("0.00");
    await expect(credit.locator("b").nth(2)).toHaveText("60.00");
    await expect(credit.locator("b").nth(3)).toHaveText("940.00");
    const stock = await read(
      context,
      `/api/v1/inventory/availability?entity_id=${entity}&warehouse_id=${warehouse}&sku_id=${product.sku_id}`,
    );
    expect(stock.items[0].on_hand).toBe("7.000000");
    expect(stock.items[0].reserved).toBe("0.000000");
    expect(errors).toEqual([]);
  } finally {
    await reviewer.close();
  }
});

test("viewer can inspect sales and receivables, but cannot create orders or adjust credit", async ({
  page,
  context,
}) => {
  const current = await wholesale(context, "demo.viewer");
  await page.goto("/");
  await nav(page, "批发销售");
  await expect(
    page.getByRole("button", { name: "新建销售单", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "设置客户额度", exact: true }),
  ).toHaveCount(0);
  await nav(page, "销售应收");
  const response = await context.request.post(
    origin +
      "/api/v1/customer-credit/" +
      randomUUID() +
      "?entity_id=" +
      current.entities[0].id,
    {
      headers: { ...headers(current), "Idempotency-Key": randomUUID() },
      data: {
        expected_version: "0",
        credit_limit: "100",
        valid_until: "2099-12-31",
        reason: "越权测试",
      },
    },
  );
  expect(response.status()).toBe(403);
});
