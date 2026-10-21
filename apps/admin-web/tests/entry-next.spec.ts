import { chooseWarehouse } from "./business-helpers";
import { chooseParty } from "./business-helpers";
import { test, expect, type Page, type Locator } from "@playwright/test";
import {
  wholesale,
  stockedCustomer,
  read,
  nav,
  chooseEntrySku,
} from "./business-helpers";

const draftId = (page: Page) => new URL(page.url()).pathname.split("/").pop()!;
const visibleEditor = (page: Page) => page.locator(".document-editor:visible");
const nextButton = (editor: Locator) =>
  editor.getByRole("button", { name: "保存并新建", exact: true });
async function fill(page: Page, f: any, purchase = true) {
  const editor = visibleEditor(page);
  await expect(nextButton(editor)).toBeEnabled();
  await chooseParty(
    editor.getByRole("combobox", {
      name: purchase ? "供应商" : "客户",
      exact: true,
    }),
    f.party.id,
  );
  await chooseWarehouse(
    editor.getByRole("combobox", {
      name: purchase ? "收货仓库" : "发货仓库",
      exact: true,
    }),
    f.warehouse,
  );
  if (!purchase) {
    await editor
      .getByLabel("收货地址", { exact: true })
      .fill("连续开单虚拟地址");
    await editor.getByLabel("最低剩余效期（天）", { exact: true }).fill("45");
    await editor.getByLabel("账期（天）", { exact: true }).fill("12");
  }
  await chooseEntrySku(editor.locator(".entry-row").first(), f.product.sku_id);
  await editor
    .getByLabel(purchase ? "采购数量" : "销售数量", { exact: true })
    .fill("2");
  await editor.getByLabel("含税单价（元）", { exact: true }).fill("0.335001");
  return editor;
}

