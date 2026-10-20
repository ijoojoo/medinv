import { test, expect } from "@playwright/test";
import { wholesale, nav, read, command } from "./business-helpers";

test("product query stays applied across paging/export and restores a long-list visit", async ({
  page,
  context,
}) => {
  await wholesale(context, "demo.owner");
  const initial = await read(
    context,
    "/api/v1/catalog/master/products?limit=1",
  );
  const original = initial.items[0];
  expect(original).toBeTruthy();
  // Only the long list is simulated; the opened detail comes from the real API.
  const rows = Array.from({ length: 55 }, (_, i) => ({
    ...original,
    id: i === 40 ? original.id : `list-only-${i}`,
    values: { ...original.values, name: `定位商品 ${i + 1}` },
  }));
  const queries: URLSearchParams[] = [];
  await page.route("**/api/v1/catalog/master/products?*", async (route) => {
    const q = new URL(route.request().url()).searchParams;
    queries.push(q);
    const offset = Number(q.get("offset")),
      limit = Number(q.get("limit"));
    await route.fulfill({
      json: {
        data: {
          ...initial,
          items: rows.slice(offset, offset + limit),
          total: rows.length,
          has_more: offset + limit < rows.length,
        },
      },
    });
  });
  await page.goto("/");
  await nav(page, "商品档案");
  const list = page.locator(".master-browser");
  await expect(
    list.getByRole("button", { name: "查询", exact: true }),
  ).toBeEnabled();
  const search = list.getByLabel("检索商品主数据");
  await search.fill("已查询");
  await search.press("Enter");
  await expect.poll(() => queries.at(-1)?.get("search")).toBe("已查询");
  await search.fill("尚未查询");
  await list.getByRole("button", { name: "下一页", exact: true }).click();
  await expect.poll(() => queries.at(-1)?.get("offset")).toBe("50");
  expect(queries.at(-1)?.get("search")).toBe("已查询");
  const download = page.waitForEvent("download");
  await list.getByRole("button", { name: "导出当前范围" }).click();
  await download;
  expect(queries.at(-1)?.get("search")).toBe("已查询");
  await list.getByRole("button", { name: "上一页", exact: true }).click();
  await expect(list.locator("button[data-catalog-record]")).toHaveCount(50);
  await search.press("ArrowDown");
  await expect(
    list.locator("button[data-catalog-record]").first(),
  ).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(
    list.locator("button[data-catalog-record]").nth(1),
  ).toBeFocused();
  const row = list.locator(`button[data-catalog-record="${original.id}"]`);
  await row.click();
  await expect(page.locator(".master-editor")).toBeVisible();
  await page.getByRole("button", { name: "返回查询", exact: true }).click();
  await expect(search).toHaveValue("尚未查询");
  await expect(row).toBeFocused();
  await expect(list.locator(".catalog-returned-row")).toContainText(
    "定位商品 41",
  );
  expect(
    await page.locator(".main-shell").evaluate((e) => e.scrollTop),
  ).toBeGreaterThan(400);
  await list.getByRole("button", { name: "重置", exact: true }).click();
  await expect(search).toHaveValue("");
  await expect.poll(() => queries.at(-1)?.get("search")).toBe("");
  await expect(list.locator(".catalog-returned-row")).toHaveCount(0);
});

