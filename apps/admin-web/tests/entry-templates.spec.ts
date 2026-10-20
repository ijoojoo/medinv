import { read, command, entryTemplatePath } from "./entry-template-helpers";
import { test, expect } from "@playwright/test";
import {
  wholesale,
  stockedCustomer,
  nav,
  chooseEntrySku,
} from "./business-helpers";
import {
  defaultEntryColumns,
  normalizeEntryColumns,
  formatEntryNumber,
  entryColumnWidths,
} from "../src/documentEntry";

const defaults = () => ({
  schema_version: 2,
  columns: defaultEntryColumns().map(({ required, editable, ...c }) => c),
});

test("templates preserve exact values, migrate old preferences and bound widths", () => {
  expect(
    formatEntryNumber("90071992547409.999999", { decimals: 2, grouping: true }),
  ).toBe("90,071,992,547,410.00");
  expect(formatEntryNumber("1.234567", { decimals: 6, grouping: false })).toBe(
    "1.234567",
  );
  expect(formatEntryNumber("0.005", { decimals: 2, grouping: false })).toBe(
    "0.01",
  );
  const migrated = normalizeEntryColumns([
    { key: "qty", visible: false, tab_stop: false },
    { key: "qty" },
    { key: "manufacturer_name", visible: true },
    { key: "script", label: "invalid" },
  ]);
  expect(migrated).toHaveLength(8);
  expect(migrated[0]).toMatchObject({
    key: "qty",
    visible: true,
    tab_stop: true,
  });
  expect(migrated[1]).toMatchObject({
    key: "manufacturer_name",
    visible: true,
  });
  const columns = normalizeEntryColumns([
    { key: "product", width_mode: "weight", width: 2 },
    { key: "qty", width_mode: "weight", width: 1 },
  ]).filter((c) => c.visible);
  const widths = entryColumnWidths(columns, 1600);
  expect(widths.reduce((s, n) => s + n, 124)).toBeCloseTo(1600);
  expect(entryColumnWidths(columns, 320)[0]).toBe(220);
});

