import { chooseParty } from "./business-helpers";
import { headers, view } from "./helpers";
import { chooseEntrySku } from "./business-helpers";
import { test, expect, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";
import {
  command,
  nav,
  read,
  stockedCustomer,
  wholesale,
} from "./business-helpers";

const purchasePath = "/api/v1/purchase-orders";
async function purchaseForm(page: Page, f: any) {
  await nav(page, "采购入库");
  await page.getByRole("button", { name: "新建采购订单", exact: true }).click();
  const dialog = page.getByRole("region", { name: "新建采购单", exact: true });
  await chooseParty(
    dialog.getByRole("combobox", { name: "供应商", exact: true }),
    f.party.id,
  );
  await chooseEntrySku(dialog, f.product.sku_id);
  await dialog.getByLabel("采购数量").fill("2");
  await dialog.getByLabel("含税单价（元）").fill("13");
  return dialog;
}
const pending = (page: Page) =>
  page.getByRole("region", { name: "待确认提交", exact: true });

test("lost purchase response survives reload, tenant and account changes and replays the original command once", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(150000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext();
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    const f = await stockedCustomer(context, owner, reviewer, checker);
    const attempts: { key: string | undefined; body: string | null }[] = [];
    let original: any;
    let denyRecovery = false;
    await page.route("**" + purchasePath, async (route) => {
      if (route.request().method() !== "POST") return route.continue();
      attempts.push({
        key: route.request().headers()["idempotency-key"],
        body: route.request().postData(),
      });
      if (denyRecovery)
        return route.fulfill({
          status: 403,
          json: {
            error: { code: "FORBIDDEN", message: "模拟恢复期间权限变化" },
          },
        });
      const response = await route.fetch({ maxRetries: 0 });
      expect(response.status()).toBe(200);
      if (!original) {
        original = (await response.json()).data;
        return route.abort("failed"); // The real transaction committed before the response was lost.
      }
      expect((await response.json()).data).toEqual(original);
      await route.fulfill({ response });
    });
    await page.goto("/");
    const dialog = await purchaseForm(page, f);
    await dialog.getByRole("button", { name: "保存采购单" }).click();
    await expect(
      pending(page).getByRole("button", { name: "继续原提交" }),
    ).toBeEnabled();
    await expect(
      page.locator(".el-message").filter({ hasText: "原提交结果尚未确认" }),
    ).toBeVisible();
    expect(original.id).toBeTruthy();
    await expect(dialog.getByLabel("采购数量")).toBeDisabled();
    await expect(
      dialog.getByRole("button", { name: "保存采购单" }),
    ).toBeDisabled();
    expect(attempts).toHaveLength(1);
    await page.reload();
    await expect(pending(page)).toBeVisible();
    expect(attempts).toHaveLength(1); // Reading/reloading does not repeat writes automatically.
    const other = owner.tenants.find((t: any) => t.id !== owner.tenant_id);
    await page.getByRole("combobox", { name: "切换企业" }).press("Enter");
    await page.getByRole("option", { name: other.name, exact: true }).click();
    const guard = page.getByRole("dialog", {
      name: "提交尚未结束",
      exact: true,
    });
    await expect(guard).toBeVisible();
    await guard.getByRole("button", { name: "知道了", exact: true }).click();
    await expect(pending(page)).toBeVisible();
    // Another tab may change the server context; pending commands remain scoped to the old tenant.
    const switched = await context.request.post(
      "http://127.0.0.1:5173/auth/tenant",
      {
        headers: headers(await view(context.request)),
        data: { tenant_id: other.id },
      },
    );
    expect(switched.status()).toBe(200);
    await page.goto("/");
    await expect(
      page
        .locator(".account .el-select__selected-item")
        .filter({ hasText: other.name }),
    ).toBeVisible();
    await expect(pending(page)).toHaveCount(0);
    await page.getByRole("combobox", { name: "切换企业" }).press("Enter");
    await page
      .getByRole("option", { name: owner.tenant_name, exact: true })
      .click();
    await expect(pending(page)).toBeVisible();
    await context.clearCookies();
    await wholesale(context, "demo.viewer");
    await page.reload();
    await expect(
      page.getByRole("heading", { name: "企业工作台", exact: true }),
    ).toBeVisible();
    await expect(pending(page)).toHaveCount(0);
    await context.clearCookies();
    await wholesale(context, "demo.owner");
    await page.reload();
    await expect(pending(page)).toBeVisible();
    denyRecovery = true;
    await pending(page).getByRole("button", { name: "继续原提交" }).click();
    await expect(
      page.locator(".el-message").filter({ hasText: "原提交结果尚未确认" }),
    ).toBeVisible();
    await expect(pending(page)).toBeVisible(); // A later denial cannot disprove the earlier commit.
    await expect(page.locator(".el-message")).toHaveCount(0, { timeout: 8000 });
    await page.screenshot({
      path: "../../artifacts/command-recovery-pending.png",
      fullPage: true,
    });
    denyRecovery = false;
    await pending(page).getByRole("button", { name: "继续原提交" }).click();
    await expect(pending(page)).toHaveCount(0);
    await expect(
      page.getByText(
        `原提交已确认，单号：${original.number}。请从业务列表或待办继续办理。`,
      ),
    ).toBeVisible();
    expect(attempts).toHaveLength(3);
    expect(
      attempts.every(
        (r) => r.key === attempts[0]!.key && r.body === attempts[0]!.body,
      ),
    ).toBe(true);
    const orders = await read(context, purchasePath + `?entity_id=${f.entity}`);
    expect(
      orders.items.filter((r: any) => r.supplier_id === f.party.id),
    ).toHaveLength(2); // Setup + exactly one recovered order.
    const saved = await read(context, purchasePath + "/" + original.id);
    expect(Number(saved.lines[0].qty)).toBe(2);
  } finally {
    await reviewer.close();
  }
});

