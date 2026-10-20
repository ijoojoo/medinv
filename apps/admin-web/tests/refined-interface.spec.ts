import { test, expect } from "@playwright/test";
import { wholesale, nav, read } from "./business-helpers";
import { presetRange } from "../src/datePresets";

test("calendar presets handle leap months, cross-year weeks and quarters inclusively", () => {
  expect(presetRange("lastMonth", "2024-03-01")).toEqual({
    date_from: "2024-02-01",
    date_to: "2024-02-29",
  });
  expect(presetRange("lastWeek", "2026-01-01")).toEqual({
    date_from: "2025-12-22",
    date_to: "2025-12-28",
  });
  expect(presetRange("week", "2026-01-01")).toEqual({
    date_from: "2025-12-29",
    date_to: "2026-01-01",
  });
  expect(presetRange("lastQuarter", "2026-01-01")).toEqual({
    date_from: "2025-10-01",
    date_to: "2025-12-31",
  });
  expect(presetRange("quarter", "2026-10-19")).toEqual({
    date_from: "2026-10-01",
    date_to: "2026-10-19",
  });
  expect(presetRange("7", "2026-01-01")).toEqual({
    date_from: "2025-12-26",
    date_to: "2026-01-01",
  });
  expect(presetRange("lastYear", "2026-01-01")).toEqual({
    date_from: "2025-01-01",
    date_to: "2025-12-31",
  });
  expect(presetRange("all")).toEqual({ date_from: "", date_to: "" });
});

