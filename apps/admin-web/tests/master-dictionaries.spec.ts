import { test, expect, type Page, type BrowserContext } from "@playwright/test";
import { wholesale, read, nav } from "./business-helpers";
import type { SchemaResponse } from "../src/productMaster";

// Browser-only choices/rules: real schema structure, no trial dictionary or product writes.
async function setup(page: Page, context: BrowserContext) {
  await wholesale(context, "demo.owner");
  const schema = (await read(
    context,
    "/api/v1/catalog/master/schema",
  )) as SchemaResponse;
  const dictionaries = schema.dictionaries!;
  const values = (active: string[], inactive: string[] = []) => [
    ...active.map((value, order) => ({ value, order, active: true })),
    ...inactive.map((value, order) => ({
      value,
      order: order + active.length,
      active: false,
    })),
  ];
  dictionaries.base_unit = {
    ...dictionaries.base_unit,
    mode: "SUGGEST",
    items: values(["盒", "次"]),
  };
  dictionaries.dosage_form = {
    ...dictionaries.dosage_form,
    mode: "SUGGEST",
    items: values(["片剂", "胶囊剂"], ["散剂"]),
  };
  dictionaries.storage_conditions = {
    ...dictionaries.storage_conditions,
    mode: "STRICT",
    items: values(["0123", "0456", "阴凉处", "12", "未知"], ["0999"]),
  };
  Object.assign(schema.payload.rules.drug.dosage_form, {
    min_length: 2,
    max_length: 2,
  });
  Object.assign(schema.payload.rules.food.storage_conditions, {
    label: "保管条件",
    min_length: 4,
    max_length: 4,
    validator: "DIGITS",
    required_when: { field: "regulatory_subtype", equals: "PREPACKAGED" },
  });
  schema.payload.rules.service.base_unit.label = "服务计价单位";
  await page.route("**/api/v1/catalog/master/schema", (route) =>
    route.fulfill({ json: { data: schema } }),
  );
  await page.route("**/api/v1/catalog/dictionaries", (route) =>
    route.fulfill({ json: { data: { items: Object.values(dictionaries) } } }),
  );
  const writes: string[] = [],
    errors: string[] = [];
  page.on("request", (r) => {
    if (r.method() !== "GET" && r.url().includes("/api/v1/catalog/"))
      writes.push(r.url());
  });
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  return { schema, dictionaries, writes, errors };
}

async function create(page: Page, schema: SchemaResponse, profile: string) {
  await nav(page, "商品档案");
  await page
    .getByRole("button", { name: "新建商品 / 项目", exact: true })
    .click();
  const choose = page.getByRole("dialog", { name: "明确选择建档品类" });
  await choose
    .getByLabel("新建商品品类")
    .selectOption(
      schema.payload.nodes.find((n) => n.node_code === profile)!.node_id,
    );
  await choose.getByRole("button", { name: "进入建档" }).click();
}

test("MDM suggestions obey length and active rules while allowing valid free text", async ({
  page,
  context,
}) => {
  const fixture = await setup(page, context);
  await create(page, fixture.schema, "drug");
  const unit = page.locator('[data-field="base_unit"]');
  await unit.locator(".dictionary-toggle").click();
  await expect(
    page.locator(".dictionary-suggestions:visible").getByRole("option"),
  ).toHaveCount(2);
  await unit.locator("input").press("Escape");
  const dosage = page.locator('[data-field="dosage_form"]');
  await dosage.locator("input").click();
  await expect(
    page.locator(".dictionary-suggestions:visible").getByRole("option"),
  ).toHaveCount(1);
  await expect(
    page.locator(".dictionary-suggestions:visible").getByRole("option"),
  ).toHaveText("片剂");
  await expect(dosage.locator("input")).toHaveAttribute("maxlength", "2");
  await expect(dosage.locator("label")).toHaveAttribute(
    "for",
    (await dosage.locator("input").getAttribute("id")) || "",
  );
  await dosage
    .getByRole("combobox", { name: "剂型", exact: true })
    .fill("粉剂");
  await page.getByRole("button", { name: "运营管控", exact: true }).click();
  // Brand has its own semantic identity, never acquires the historical trade_name dictionary.
  await expect(
    page.locator('[data-field="brand"] .dictionary-input'),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "基本信息", exact: true }).click();
  await expect(dosage.locator("input")).toHaveValue("粉剂");
  await expect(page.locator('[data-tab="basic"] [data-slot]')).toHaveCount(15);
  const border = await dosage.locator("input").evaluate((el) => ({
    input: getComputedStyle(el).borderTopWidth,
    parent: getComputedStyle(el.parentElement!).borderTopWidth,
  }));
  expect(border).toEqual({ input: "1px", parent: "0px" });
  expect(fixture.writes).toEqual([]);
  expect(fixture.errors).toEqual([]);
});

test("food strict dictionary uses aliases, conditional required and arrow selection", async ({
  page,
  context,
}) => {
  const fixture = await setup(page, context);
  await create(page, fixture.schema, "food");
  await page
    .locator('[data-field="regulatory_subtype"] select')
    .selectOption("PREPACKAGED");
  await page.getByRole("button", { name: "质量管控", exact: true }).click();
  const slot = page.locator('[data-field="storage_conditions"]');
  const select = slot.getByRole("combobox", { name: "保管条件", exact: true });
  await expect(slot.locator(".master-required")).toBeVisible();
  expect(
    await select
      .locator("option")
      .evaluateAll((options) =>
        options.map((o) => (o as HTMLOptionElement).value),
      ),
  ).toEqual(["", "0123", "0456"]);
  await select.focus();
  await select.press("ArrowDown");
  await select.press("Enter");
  await expect(select).toHaveValue("0123");
  await select.press("ArrowDown");
  await select.press("Enter");
  await expect(select).toHaveValue("0456");
  await page.getByRole("button", { name: "基本信息", exact: true }).click();
  await page
    .locator('[data-field="regulatory_subtype"] select')
    .selectOption("BULK");
  await page.getByRole("button", { name: "质量管控", exact: true }).click();
  await expect(slot.locator(".master-required")).toHaveCount(0);
  await expect(select).toHaveValue("0456");
  await page.screenshot({
    path: "../../outputs/mdm-dictionaries/品类字典与条件必填.png",
    fullPage: true,
  });
  expect(fixture.writes).toEqual([]);
  expect(fixture.errors).toEqual([]);
});