for (const purchase of [true, false]) {
  test(`${purchase ? "purchase" : "sales"} save and new carries only headers, remains linked, and can start blank`, async ({
    page,
    context,
    browser,
  }) => {
    test.setTimeout(120000);
    const owner = await wholesale(context, "demo.owner"),
      reviewer = await browser.newContext();
    let release = () => {},
      releaseParty = () => {};
    try {
      const checker = await wholesale(reviewer, "demo.quality");
      const f = await stockedCustomer(context, owner, reviewer, checker);
      const endpoint = purchase
        ? "/api/v1/purchase-orders"
        : "/api/v1/sales-orders";
      const action = purchase ? "新建采购单" : "新建销售单";
      const qtyLabel = purchase ? "采购数量" : "销售数量";
      const faults: string[] = [];
      page.on("pageerror", (e) => faults.push(e.message));
      await page.setViewportSize({ width: 1366, height: 768 });
      await page.goto("/");
      await nav(page, purchase ? "采购入库" : "批发销售");
      await page
        .getByRole("button", {
          name: action === "新建采购单" ? "新建采购订单" : action,
          exact: true,
        })
        .click();
      const editor = await fill(page, f, purchase);
      const originalId = draftId(page),
        originalUrl = page.url();
      const originalKey = await editor
        .locator(".entry-row")
        .first()
        .getAttribute("data-row-key");
      await editor
        .getByLabel("草稿备注", { exact: true })
        .fill("此备注仅用于上一张单据");
      await editor
        .getByRole("button", { name: "复制当前商品行", exact: true })
        .click();
      await expect(editor.locator(".entry-row")).toHaveCount(2);
      const attempts: any[] = [];
      const hold = new Promise<void>((resolve) => {
        release = resolve;
      });
      await page.route("**" + endpoint, async (route) => {
        if (route.request().method() !== "POST") return route.continue();
        attempts.push(route.request().postDataJSON());
        if (attempts.length === 1) await hold;
        return route.continue();
      });
      // Hold the carried party label until the new editor is otherwise ready.
      // Empty display text during hydration must not count as a missing party.
      let partyLoads = 0;
      const partyHold = new Promise<void>((resolve) => {
        releaseParty = resolve;
      });
      await page.route("**/api/v1/catalog/entry-parties?**", async (route) => {
        if (
          new URL(route.request().url()).searchParams.get("party_id") ===
          f.party.id
        ) {
          partyLoads++;
          if (partyLoads === 1) await partyHold;
        }
        await route.continue();
      });
      await nextButton(editor).click();
      await expect.poll(() => attempts.length).toBe(1);
      await expect(nextButton(editor)).toBeDisabled();
      await expect(
        editor.getByRole("checkbox", { name: "沿用表头", exact: true }),
      ).toBeDisabled();
      await page.keyboard.press("Control+Shift+Enter");
      expect(page.url()).toBe(originalUrl);
      release();
      await expect(page).not.toHaveURL(originalUrl);
      await expect(editor.locator(".editor-actions")).toContainText(
        "草稿已保存",
      );
      await expect(nextButton(editor)).toBeEnabled();
      await expect.poll(() => partyLoads).toBeGreaterThan(0);
      releaseParty();
      const nextId = draftId(page);
      expect(nextId).not.toBe(originalId);
      await expect(
        page.locator(".editor-tab:not(.module-tab):not(.record-tab)"),
      ).toHaveCount(1);
      await expect(editor.locator(".editor-continuation")).toContainText(
        "明细与备注已清空",
      );
      await expect(
        editor.getByRole("combobox", { name: "商品", exact: true }),
      ).toBeFocused();
      await page.keyboard.press("Escape");
      const original = await read(
        context,
        `/api/v1/document-drafts/${originalId}`,
      );
      const continued = await read(
        context,
        `/api/v1/document-drafts/${nextId}`,
      );
      expect(original.state).toBe("CONVERTED");
      expect(continued.state).toBe("EDITING");
      expect(continued.entity_id).toBe(f.entity);
      expect(continued.source_order_id).toBeNull();
      expect(continued.source_version).toBeNull();
      expect(continued.title).toBe("");
      expect(continued.content.reason).toBe("");
      expect(continued.content.warehouse_id).toBe(f.warehouse);
      expect(continued.content[purchase ? "supplier_id" : "customer_id"]).toBe(
        f.party.id,
      );
      expect(continued.content[purchase ? "customer_id" : "supplier_id"]).toBe(
        "",
      );
      if (!purchase) {
        expect(continued.content.delivery_address).toBe("连续开单虚拟地址");
        expect(continued.content.minimum_shelf_days).toBe("45");
        expect(continued.content.credit_days).toBe("12");
      }
      expect(continued.content.lines).toHaveLength(1);
      expect(continued.content.lines[0]).toMatchObject({
        sku_id: "",
        packaging_id: "",
        qty: "",
        unit_price: "",
        tax_rate: "0.13",
      });
      expect(continued.content.lines[0].client_key).not.toBe(originalKey);
      const saved = await read(
        context,
        `${endpoint}/${original.converted_order_id}`,
      );
      expect(saved.lines).toHaveLength(2);
      expect(saved.lines[0].unit_price).toBe("0.335001");
      expect(attempts).toHaveLength(1);
      await page.screenshot({
        path: `../../artifacts/entry-next-${purchase ? "purchase" : "sale"}.png`,
        animations: "disabled",
      });
      await editor
        .getByRole("button", { name: "查看上一单", exact: true })
        .click();
      await expect(page).toHaveURL(
        new RegExp(`order=${original.converted_order_id}`),
      );
      await expect(
        page.getByRole("button", {
          name: purchase ? "修改采购单" : "修改销售单",
          exact: true,
        }),
      ).toBeVisible();
      await page
        .getByRole("navigation", { name: "工作标签" })
        .getByRole("button", {
          name: new RegExp(`${action} · ${nextId.slice(0, 4)}`),
        })
        .click();
      await page.reload();
      await expect(nextButton(editor)).toBeEnabled();
      await expect(
        editor.getByRole("combobox", {
          name: purchase ? "供应商" : "客户",
          exact: true,
        }),
      ).toHaveValue(f.customer);
      await expect(editor.getByLabel("草稿备注", { exact: true })).toHaveValue(
        "",
      );
      await fill(page, f, purchase);
      await editor
        .getByRole("checkbox", { name: "沿用表头", exact: true })
        .uncheck();
      const secondUrl = page.url();
      await editor
        .getByLabel(qtyLabel, { exact: true })
        .press("Control+Shift+Enter");
      await expect(page).not.toHaveURL(secondUrl);
      await expect(editor.locator(".editor-actions")).toContainText(
        "草稿已保存",
      );
      await expect(nextButton(editor)).toBeEnabled();
      await expect(
        editor.getByRole("checkbox", { name: "沿用表头", exact: true }),
      ).not.toBeChecked();
      const blank = await read(
        context,
        `/api/v1/document-drafts/${draftId(page)}`,
      );
      expect(blank.content).toMatchObject({
        supplier_id: "",
        customer_id: "",
        warehouse_id: "",
        delivery_address: "",
        minimum_shelf_days: "0",
        credit_days: "30",
        reason: "",
      });
      expect(blank.content.lines).toHaveLength(1);
      expect(blank.content.lines[0].sku_id).toBe("");
      await expect(
        editor.getByRole("combobox", {
          name: purchase ? "供应商" : "客户",
          exact: true,
        }),
      ).toBeFocused();
      await nextButton(editor).click(); // Required headers still block native submission.
      expect(draftId(page)).toBe(blank.id);
      expect(attempts).toHaveLength(2);
      await page.setViewportSize({ width: 390, height: 844 });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      await page.screenshot({
        path: `../../artifacts/entry-next-${purchase ? "purchase" : "sale"}-mobile.png`,
        fullPage: true,
        animations: "disabled",
      });
      expect(faults).toEqual([]);
    } finally {
      release();
      releaseParty();
      await reviewer.close();
    }
  });
}