test("field templates retain rows, stage keyboard choices, protect precision and survive a fresh session", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(150000);
  const owner = await wholesale(context, "demo.owner");
  const path = entryTemplatePath(true, owner.entities[0].id),
    original = await read(context, path);
  await command(context, owner, path, {
    expected_version: original.version,
    template: defaults(),
  });
  const reviewer = await browser.newContext();
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    const f = await stockedCustomer(context, owner, reviewer, checker);
    await page.goto("/");
    await nav(page, "采购入库");
    await page
      .getByRole("button", { name: "新建采购订单", exact: true })
      .click();
    const editor = page.getByRole("region", {
        name: "新建采购单",
        exact: true,
      }),
      rows = editor.locator(".entry-row");
    await chooseEntrySku(rows.first(), f.product.sku_id);
    const qty = rows.first().getByLabel("采购数量", { exact: true }),
      price = rows.first().getByLabel("含税单价（元）", { exact: true }),
      tax = rows.first().getByLabel("税率（小数）", { exact: true });
    await qty.fill("1000");
    await qty.press("Enter");
    await expect(price).toBeFocused();
    const priceCell = rows.first().locator('[data-col="price"]');
    expect((await priceCell.boundingBox())!.width).toBeCloseTo(130, 0);
    await price.fill("1.234567");
    await price.press("Enter");
    await expect(rows).toHaveCount(2);
    await expect(
      rows.nth(1).getByRole("combobox", { name: "商品", exact: true }),
    ).toBeFocused();
    const rowKey = await rows.first().getAttribute("data-row-key");
    await editor.getByRole("button", { name: "列设置", exact: true }).click();
    const settings = page.getByRole("dialog", {
      name: "配置单据明细列",
      exact: true,
    });
    await settings
      .getByLabel("含税单价 显示名称", { exact: true })
      .fill("采购价");
    await settings
      .getByLabel("含税单价 小数位", { exact: true })
      .selectOption("2");
    await settings.getByLabel("数量 小数位", { exact: true }).selectOption("2");
    await settings
      .getByRole("checkbox", { name: "数量 千分位", exact: true })
      .check();

    await settings
      .getByLabel("商品 宽度方式", { exact: true })
      .selectOption("weight");
    await settings.getByLabel("商品 宽度", { exact: true }).fill("2");
    await settings
      .getByLabel("税率 格式", { exact: true })
      .selectOption("percent");
    await settings
      .getByLabel("数量 拖动排序", { exact: true })
      .dragTo(settings.locator('[data-field="pack"]'));
    await settings
      .getByRole("checkbox", { name: "交易单位", exact: true })
      .uncheck();
    const saving = page.waitForResponse(
      (r) => r.url().endsWith(path) && r.request().method() === "POST",
    );
    await settings
      .getByRole("button", { name: "保存配置", exact: true })
      .click();
    expect((await saving).status()).toBe(200);
    await expect(settings).not.toBeVisible();
    const configured = await read(context, path);
    expect(
      configured.template.columns.findIndex((c: any) => c.key === "qty"),
    ).toBeLessThan(
      configured.template.columns.findIndex((c: any) => c.key === "pack"),
    );
    await expect(rows.first()).toHaveAttribute("data-row-key", rowKey!);
    await expect(price).toHaveValue("1.23");
    await expect(qty).toHaveValue("1,000.00");
    await expect(
      editor.getByRole("columnheader", { name: "采购价（含税）", exact: true }),
    ).toBeVisible();
    await expect(tax).toHaveValue("13%");
    await tax.focus();
    await expect(tax).toHaveValue("0.13");
    await tax.press("ArrowDown");
    await expect(tax).toHaveValue("0.13");
    await expect(
      page.getByRole("button", { name: "9%", exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
    await tax.press("Escape");
    await expect(tax).toHaveValue("0.13");
    await tax.press("ArrowDown");
    await tax.press("Enter");
    await expect(tax).toHaveValue("9%");
    await expect(rows).toHaveCount(2);
    await price.focus();
    await expect(price).toHaveValue("1.234567");
    await price.press("Enter");
    await expect(price).toHaveValue("1.23");
    // Replace a blurred, formatted number without a separate focus/wait step.
    await price.fill("1.234567");
    await price.press("Enter");
    await expect(price).toHaveValue("1.23");
    // A hidden invalid tax must be revealed and focused on row exit.
    await tax.fill("9");
    await editor.getByRole("button", { name: "列设置", exact: true }).click();
    await settings
      .getByRole("checkbox", { name: "税率", exact: true })
      .uncheck();
    await settings
      .getByRole("button", { name: "保存配置", exact: true })
      .click();
    await expect(settings).not.toBeVisible();
    await expect(rows.first().locator('[data-col="tax"]')).toHaveCount(0);
    await expect(rows.first()).toContainText("税率 9（小数）");
    await price.press("Enter");
    await expect(tax).toBeFocused();
    await expect(editor).toContainText("临时展开字段");
    await tax.fill("0.06");
    await tax.press("Tab");
    await editor.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect(editor.locator(".editor-actions")).toContainText("草稿已保存");
    const draft = await read(
      context,
      "/api/v1/document-drafts/" +
        new URL(page.url()).pathname.split("/").pop(),
    );
    expect(draft.content.lines[0]).toMatchObject({
      qty: "1000",
      unit_price: "1.234567",
      tax_rate: "0.06",
    });
    await page.screenshot({
      path: "../../artifacts/entry-template-configured.png",
      animations: "disabled",
    });
    const fresh = await browser.newContext();
    try {
      await wholesale(fresh, "demo.owner");
      const other = await fresh.newPage();
      await other.goto(page.url());
      const reopened = other.getByRole("region", {
        name: "新建采购单",
        exact: true,
      });
      await expect(
        reopened.getByRole("columnheader", {
          name: "采购价（含税）",
          exact: true,
        }),
      ).toBeVisible();
      await expect(
        reopened.getByLabel("含税单价（元）", { exact: true }).first(),
      ).toHaveValue("1.23");
      await expect(reopened.locator('.entry-row [data-col="tax"]')).toHaveCount(
        0,
      );
    } finally {
      await fresh.close();
    }
  } finally {
    const latest = await read(context, path);
    await command(context, owner, path, {
      expected_version: latest.version,
      template: original.template || defaults(),
    });
    await reviewer.close();
  }
});

test("template saves retry identical requests after response loss and reject stale windows", async ({
  page,
  context,
}) => {
  test.setTimeout(90000);
  const owner = await wholesale(context, "demo.owner"),
    path = entryTemplatePath(true, owner.entities[0].id);
  const original = await read(context, path);
  await command(context, owner, path, {
    expected_version: original.version,
    template: defaults(),
  });
  try {
    await page.goto("/");
    await nav(page, "采购入库");
    await page
      .getByRole("button", { name: "新建采购订单", exact: true })
      .click();
    const editor = page.getByRole("region", {
      name: "新建采购单",
      exact: true,
    });
    await editor.getByRole("button", { name: "列设置", exact: true }).click();
    const settings = page.getByRole("dialog", {
      name: "配置单据明细列",
      exact: true,
    });
    await settings
      .getByLabel("数量 显示名称", { exact: true })
      .fill("订购数量");
    let lose = true;
    const keys: string[] = [];
    await page.route(
      (url) => url.pathname + url.search === path,
      async (route) => {
        if (route.request().method() !== "POST") return route.continue();
        keys.push(route.request().headers()["idempotency-key"]!);
        if (lose) {
          await route.fetch();
          return route.abort("failed");
        }
        return route.continue();
      },
    );
    await settings
      .getByRole("button", { name: "保存配置", exact: true })
      .click();
    await expect(settings.getByRole("alert")).toBeVisible();
    await expect(
      settings.getByLabel("数量 显示名称", { exact: true }),
    ).toBeDisabled();
    lose = false;
    await settings
      .getByRole("button", { name: "继续确认保存", exact: true })
      .click();
    await expect(settings).not.toBeVisible();
    expect(new Set(keys).size).toBe(1);
    await editor.getByRole("button", { name: "列设置", exact: true }).click();
    const remote = await read(context, path);
    await command(context, owner, path, {
      expected_version: remote.version,
      template: defaults(),
    });
    await settings
      .getByLabel("数量 显示名称", { exact: true })
      .fill("旧窗口数量");
    await settings
      .getByRole("button", { name: "保存配置", exact: true })
      .click();
    await expect(settings.getByRole("alert")).toContainText("其他窗口");
    await expect(
      settings.getByLabel("数量 显示名称", { exact: true }),
    ).toHaveValue("旧窗口数量");
    await settings
      .getByRole("button", { name: "重新读取", exact: true })
      .click();
    await expect(
      settings.getByLabel("数量 显示名称", { exact: true }),
    ).toHaveValue("数量");
    await page.screenshot({
      path: "../../artifacts/entry-template-settings.png",
      animations: "disabled",
    });
    await page.setViewportSize({ width: 390, height: 844 });
    const bounds = await settings.boundingBox();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
    await settings
      .getByRole("button", { name: "保存配置", exact: true })
      .scrollIntoViewIfNeeded();
    await expect(
      settings.getByRole("button", { name: "保存配置", exact: true }),
    ).toBeInViewport();
    await page.screenshot({
      path: "../../artifacts/entry-template-settings-mobile.png",
      animations: "disabled",
    });
  } finally {
    const latest = await read(context, path);
    await command(context, owner, path, {
      expected_version: latest.version,
      template: original.template || defaults(),
    });
  }
});