test("layout settings retain open input, single dictionary border, responsive navigation and preferences", async ({
  page,
  context,
}) => {
  test.setTimeout(120000);
  await wholesale(context, "demo.owner");
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(page.getByRole("region", { name: "业务工作台" })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "刷新业务待办", exact: true }),
  ).toBeEnabled();
  await expect(page.locator(".el-loading-mask:visible")).toHaveCount(0);
  await page.screenshot({
    path: "../../outputs/refined-ui/工作台-横向.png",
    fullPage: true,
  });
  await nav(page, "商品档案");
  await page
    .getByRole("button", { name: "新建商品 / 项目", exact: true })
    .click();
  const schema = await read(context, "/api/v1/catalog/master/schema");
  const node = schema.payload.nodes.find((n: any) => n.node_code === "drug");
  const category = page.getByRole("dialog", { name: "明确选择建档品类" });
  await category.getByLabel("新建商品品类").selectOption(node.node_id);
  await category.getByRole("button", { name: "进入建档" }).click();
  const manufacturer = page.locator('[data-field="manufacturer"] input');
  await manufacturer.fill("切换界面保留的输入");
  for (const field of [
    "manufacturer",
    "regulatory_subject",
    "product_certificates",
  ]) {
    const input = page.locator(`[data-field="${field}"] input`);
    await input.focus();
    await expect(input).toHaveCSS("border-top-width", "0px");
    await expect(input).toHaveCSS("box-shadow", "none");
    await expect(input).toHaveCSS("outline-style", "none");
    expect(
      await input
        .locator("..")
        .evaluate((el) => getComputedStyle(el).boxShadow),
    ).not.toBe("none");
  }
  await page.locator(".master-editor .master-toolbar h2").click();
  await expect(
    page.locator(".master-reference-candidates:visible"),
  ).toHaveCount(0);
  await page.screenshot({
    path: "../../outputs/refined-ui/字典输入-单层边框.png",
    fullPage: true,
  });
  const settings = page.getByRole("button", {
    name: "界面与操作设置",
    exact: true,
  });
  await settings.click();
  await page.getByRole("button", { name: /侧栏工作区/ }).click();
  await expect(page.locator("html")).toHaveAttribute("data-layout", "sidebar");
  await page.getByRole("button", { name: "清朗蓝", exact: true }).click();
  await nav(page, "商品档案");
  await expect(manufacturer).toHaveValue("切换界面保留的输入");
  const primary = page.getByRole("navigation", {
    name: "主要功能",
    exact: true,
  });
  await primary
    .getByRole("button", { name: "企业工作台", exact: true })
    .click();
  await expect(page.getByRole("region", { name: "业务工作台" })).toBeVisible();
  await expect(page.locator(".dashboard-aside")).toContainText("商品档案");
  await expect(
    page.getByRole("button", { name: "刷新业务待办", exact: true }),
  ).toBeEnabled();
  await expect(page.locator(".el-loading-mask:visible")).toHaveCount(0);
  await page.screenshot({
    path: "../../outputs/refined-ui/工作台-侧栏.png",
    fullPage: true,
  });
  for (const width of [1440, 850, 480]) {
    await page.setViewportSize({ width, height: 900 });
    const contentBox = (await page.locator(".main-shell").boundingBox())!;
    expect(contentBox.x + contentBox.width).toBeLessThanOrEqual(width);
    const asideBox = (await page.locator(".dashboard-aside").boundingBox())!;
    expect(asideBox.x + asideBox.width).toBeLessThanOrEqual(width);
    await primary.getByRole("button", { name: "采购", exact: true }).click();
    const popup = page.getByRole("navigation", {
      name: "采购功能",
      exact: true,
    });
    await expect(popup).toBeVisible();
    const box = (await popup.boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(width);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.keyboard.press("Escape");
    await expect(popup).not.toBeVisible();
  }
  // Reload from the dashboard does not submit or discard any server record.
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-layout", "sidebar");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "blue");
  await settings.click();
  await page.getByRole("button", { name: /横向工作区/ }).click();
  await nav(page, "采购入库");
  await expect(page.getByRole("form", { name: "单据查询" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("party lookup searches remotely, chooses with keyboard, clears stale selection and retains failures", async ({
  page,
  context,
}) => {
  const current = await wholesale(context, "demo.owner");
  const choices = await read(
    context,
    `/api/v1/catalog/entry-parties?entity_id=${current.entities[0].id}&channel=purchase`,
  );
  expect(choices.items.length).toBeGreaterThan(0);
  const party = choices.items[0];
  await page.goto("/");
  await nav(page, "采购入库");
  await page.getByRole("button", { name: "新建采购订单", exact: true }).click();
  const editor = page.locator(".document-editor:visible");
  const input = editor.getByRole("combobox", { name: "供应商", exact: true });
  await expect(input).toHaveJSProperty("tagName", "INPUT");
  await input.fill(party.code);
  const row = page.locator(
    `.party-lookup-popover:visible [data-party="${party.id}"]`,
  );
  await expect(row).toBeVisible();
  await input.press("ArrowUp");
  await expect(row).toHaveAttribute("aria-selected", "true");
  await input.press("Enter");
  await expect(input).toHaveValue(party.name);
  await expect(page.locator(".party-lookup-popover:visible")).toHaveCount(0);
  await editor.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(editor.locator(".editor-actions")).toContainText("草稿已保存");
  const id = new URL(page.url()).pathname.split("/").pop()!;
  expect(
    (await read(context, `/api/v1/document-drafts/${id}`)).content.supplier_id,
  ).toBe(party.id);
  await input.fill("尚未建档的输入");
  await expect(page.locator(".party-lookup-popover:visible")).toContainText(
    "未找到匹配单位",
  );
  await input.press("Escape");
  await editor.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(editor.locator(".editor-actions")).toContainText("草稿已保存");
  expect(
    (await read(context, `/api/v1/document-drafts/${id}`)).content.supplier_id,
  ).toBe("");
  expect(
    await input.evaluate((el: HTMLInputElement) => el.validity.valid),
  ).toBe(false);
  await page.route("**/api/v1/catalog/entry-parties?**", (route) =>
    route.abort("failed"),
  );
  await input.fill(party.code);
  await expect(page.locator(".party-lookup-popover:visible")).toContainText(
    "检索失败，输入已保留",
  );
  await expect(input).toHaveValue(party.code);
  await page.unroute("**/api/v1/catalog/entry-parties?**");
  await page
    .locator(".party-lookup-popover:visible")
    .getByRole("button", { name: "重试", exact: true })
    .click();
  await expect(row).toBeVisible();
  await row.click();
  await expect(input).toHaveValue(party.name);
  // A late response from a prior keyword must not replace the current result.
  let release!: () => void;
  let arrived!: () => void;
  let finished!: () => void;
  const delayed = new Promise<void>((r) => (release = r));
  const received = new Promise<void>((r) => (arrived = r));
  const handled = new Promise<void>((r) => (finished = r));
  await page.route("**/api/v1/catalog/entry-parties?**", async (route) => {
    if (
      new URL(route.request().url()).searchParams.get("search") !== party.code
    )
      return route.continue();
    const response = await route.fetch();
    arrived();
    await delayed;
    await route.fulfill({ response });
    finished();
  });
  await input.fill(party.code);
  await received;
  try {
    await input.fill("不会匹配任何单位的后续关键词");
    await expect(page.locator(".party-lookup-popover:visible")).toContainText(
      "未找到匹配单位",
    );
  } finally {
    release();
  }
  await handled;
  await expect(row).toHaveCount(0);
  await expect(input).toHaveValue("不会匹配任何单位的后续关键词");
  await page.unroute("**/api/v1/catalog/entry-parties?**");
  await input.fill(party.code);
  await expect(row).toBeVisible();
  await page.screenshot({
    path: "../../outputs/refined-ui/供应商检索.png",
    fullPage: true,
  });
});

test("collapsed invalid party filters reveal the input and clear resets unconfirmed text", async ({
  page,
  context,
}) => {
  await wholesale(context, "demo.owner");
  await page.goto("/");
  await nav(page, "采购入库");
  const form = page.getByRole("form", { name: "单据查询" });
  await expect(form.getByRole("button", { name: "查询单据" })).toBeEnabled();
  await form.getByRole("button", { name: "更多条件", exact: true }).click();
  const input = form.getByRole("combobox", { name: "筛选供应商", exact: true });
  await input.fill("未确认的筛选单位");
  await input.press("Escape");
  await form.getByRole("button", { name: "收起条件", exact: true }).click();
  let reads = 0;
  page.on("request", (r) => {
    if (new URL(r.url()).pathname === "/api/v1/purchase-orders") reads++;
  });
  await form.getByRole("button", { name: "查询单据", exact: true }).click();
  await expect(input).toBeVisible();
  await expect(input).toBeFocused();
  expect(reads).toBe(0);
  await form.getByRole("button", { name: "清除筛选", exact: true }).click();
  await expect(input).toHaveValue("");
  await expect(form.getByRole("button", { name: "查询单据" })).toBeEnabled();
  await expect.poll(() => reads).toBe(1);
  expect(
    await input.evaluate((el: HTMLInputElement) => el.validity.valid),
  ).toBe(true);
  await form
    .getByRole("combobox", { name: "快捷日期范围", exact: true })
    .selectOption("month");
  await page.locator(".main-shell").evaluate((el) => el.scrollTo(0, 0));
  await page.screenshot({
    path: "../../outputs/refined-ui/单据查询-快捷日期与更多条件.png",
    fullPage: true,
  });
});