test("save rejection keeps input; a failed next draft is recoverable without resubmitting the saved purchase", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(120000);
  const owner = await wholesale(context, "demo.owner"),
    reviewer = await browser.newContext();
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    const f = await stockedCustomer(context, owner, reviewer, checker);
    await page.goto("/");
    await nav(page, "采购入库");
    await page
      .getByRole("button", { name: "新建采购订单", exact: true })
      .click();
    const editor = await fill(page, f);
    const originalId = draftId(page),
      originalUrl = page.url();
    let reject = true,
      failNext = true,
      orders = 0;
    const nextAttempts: { id: string; key: string | undefined }[] = [];
    await page.route("**/api/v1/purchase-orders", async (route) => {
      if (route.request().method() !== "POST") return route.continue();
      orders++;
      if (reject)
        return route.fulfill({
          status: 422,
          json: {
            error: { code: "TEST_REJECTED", message: "模拟业务校验失败" },
          },
        });
      return route.continue();
    });
    await page.route("**/api/v1/document-drafts/*", async (route) => {
      const id = new URL(route.request().url()).pathname.split("/").pop()!;
      if (route.request().method() !== "POST" || id === originalId)
        return route.continue();
      nextAttempts.push({
        id,
        key: route.request().headers()["idempotency-key"],
      });
      if (failNext) return route.abort("failed");
      return route.continue();
    });
    await nextButton(editor).click();
    await expect(editor.getByRole("alert")).toContainText("模拟业务校验失败");
    expect(page.url()).toBe(originalUrl);
    await expect(editor.getByLabel("采购数量", { exact: true })).toHaveValue(
      "2",
    );
    await expect(nextButton(editor)).toBeEnabled();
    expect(nextAttempts).toHaveLength(0);
    reject = false;
    await nextButton(editor).click();
    await expect(page).not.toHaveURL(originalUrl);
    await expect(
      page
        .locator(".el-message")
        .filter({ hasText: "上一单已保存；下一张草稿" }),
    ).toBeVisible();
    const failedId = draftId(page);
    await expect(editor.getByRole("alert")).toBeVisible();
    const original = await read(
      context,
      `/api/v1/document-drafts/${originalId}`,
    );
    expect(original.state).toBe("CONVERTED");
    const saved = await read(
      context,
      `/api/v1/purchase-orders/${original.converted_order_id}`,
    );
    expect(saved.lines).toHaveLength(1);
    await page.reload();
    await expect(nextButton(editor)).toBeEnabled();
    expect(draftId(page)).toBe(failedId);
    await expect(
      editor.getByRole("combobox", { name: "供应商", exact: true }),
    ).toHaveValue(f.customer);
    failNext = false;
    await editor.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect(editor.locator(".editor-actions")).toContainText("草稿已保存");
    const restored = await read(context, `/api/v1/document-drafts/${failedId}`);
    expect(restored.content.supplier_id).toBe(f.party.id);
    expect(restored.content.lines[0].sku_id).toBe("");
    expect(nextAttempts.length).toBeGreaterThanOrEqual(2);
    expect(new Set(nextAttempts.map((a) => a.key)).size).toBe(1);
    expect(new Set(nextAttempts.map((a) => a.id)).size).toBe(1);
    expect(orders).toBe(2); // One definite rejection, then one successful business write.
  } finally {
    await reviewer.close();
  }
});

