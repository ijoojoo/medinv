import { test, expect } from "@playwright/test";
import { wholesale, nav, read } from "./business-helpers";

test("independent drafts survive reload, keep partial search text and preserve source list", async ({
  page,
  context,
}) => {
  test.setTimeout(120000);
  const current = await wholesale(context, "demo.owner");
  const faults: string[] = [];
  page.on("pageerror", (e) => faults.push(e.message));
  await page.goto("/");
  await nav(page, "采购入库");
  const search = page.getByPlaceholder("单号、供应商名称或编码");
  await search.fill("保留筛选-草稿验收");
  await page.getByRole("button", { name: "查询单据", exact: true }).click();
  await page.getByRole("button", { name: "新建采购订单", exact: true }).click();
  const editor = page.getByRole("region", { name: "新建采购单", exact: true });
  await expect(editor).toBeVisible();
  await expect(page).toHaveURL(/\/workspace\/purchasing\/drafts\//);
  const firstUrl = page.url();
  await editor.getByLabel("草稿备注").fill("未完成的采购需求");
  await editor
    .getByRole("combobox", { name: "商品", exact: true })
    .fill("尚未选定的商品");
  await editor.getByLabel("采购数量", { exact: true }).fill("12");
  await editor.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(editor.locator(".editor-actions")).toContainText("草稿已保存");
  const did = new URL(firstUrl).pathname.split("/").pop()!;
  const record = await read(context, `/api/v1/document-drafts/${did}`);
  expect(record.content.lines[0].search_text).toBe("尚未选定的商品");
  expect(record.content.supplier_id).toBe("");
  await page.reload();
  await expect(editor.getByLabel("采购数量", { exact: true })).toHaveValue(
    "12",
  );
  await expect(
    editor.getByRole("combobox", { name: "商品", exact: true }),
  ).toHaveValue("尚未选定的商品");
  await expect(editor.getByLabel("草稿备注")).toHaveValue("未完成的采购需求");
  await editor
    .getByRole("button", { name: "返回采购记录", exact: true })
    .click();
  await expect(search).toHaveValue("保留筛选-草稿验收");
  await page.getByRole("button", { name: "新建采购订单", exact: true }).click();
  await expect(page).not.toHaveURL(firstUrl);
  await expect(
    page.locator(".editor-tab:not(.module-tab):not(.record-tab)"),
  ).toHaveCount(2);
  await expect(
    editor.filter({ visible: true }).getByLabel("采购数量", { exact: true }),
  ).toHaveValue("");
  await page
    .getByRole("navigation", { name: "工作标签" })
    .getByRole("button", {
      name: new RegExp(`新建采购单 · ${did.slice(0, 4)}`),
    })
    .click();
  const visible = page.locator(".document-editor:visible");
  await expect(visible.getByLabel("采购数量", { exact: true })).toHaveValue(
    "12",
  );
  await page.setViewportSize({ width: 1366, height: 768 });
  await page.screenshot({
    path: "../../artifacts/editor-workspace-desktop.png",
    animations: "disabled",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "../../artifacts/editor-workspace-mobile.png",
    animations: "disabled",
  });
  expect(record.entity_id).toBe(current.entities[0].id);
  expect(faults).toEqual([]);
});

test("concurrent windows cannot overwrite a newer draft; tax arrows and Enter stay usable", async ({
  page,
  context,
}) => {
  test.setTimeout(90000);
  await wholesale(context, "demo.owner");
  await page.goto("/");
  await nav(page, "采购入库");
  await page.getByRole("button", { name: "新建采购订单", exact: true }).click();
  const editor = page.getByRole("region", { name: "新建采购单", exact: true });
  await expect(editor.locator(".editor-actions")).toContainText("草稿已保存");
  const second = await context.newPage();
  await second.goto(page.url());
  const other = second.getByRole("region", { name: "新建采购单", exact: true });
  await expect(other.locator(".editor-actions")).toContainText("草稿已保存");
  await editor.getByLabel("草稿备注").fill("窗口一已存");
  await editor.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(editor.locator(".editor-actions")).toContainText("草稿已保存");
  await other.getByLabel("草稿备注").fill("窗口二保留");
  await other.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(other.getByRole("alert")).toContainText("其他窗口");
  await expect(other.getByLabel("草稿备注")).toHaveValue("窗口二保留");
  await other
    .getByRole("button", { name: "读取已存版本", exact: true })
    .click();
  await second
    .getByRole("dialog", { name: "读取已存版本" })
    .getByRole("button", { name: "读取已存版本", exact: true })
    .click();
  await expect(other.getByLabel("草稿备注")).toHaveValue("窗口一已存");
  const tax = other.getByLabel("税率（小数）", { exact: true });
  await tax.focus();
  await tax.press("ArrowDown");
  await expect(tax).toHaveValue("0.13");
  await expect(
    second.getByRole("button", { name: "9%", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await tax.press("ArrowUp");
  await expect(tax).toHaveValue("0.13");
  await tax.press("Enter");
  await expect(
    other.getByRole("combobox", { name: "商品", exact: true }),
  ).toBeFocused();
  await other.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(other.locator(".editor-actions")).toContainText("草稿已保存");
  await second.close();
});

test("failed draft saves retain input and closing can retry the same save", async ({
  page,
  context,
}) => {
  await wholesale(context, "demo.owner");
  await page.goto("/");
  await nav(page, "采购入库");
  await page.getByRole("button", { name: "新建采购订单", exact: true }).click();
  const editor = page.getByRole("region", { name: "新建采购单", exact: true });
  await expect(editor.locator(".editor-actions")).toContainText("草稿已保存");
  const id = new URL(page.url()).pathname.split("/").pop()!;
  let fail = true;
  const attempts: string[] = [];
  await page.route(`**/api/v1/document-drafts/${id}`, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    attempts.push(route.request().headers()["idempotency-key"]!);
    if (fail) return route.abort("failed");
    return route.continue();
  });
  await editor.getByLabel("草稿备注").fill(`断网仍保留-${id.slice(0, 8)}`);
  await editor.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(editor.getByRole("alert")).toBeVisible();
  await page
    .getByRole("button", { name: `关闭单据 ${id.slice(0, 4)}`, exact: true })
    .click();
  const closing = page.getByRole("dialog", {
    name: "关闭前保存修改",
    exact: true,
  });
  await expect(closing).toBeVisible();
  fail = false;
  await closing
    .getByRole("button", { name: "保存后关闭", exact: true })
    .click();
  await expect(editor).not.toBeVisible();
  expect(attempts.length).toBeGreaterThanOrEqual(2);
  expect(new Set(attempts).size).toBe(1);
  await page.getByRole("button", { name: "我的草稿", exact: true }).click();
  await page
    .getByRole("dialog", { name: "我的编辑草稿", exact: true })
    .getByRole("row")
    .filter({ hasText: `断网仍保留-${id.slice(0, 8)}` })
    .getByRole("button", { name: "继续录入" })
    .click();
  await expect(editor.getByLabel("草稿备注")).toHaveValue(
    `断网仍保留-${id.slice(0, 8)}`,
  );
});
