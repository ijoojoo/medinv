import { test, expect } from "@playwright/test";
import { wholesale, nav, read } from "./business-helpers";

test("custom date draft uses Chinese calendar and stable slots without publishing test configuration", async ({
  page,
  context,
}) => {
  await wholesale(context, "demo.owner");
  const schema = await read(context, "/api/v1/catalog/master/schema");
  await page.goto("/");
  await nav(page, "商品字段设置");
  await page.getByRole("button", { name: "字段目录", exact: true }).click();
  const key = "custom_date_" + Date.now().toString(36);
  await page.getByPlaceholder("custom_英文稳定键", { exact: true }).fill(key);
  await page
    .getByPlaceholder("自定义属性名称", { exact: true })
    .fill("资料复核日期验收");
  await page
    .locator(".master-search select")
    .filter({ has: page.locator('option[value="date"]') })
    .selectOption("date");
  await page
    .getByRole("button", { name: "添加自定义字段", exact: true })
    .click();
  await expect(page.locator(".master-list-table")).toContainText(key);
  await page
    .getByRole("button", { name: "跨品类布局预览", exact: true })
    .click();
  const preview = page.getByRole("dialog", {
    name: "跨品类固定布局预览",
    exact: true,
  });
  const slot = preview.locator(`[data-field="${key}"]`);
  const date = slot.getByRole("textbox", {
    name: "资料复核日期验收",
    exact: true,
  });
  const height = (await slot.boundingBox())!.height;
  await date.fill("20260230");
  await date.press("Enter");
  await expect(date).toHaveValue("20260230");
  await expect(date).toHaveAttribute("aria-invalid", "true");
  await expect(slot).toContainText("日期不存在");
  expect((await slot.boundingBox())!.height).toBe(height);
  await date.fill("20240229");
  await date.press("Enter");
  await expect(date).toHaveValue("2024-02-29");
  await expect(date).toHaveAttribute("aria-invalid", "false");
  await date.press("Alt+ArrowDown");
  const calendar = page.locator(".date-popover:visible");
  await expect(calendar).toContainText("2024年2月");
  await calendar.locator('[data-day="2024-02-29"]').press("Escape");
  await expect(date).toBeFocused();
  await date.fill("");
  await date.press("Enter");
  await expect(date).toHaveAttribute("aria-invalid", "false");
  await preview.locator(".el-dialog__headerbtn").click();
  await page
    .getByRole("button", { name: "关闭商品字段设置", exact: true })
    .click();
  await page
    .getByRole("dialog", { name: "关闭前保存修改", exact: true })
    .getByRole("button", { name: "放弃修改", exact: true })
    .click();
  const after = await read(context, "/api/v1/catalog/master/schema");
  expect(after.revision).toBe(schema.revision);
  expect(after.payload.fields.some((f: { key: string }) => f.key === key)).toBe(
    false,
  );
});