test("an uncertain save and new never starts another draft and recovery confirms the same order", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(90000);
  const owner = await wholesale(context, "demo.owner"),
    reviewer = await browser.newContext();
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    const f = await stockedCustomer(context, owner, reviewer, checker);
    await page.goto("/");
    await nav(page, "采购入库");
    await page
      .getByRole("button", { name: "新建采购订单", exact: true })
      .click();
    const editor = await fill(page, f);
    const originalUrl = page.url(),
      id = draftId(page);
    const attempts: { key: string | undefined; body: string | null }[] = [];
    let original: any,
      newDraftWrites = 0;
    await page.route("**/api/v1/document-drafts/*", async (route) => {
      if (
        route.request().method() === "POST" &&
        !route.request().url().endsWith(id)
      )
        newDraftWrites++;
      return route.continue();
    });
    await page.route("**/api/v1/purchase-orders", async (route) => {
      if (route.request().method() !== "POST") return route.continue();
      attempts.push({
        key: route.request().headers()["idempotency-key"],
        body: route.request().postData(),
      });
      const response = await route.fetch({ maxRetries: 0 });
      expect(response.status()).toBe(200);
      if (!original) {
        original = (await response.json()).data;
        return route.abort("failed");
      }
      expect((await response.json()).data).toEqual(original);
      return route.fulfill({ response });
    });
    await nextButton(editor).click();
    const pending = page.getByRole("region", {
      name: "待确认提交",
      exact: true,
    });
    await expect(
      pending.getByRole("button", { name: "继续原提交", exact: true }),
    ).toBeEnabled();
    expect(page.url()).toBe(originalUrl);
    await expect(nextButton(editor)).toBeDisabled();
    expect(newDraftWrites).toBe(0);
    await page.reload();
    await expect(pending).toBeVisible();
    await pending
      .getByRole("button", { name: "继续原提交", exact: true })
      .click();
    await expect(page).toHaveURL(new RegExp(`order=${original.id}`));
    await expect(pending).toHaveCount(0);
    expect(attempts).toHaveLength(2);
    expect(attempts[1]).toEqual(attempts[0]);
    expect(newDraftWrites).toBe(0);
    const record = await read(context, `/api/v1/document-drafts/${id}`);
    expect(record.state).toBe("CONVERTED");
    expect(record.converted_order_id).toBe(original.id);
    await expect(
      page.locator(".editor-tab:not(.module-tab):not(.record-tab)"),
    ).toHaveCount(0);
  } finally {
    await reviewer.close();
  }
});
