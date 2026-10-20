import { test, expect } from "@playwright/test";
import { wholesale, nav, read, command } from "./business-helpers";

test("all categories keep the same 15 basic slots at three widths", async ({
  page,
  context,
}) => {
  await wholesale(context, "demo.owner");
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await nav(page, "商品字段设置");
  await page
    .getByRole("button", { name: "跨品类布局预览", exact: true })
    .click();
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.addStyleTag({
    content:
      "*, *::before, *::after { animation-duration: 0s !important; transition-duration: 0s !important; }",
  });
  const dialog = page.getByRole("dialog", { name: "跨品类固定布局预览" });
  for (const width of [1440, 850, 480]) {
    await page.setViewportSize({ width, height: 1000 });
    let reference: { x: number; y: number; width: number; height: number }[] =
      [];
    for (const profile of [
      "drug",
      "herb",
      "device",
      "food",
      "health",
      "fsmp",
      "infant",
      "cosmetic",
      "daily",
      "service",
    ]) {
      await dialog
        .locator(".master-search select")
        .first()
        .selectOption(profile);
      const slots = dialog.locator('[data-tab="basic"] [data-slot]');
      await expect(slots).toHaveCount(15);
      const coords = await slots.evaluateAll((elements) =>
        elements.map((el) => {
          const r = el.getBoundingClientRect();
          const parent = el.parentElement!.getBoundingClientRect();
          return {
            x: Math.round(r.x - parent.x),
            y: Math.round(r.y - parent.y),
            width: Math.round(r.width),
            height: Math.round(r.height),
          };
        }),
      );
      if (!reference.length) reference = coords;
      else expect(coords).toEqual(reference);
      expect(new Set(coords.map((r) => r.x)).size).toBe(
        width === 1440 ? 3 : width === 850 ? 2 : 1,
      );
      if (width === 1440 && profile === "device")
        await dialog.screenshot({
          path: "../../outputs/mdm-implementation/固定槽位预览.png",
        });
    }
  }
  expect(errors).toEqual([]);
});

test("parent new requires a choice; browsing and tabs do not overwrite the actual editor", async ({
  page,
  context,
}) => {
  await wholesale(context, "demo.owner");
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await nav(page, "商品档案");
  await page
    .locator(".master-tree-label")
    .getByText("食品", { exact: true })
    .click();
  await page
    .getByRole("button", { name: "新建商品 / 项目", exact: true })
    .click();
  const choose = page.getByRole("dialog", { name: "明确选择建档品类" });
  await expect(choose.getByRole("button", { name: "进入建档" })).toBeDisabled();
  const schema = await read(context, "/api/v1/catalog/master/schema");
  const food = schema.payload.nodes.find((n: any) => n.node_code === "food");
  await choose.getByLabel("新建商品品类").selectOption(food.node_id);
  await choose.getByRole("button", { name: "进入建档" }).click();
  const suffix = Date.now().toString(36),
    name = "主数据页面验收-" + suffix;
  await page.locator("#master-B002").fill(name);
  await page.locator("#master-B004").fill("袋");
  await page.locator("#master-B005").selectOption("PREPACKAGED");
  await page.getByRole("button", { name: "运营管控", exact: true }).click();
  await page.getByLabel("品牌", { exact: true }).fill("测试品牌");
  await page.getByRole("button", { name: "基本信息", exact: true }).click();
  await expect(page.locator("#master-B002")).toHaveValue(name);
  await page
    .locator(".master-tree-label")
    .getByText("医疗器械", { exact: true })
    .click();
  await expect(page.locator("#master-B002")).toHaveValue(name);
  await expect(page.locator(".master-editor .master-toolbar")).toContainText(
    "普通食品",
  );
  await page.getByRole("button", { name: "保存资料草稿", exact: true }).click();
  await expect(page.locator(".master-state")).toContainText("待审核");
  await expect(
    page.getByRole("button", { name: "保存资料草稿", exact: true }),
  ).toBeEnabled();
  await page.screenshot({
    path: "../../outputs/mdm-implementation/商品维护.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "校验全部页签", exact: true }).click();
  await expect(page.locator(".master-issue-strip")).not.toContainText(
    "不能为空",
  );
  await page.locator("#master-B003").fill("袋装规格");
  await page.getByRole("button", { name: "返回查询", exact: true }).click();
  const leave = page.getByRole("dialog", { name: "离开当前商品" });
  await leave.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.locator("#master-B003")).toHaveValue("袋装规格");
  await page.getByRole("button", { name: "返回查询", exact: true }).click();
  await leave.getByRole("button", { name: "保存后离开", exact: true }).click();
  await expect(leave).not.toBeVisible();
  await page
    .locator(".master-tree-label")
    .getByText("全部商品与项目", { exact: true })
    .click();
  await page.getByLabel("检索商品主数据").fill(name);
  await page.getByRole("button", { name: "查询", exact: true }).click();
  await expect(page.locator(".master-list-table tbody tr")).toHaveCount(1);
  await expect(page.locator(".master-list-table")).toContainText(name);
  expect(errors).toEqual([]);
  await page.screenshot({
    path: "../../outputs/mdm-implementation/商品查询.png",
    fullPage: true,
  });
});

test("schema draft preview and outpatient CSV import use real APIs", async ({
  page,
  context,
}) => {
  const current = await wholesale(context, "demo.owner");
  const schema = await read(context, "/api/v1/catalog/master/schema");
  const service = schema.payload.nodes.find(
    (n: any) => n.node_code === "service",
  );
  const imported = await command(
    context,
    current,
    "/api/v1/catalog/master/import",
    {
      category_node_id: service.node_id,
      form_revision: schema.revision,
      content:
        "name,base_unit,regulatory_subtype,service_price\n门诊验收服务,次,OUTPATIENT,30.5",
      reason: "自动化验收",
    },
  );
  const detail = await read(
    context,
    `/api/v1/catalog/master/products/${imported.items[0].id}`,
  );
  expect(detail.skus).toEqual([]);
  expect(detail.values.service_price).toBe("30.500000");
  await page.goto("/");
  await nav(page, "商品字段设置");
  const pendingSave = page.waitForResponse(
    (r) =>
      r.url().includes("/master/schema/draft") &&
      r.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "保存草稿并预览影响", exact: true })
    .click();
  const saveResponse = await pendingSave;
  expect(saveResponse.status(), await saveResponse.text()).toBe(200);
  await page.getByRole("button", { name: "版本与发布", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "发布已预览版本", exact: true }),
  ).toBeEnabled();
  // Do not publish test-only configuration into the shared demonstration tenant.
});