test("product keyboard save preserves its card, retries only a failed read, and saves back to its list", async ({
  page,
  context,
}) => {
  const owner = await wholesale(context, "demo.owner");
  const schema = await read(context, "/api/v1/catalog/master/schema");
  const node = schema.payload.nodes.find((n: any) => n.node_code === "food");
  const name = `资料操作验收-${Date.now().toString(36)}`;
  const item = await command(
    context,
    owner,
    "/api/v1/catalog/master/products",
    {
      category_node_id: node.node_id,
      form_revision: schema.revision,
      expected_version: 0,
      values: { name, base_unit: "袋", regulatory_subtype: "PREPACKAGED" },
      reason: "软件验收",
    },
  );
  await page.goto("/");
  await nav(page, "商品档案");
  await expect(
    page
      .locator(".master-browser")
      .getByRole("button", { name: "查询", exact: true }),
  ).toBeEnabled();
  await page.getByLabel("检索商品主数据").fill(name);
  await page.getByLabel("检索商品主数据").press("Enter");
  await page.locator(`button[data-catalog-record="${item.id}"]`).click();
  await page.getByRole("button", { name: "运营管控", exact: true }).click();
  const brand = page.getByLabel("品牌", { exact: true });
  await brand.fill("快捷键验收品牌");
  let writes = 0,
    failRead = true;
  page.on("request", (r) => {
    if (r.url().endsWith("/catalog/master/products") && r.method() === "POST")
      writes++;
  });
  await page.route(
    `**/api/v1/catalog/master/products/${item.id}`,
    async (route) => {
      if (failRead) {
        failRead = false;
        await route.fulfill({
          status: 503,
          json: {
            error: { code: "TEST_READ_FAILURE", message: "验收读取失败" },
          },
        });
      } else await route.continue();
    },
  );
  await brand.press("Control+s");
  await expect(
    page.getByRole("button", { name: "重试读取已保存资料" }),
  ).toBeVisible();
  expect(writes).toBe(1);
  await expect(brand).toBeDisabled();
  await page.getByRole("button", { name: "重试读取已保存资料" }).click();
  await expect(brand).toBeEnabled();
  await expect(brand).toHaveValue("快捷键验收品牌");
  expect(writes).toBe(1);
  await expect(page.locator(".master-tabs button.active")).toHaveText(
    "运营管控",
  );
  await brand.fill("保存返回验收品牌");
  await page
    .getByRole("button", { name: "保存并返回列表", exact: true })
    .click();
  await expect(page.locator(".master-editor")).toHaveCount(0);
  await expect(page.getByLabel("检索商品主数据")).toHaveValue(name);
  await expect(
    page.locator(`button[data-catalog-record="${item.id}"]`),
  ).toBeFocused();
  expect(writes).toBe(2);
  const latest = await read(
    context,
    `/api/v1/catalog/master/products/${item.id}`,
  );
  expect(latest.values.brand).toBe("保存返回验收品牌");
  await expect(page.locator(".el-message:visible")).toHaveCount(0, {
    timeout: 8000,
  });
  await page.screenshot({
    path: "../../outputs/catalog-interaction/product-return.png",
    fullPage: true,
  });
});