test("incomplete cash response resumes without a second receipt and completes the original sale", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(120000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext();
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    const f = await stockedCustomer(context, owner, reviewer, checker);
    const account = await command(
      context,
      owner,
      "/api/v1/settlements/accounts",
      {
        entity_id: f.entity,
        code: "REC-" + f.suffix,
        name: "恢复验证现金-" + f.suffix,
        method: "CASH",
      },
    );
    const sale = await command(context, owner, "/api/v1/retail/sales", {
      client_operation_id: randomUUID(),
      entity_id: f.entity,
      warehouse_id: f.warehouse,
      lines: [
        {
          sku_id: f.product.sku_id,
          qty: "3",
          unit_price: "20",
          tax_rate: "0.13",
        },
      ],
    });
    const attempts: string[] = [];
    await page.route(
      `**/api/v1/retail/sales/${sale.id}/cash`,
      async (route) => {
        attempts.push(
          route.request().headers()["idempotency-key"]! +
            route.request().postData(),
        );
        const response = await route.fetch({ maxRetries: 0 });
        expect(response.status()).toBe(200);
        if (attempts.length === 1)
          return route.fulfill({ status: 200, json: { incomplete: true } });
        await route.fulfill({ response });
      },
    );
    await page.goto("/");
    await nav(page, "零售收银");
    await page
      .getByRole("row")
      .filter({ hasText: sale.number })
      .getByRole("button", { name: "零售详情" })
      .click();
    await page
      .getByRole("button", { name: "登记现金收款", exact: true })
      .click();
    let dialog = page.getByRole("dialog");
    await dialog
      .getByLabel("现金账户", { exact: true })
      .selectOption(account.id);
    await dialog.getByLabel("顾客交付现金（元）", { exact: true }).fill("100");
    await dialog.getByLabel("我已核对现金实收与找零").check();
    await dialog
      .getByLabel("操作说明")
      .fill("实际收现金100，找零40，响应丢失恢复验证");
    await dialog.getByRole("button", { name: "确认保存", exact: true }).click();
    await expect(
      page.locator(".el-message").filter({ hasText: "原提交结果尚未确认" }),
    ).toBeVisible();
    await page.reload();
    await expect(pending(page)).toBeVisible();
    await pending(page).getByRole("button", { name: "继续原提交" }).dblclick();
    await expect(pending(page)).toHaveCount(0);
    expect(attempts).toHaveLength(2);
    expect(attempts[1]).toBe(attempts[0]);
    let doc = await read(context, `/api/v1/retail/sales/${sale.id}`);
    expect(doc.sale.state).toBe("PAID_PENDING_POST");
    expect(doc.payments).toHaveLength(1);
    expect(Number(doc.payments[0].amount)).toBe(60);
    const queue = page.getByRole("region", { name: "进销存业务待办" });
    await queue.getByLabel("待办环节").selectOption("retail_complete");
    await queue.getByLabel("查找待办").fill(sale.number);
    await queue.getByRole("button", { name: "查询待办", exact: true }).click();
    await queue
      .getByRole("row")
      .filter({ hasText: sale.number })
      .getByRole("button", { name: "查看原单" })
      .click();
    await page
      .getByRole("button", { name: "核对并完成出库", exact: true })
      .click();
    dialog = page.getByRole("dialog");
    await dialog.getByLabel("交付数量 1", { exact: true }).fill("3");
    await dialog.getByLabel("交付批号 1", { exact: true }).fill(f.lot);
    await dialog.getByLabel("操作说明").fill("原单恢复后实际交付核对");
    await dialog.getByRole("button", { name: "确认保存", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    doc = await read(context, `/api/v1/retail/sales/${sale.id}`);
    expect(doc.sale.state).toBe("COMPLETED");
    expect(doc.payments).toHaveLength(1);
    const stock = await read(
      context,
      `/api/v1/inventory/availability?entity_id=${f.entity}&warehouse_id=${f.warehouse}&sku_id=${f.product.sku_id}`,
    );
    expect(stock.items[0].on_hand).toBe("7.000000");
    expect(stock.items[0].reserved).toBe("0.000000");
  } finally {
    await reviewer.close();
  }
});

test("unavailable tab storage prevents dispatch and expired or corrupt records require explicit manual reconciliation", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(120000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext();
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    const f = await stockedCustomer(context, owner, reviewer, checker);
    let writes = 0;
    page.on("request", (r) => {
      if (
        r.method() === "POST" &&
        r.url().includes("/api/v1/") &&
        !r.url().includes("/document-drafts/")
      )
        writes++;
    });
    await page.goto("/");
    await page.evaluate(() => {
      const set = Storage.prototype.setItem;
      Storage.prototype.setItem = function (key, value) {
        if (key.startsWith("medinv.submission.v1:"))
          throw new DOMException("Unavailable", "QuotaExceededError");
        return set.call(this, key, value);
      };
    });
    const dialog = await purchaseForm(page, f);
    await dialog.getByRole("button", { name: "保存采购单" }).click();
    await expect(
      page.locator(".el-message").filter({ hasText: "本次未发送" }),
    ).toBeVisible();
    expect(writes).toBe(0);
    await page.reload(); // Restores the real Storage implementation.
    const storageKey = `medinv.submission.v1:${owner.principal_id}:${owner.tenant_id}`;
    for (const raw of [
      JSON.stringify({
        version: 1,
        principal: owner.principal_id,
        tenant: owner.tenant_id,
        path: purchasePath,
        body: "{}",
        key: randomUUID(),
        created: Date.now() - 25 * 60 * 60 * 1000,
      }),
      "{broken",
    ]) {
      await page.evaluate(
        ({ key, value }) => sessionStorage.setItem(key, value),
        { key: storageKey, value: raw },
      );
      await page.reload();
      await expect(pending(page)).toBeVisible();
      if (raw !== "{broken") {
        await pending(page).getByRole("button", { name: "继续原提交" }).click();
        await expect(
          page.locator(".el-message").filter({ hasText: "超过可自动恢复时间" }),
        ).toBeVisible();
      } else
        await expect(pending(page)).toContainText("无法读取本页的提交记录");
      expect(writes).toBe(0);
      await pending(page)
        .getByRole("button", { name: "已人工核对，结束确认" })
        .click();
      const confirmation = page.getByRole("dialog", {
        name: "确认已核对原业务",
      });
      await expect(
        confirmation.getByRole("button", { name: "结束本次确认" }),
      ).toBeDisabled();
      await confirmation.getByRole("checkbox").check();
      await confirmation.getByRole("button", { name: "结束本次确认" }).click();
      await expect(pending(page)).toHaveCount(0);
      expect(
        await page.evaluate((key) => sessionStorage.getItem(key), storageKey),
      ).toBeNull();
    }
    expect(writes).toBe(0);
  } finally {
    await reviewer.close();
  }
});