test("service units share dictionary identity and configuration warns before publishing", async ({
  page,
  context,
}) => {
  const fixture = await setup(page, context);
  await create(page, fixture.schema, "service");
  const unit = page.getByRole("combobox", {
    name: "服务计价单位",
    exact: true,
  });
  await unit.fill("次");
  await expect(unit).toHaveValue("次");
  await expect(page.locator(".dictionary-suggestions:visible")).toHaveCount(0);
  await page
    .getByRole("button", { name: "展开服务计价单位字典", exact: true })
    .click();
  await expect(
    page
      .locator(".dictionary-suggestions:visible")
      .getByRole("option")
      .filter({ hasText: "次" }),
  ).toContainText("当前值");
  // Retain the product draft in its module while inspecting configuration.
  await nav(page, "商品字段设置");
  const settings = page.locator(".master-config");
  await settings.locator(".master-search select").first().selectOption("food");
  await settings
    .locator(".master-search select")
    .last()
    .selectOption("quality");
  const row = settings.locator("tbody tr").filter({
    has: page.locator("small", { hasText: /^storage_conditions$/ }),
  });
  await expect(row).toContainText("可选 2 / 5");
  await row.getByRole("spinbutton", { name: "最小长度" }).fill("5");
  await row.getByRole("spinbutton", { name: "最大长度" }).fill("5");
  await expect(row.getByRole("alert")).toContainText("必填字段无可用字典值");
  expect(fixture.writes).toEqual([]);
  expect(fixture.errors).toEqual([]);
});

test("editable dictionary filters, closes exact matches, preserves free text and supports explicit selection", async ({
  page,
  context,
}) => {
  const fixture = await setup(page, context);
  fixture.schema.payload.rules.drug.dosage_form.max_length = 8;
  fixture.dictionaries.dosage_form.items = [
    { value: "片剂", active: true, order: 2 },
    { value: "胶囊剂", active: true, order: 1 },
    { value: "注射剂", active: true, order: 3 },
    { value: "旧剂型", active: false, order: 0 },
  ];
  await create(page, fixture.schema, "drug");
  const input = page.getByRole("combobox", { name: "剂型", exact: true });
  const popup = page.locator(".dictionary-suggestions:visible");
  await input.fill("剂");
  await expect(popup.getByRole("option")).toHaveText([
    "胶囊剂",
    "片剂",
    "注射剂",
  ]);
  await expect(popup.locator('[aria-selected="true"]')).toHaveCount(0);
  await input.press("Enter");
  await expect(input).toHaveValue("剂");
  await expect(popup).toHaveCount(0);
  await input.fill("片");
  await expect(popup.getByRole("option")).toHaveCount(1);
  await input.fill("片剂");
  await expect(popup).toHaveCount(0);
  await input.press("Tab");
  await input.focus();
  await expect(popup).toHaveCount(0);
  await page.getByRole("button", { name: "展开剂型字典", exact: true }).click();
  await expect(popup.getByRole("option")).toHaveCount(3);
  await expect(
    popup.getByRole("option").filter({ hasText: "片剂" }),
  ).toContainText("当前值");
  await popup.getByRole("option").filter({ hasText: "注射剂" }).click();
  await expect(input).toHaveValue("注射剂");
  await expect(popup).toHaveCount(0);
  await input.fill("剂");
  await input.press("ArrowDown");
  await expect(popup.locator('[aria-selected="true"]')).toContainText("胶囊剂");
  await input.press("ArrowDown");
  await input.press("Enter");
  await expect(input).toHaveValue("片剂");
  await expect(popup).toHaveCount(0);
  await input.fill("自填剂型");
  await expect(popup).toContainText("未找到匹配项，可保留当前输入");
  await input.press("Escape");
  await expect(input).toHaveValue("自填剂型");
  await input.dispatchEvent("compositionstart");
  await input.fill("胶");
  await input.dispatchEvent("keydown", { key: "Enter", isComposing: true });
  await expect(popup).toHaveCount(0);
  await expect(input).toHaveValue("胶");
  await input.dispatchEvent("compositionend");
  await expect(popup.getByRole("option")).toHaveText(["胶囊剂"]);
  await input.press("Tab");
  await expect(popup).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await input.evaluate((el) => el.scrollIntoView({ block: "center" }));
  await input.fill("剂");
  await expect(popup).toBeVisible();
  await expect(popup).toHaveCSS("opacity", "1");
  await expect
    .poll(async () => {
      const r = await popup.boundingBox();
      return (
        !!r &&
        r.x >= 0 &&
        r.x + r.width <= 390 &&
        r.y >= 0 &&
        r.y + r.height <= 844
      );
    })
    .toBe(true);
  await page.screenshot({
    path: "../../outputs/dictionary-lookup/dictionary-390.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await input.click();
  await expect(popup).toBeVisible();
  await expect(popup).toHaveCSS("opacity", "1");
  await page.screenshot({
    path: "../../outputs/dictionary-lookup/dictionary-1440.png",
    fullPage: true,
  });
  expect(fixture.writes).toEqual([]);
  expect(fixture.errors).toEqual([]);
});