test("supplier and customer maintenance protects card edits, keyboard saves and returns after revision", async ({
  page,
  context,
}) => {
  const owner = await wholesale(context, "demo.owner");
  const suffix = Date.now().toString(36),
    code = `CI-${suffix}`;
  const year = new Date().getFullYear();
  const item = await command(context, owner, "/api/v1/catalog/parties", {
    entity_id: owner.entities[0].id,
    code,
    name: `供销资料操作验收-${suffix}`,
    credit_code: code,
    kind: "BOTH",
    license_no: "TEST-ONLY",
    categories: ["DEVICE"],
    valid_from: `${year}-01-01`,
    valid_until: `${year + 2}-12-31`,
  });
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await nav(page, "往来单位");
  const list = page.getByRole("region", { name: "基础资料列表" });
  await list.getByLabel("业务身份").selectOption("SUPPLIER");
  await list.getByLabel("查询资料").fill(code);
  await list.getByLabel("查询资料").press("Enter");
  const row = list.locator(`button[data-catalog-record="${item.id}"]`);
  await expect(row).toBeVisible();
  await list.getByLabel("查询资料").press("ArrowDown");
  await expect(row).toBeFocused();
  await row.press("Enter");
  const detail = page.getByRole("region", { name: "资料详情", exact: true });
  await detail.getByRole("tab", { name: "经营信息", exact: true }).click();
  const operations = detail.getByRole("region", { name: "经营信息卡片" });
  await operations.getByLabel("联系人", { exact: true }).fill("业务联系验收");
  await detail.getByRole("button", { name: "← 返回资料列表" }).click();
  const leave = page.getByRole("dialog", { name: "关闭前保存修改" });
  await leave.getByRole("button", { name: "取消关闭" }).click();
  await expect(operations.getByLabel("联系人", { exact: true })).toHaveValue(
    "业务联系验收",
  );
  await operations.getByLabel("变更说明").fill("软件操作验收");
  await operations.getByLabel("变更说明").press("Control+s");
  await expect(operations).toContainText("已与当前档案同步");
  await detail.getByRole("tab", { name: "质量管控", exact: true }).click();
  await detail.getByRole("button", { name: "修订资料", exact: true }).click();
  const entry = page.getByRole("region", { name: "修订资料", exact: true });
  await entry
    .getByLabel("名称", { exact: true })
    .fill(`供销资料已修订-${suffix}`);
  await entry.getByRole("button", { name: "保存并返回列表" }).click();
  await expect(entry.getByLabel("修订原因")).toBeFocused();
  await entry.getByLabel("修订原因").fill("更正名称软件验收");
  await entry.getByLabel("修订原因").press("Control+s");
  await expect(entry).not.toBeVisible();
  await expect(detail.getByRole("tab", { name: "质量管控" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await detail.getByRole("button", { name: "修订资料", exact: true }).click();
  await entry
    .getByLabel("名称", { exact: true })
    .fill(`供销资料完成-${suffix}`);
  await entry.getByLabel("修订原因").fill("继续修订验收");
  await entry.getByRole("button", { name: "保存并返回列表" }).click();
  await expect(list).toBeVisible();
  await expect(row).toBeFocused();
  await expect(list.getByLabel("查询资料")).toHaveValue(code);
  await expect(list.getByLabel("业务身份")).toHaveValue("SUPPLIER");
  // The same dual-role identity remains accessible from the customer query.
  await list.getByLabel("业务身份").selectOption("CUSTOMER");
  await expect(row).toBeVisible();
  await row.click();
  await detail.getByRole("tab", { name: "经营信息", exact: true }).click();
  await operations.getByLabel("联系人", { exact: true }).fill("将要放弃");
  await detail.getByRole("button", { name: "← 返回资料列表" }).click();
  await leave.getByRole("button", { name: "放弃修改" }).click();
  await expect(row).toBeFocused();
  const latest = await read(context, `/api/v1/catalog/parties/${item.id}`);
  expect(latest.record.contact_name).toBe("业务联系验收");
  expect(latest.record.state).toBe("DRAFT");
  await expect(page.locator(".el-message:visible")).toHaveCount(0, {
    timeout: 8000,
  });
  await page.screenshot({
    path: "../../outputs/catalog-interaction/party-return.png",
    fullPage: true,
  });
  await list.getByRole("button", { name: "重置", exact: true }).click();
  await expect(list.getByLabel("查询资料")).toHaveValue("");
  await expect(list.getByLabel("业务身份")).toHaveValue("");
  expect(errors).toEqual([]);
});

test("party return backs up from an emptied last page and focuses the nearest remaining row", async ({
  page,
  context,
}) => {
  const owner = await wholesale(context, "demo.owner");
  const initial = await read(
    context,
    `/api/v1/catalog/parties?entity_id=${owner.entities[0].id}`,
  );
  const original = initial.items[0];
  expect(original).toBeTruthy();
  const rows = Array.from({ length: 51 }, (_, i) => ({
    ...original,
    id: i === 50 ? original.id : `read-only-party-${i}`,
    name: `长列表单位 ${i + 1}`,
  }));
  let shrink = false;
  const offsets: number[] = [];
  await page.route("**/api/v1/catalog/parties?*", async (route) => {
    const offset = Number(
      new URL(route.request().url()).searchParams.get("offset"),
    );
    offsets.push(offset);
    const visible = shrink ? rows.slice(0, 50) : rows;
    await route.fulfill({
      json: {
        data: {
          items: visible.slice(offset, offset + 50),
          has_more: offset + 50 < visible.length,
        },
      },
    });
  });
  await page.goto("/");
  await nav(page, "往来单位");
  const list = page.getByRole("region", { name: "基础资料列表" });
  await list.getByRole("button", { name: "下一页", exact: true }).click();
  const row = list.locator(`button[data-catalog-record="${original.id}"]`);
  await expect(row).toBeVisible();
  await row.click();
  const detail = page.getByRole("region", { name: "资料详情", exact: true });
  await expect(detail).toBeVisible();
  shrink = true;
  await detail.getByRole("button", { name: "← 返回资料列表" }).click();
  await expect(list.locator("button[data-catalog-record]")).toHaveCount(50);
  await expect(list.locator(".pager")).toContainText("第 1 页");
  await expect(
    list.locator("button[data-catalog-record]").first(),
  ).toBeFocused();
  expect(offsets.slice(-2)).toEqual([50, 0]);
});
