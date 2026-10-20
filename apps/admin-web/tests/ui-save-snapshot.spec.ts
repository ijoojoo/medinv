import { test, expect } from "@playwright/test";
import { wholesale, nav } from "./business-helpers";

test("edits made during close-save remain dirty and keep their page open", async ({
  page,
  context,
}) => {
  await wholesale(context, "demo.owner");
  await page.goto("/");
  await nav(page, "商品字段设置");
  const field = page
    .locator(".master-list-table input[placeholder]:not(:disabled)")
    .first();
  await expect(field).toBeVisible();
  const original = await field.inputValue();
  await field.fill(original + "待存");
  let release!: () => void, received!: () => void;
  const waiting = new Promise<void>((resolve) => (release = resolve)),
    started = new Promise<void>((resolve) => (received = resolve));
  let sent = "";
  await page.route("**/api/v1/catalog/master/schema/draft", async (route) => {
    sent = route.request().postData()!;
    const response = await route.fetch();
    received();
    await waiting;
    await route.fulfill({ response });
  });
  const close = page
    .getByRole("navigation", { name: "工作标签" })
    .getByRole("button", { name: "关闭商品字段设置", exact: true });
  await close.click();
  await page
    .getByRole("dialog", { name: "关闭前保存修改" })
    .getByRole("button", { name: "保存后关闭" })
    .click();
  await started;
  await field.fill(original + "后续");
  release();
  await expect(
    page.getByRole("button", { name: "保存草稿并预览影响", exact: true }),
  ).toBeEnabled();
  await expect(field).toHaveValue(original + "后续");
  await expect(close).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "工作标签" }).getByLabel("未保存"),
  ).toBeVisible();
  expect(sent).toContain(original + "待存");
  expect(sent).not.toContain(original + "后续");
  await close.click();
  await page
    .getByRole("dialog", { name: "关闭前保存修改" })
    .getByRole("button", { name: "放弃修改" })
    .click();
  await expect(close).toHaveCount(0);
});
