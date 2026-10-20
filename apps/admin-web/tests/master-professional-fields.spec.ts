import { test, expect, type Page, type BrowserContext } from "@playwright/test";
import fs from "node:fs/promises";
import { nav, read, wholesale } from "./business-helpers";

async function create(page: Page, context: BrowserContext, profile: string) {
  await wholesale(context, "demo.owner");
  const schema = await read(context, "/api/v1/catalog/master/schema");
  await page.goto("/");
  await nav(page, "商品档案");
  await page
    .getByRole("button", { name: "新建商品 / 项目", exact: true })
    .click();
  const choose = page.getByRole("dialog", { name: "明确选择建档品类" });
  await choose
    .getByLabel("新建商品品类")
    .selectOption(
      schema.payload.nodes.find((n: any) => n.node_code === profile).node_id,
    );
  await choose.getByRole("button", { name: "进入建档" }).click();
  return schema;
}

test("herbal fields keep old slots and retain original notes across tabs", async ({
  page,
  context,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await create(page, context, "herb");
  await page.locator('[data-field="name"] input').fill("饮片字段页面预览");
  await page.locator('[data-field="base_unit"] input').fill("袋");
  await page
    .locator('[data-field="regulatory_subtype"] select')
    .selectOption("ORDINARY_HERB");
  await page.getByRole("button", { name: "扩展属性", exact: true }).click();
  await expect(page.locator('[data-field="herb_origin"]')).toHaveAttribute(
    "data-slot",
    "E_003",
  );
  const source = page.locator('[data-field="herb_source_note"]');
  await source.locator("input").fill("按标准核对允许使用的基原范围");
  await expect(source).toContainText("每批实际基原另行记录");
  await page
    .locator('[data-field="herb_adjuvants_note"] input')
    .fill("按已核对标准记录辅料原文");
  const decoction = page.locator('[data-field="herb_decoction_note"] input');
  await decoction.fill("保留标签中的特殊煎煮说明及适用条件");
  await page.getByRole("button", { name: "质量管控", exact: true }).click();
  await page
    .locator('[data-field="herb_toxicity_note"] input')
    .fill("按核对后的标签记录毒性描述");
  await expect(
    page.locator('[data-field="food_warning_note"] input'),
  ).toBeDisabled();
  await page.getByRole("button", { name: "扩展属性", exact: true }).click();
  await expect(decoction).toHaveValue("保留标签中的特殊煎煮说明及适用条件");
  const validation = page.waitForRequest(
    (r) =>
      r.url().includes("/catalog/master/validate") && r.method() === "POST",
  );
  await page.getByRole("button", { name: "校验全部页签", exact: true }).click();
  const submitted = (await validation).postDataJSON();
  expect(submitted.values.herb_source_note).toContain("基原范围");
  expect(submitted.values.herb_toxicity_note).toContain("毒性描述");
  expect(errors).toEqual([]);
});

test("special food notes share fields without enabling service or herbal attributes", async ({
  page,
  context,
}) => {
  const schema = await create(page, context, "fsmp");
  expect(
    schema.payload.fields.filter(
      (f: any) => f.key === "food_opened_storage_note",
    ),
  ).toHaveLength(1);
  await page.getByRole("button", { name: "质量管控", exact: true }).click();
  await page
    .locator('[data-field="food_opened_storage_note"] input')
    .fill("按标签记载开封后的贮存条件和期限");
  await page
    .locator('[data-field="food_warning_note"] input')
    .fill("按标签记录适用限制和食用警示");
  await page
    .locator('[data-field="fsmp_guidance_notice"] input')
    .fill("按注册标签核对指导使用提示");
  await expect(
    page.locator('[data-field="health_precautions"] input'),
  ).toBeDisabled();
  await page.getByRole("button", { name: "扩展属性", exact: true }).click();
  const formula = page.locator('[data-field="fsmp_formula_features"] input');
  await formula.fill("配方特点的原始资料说明");
  await expect(
    page.locator('[data-field="service_expected_output"] input'),
  ).toBeDisabled();
  await expect(
    page.locator('[data-field="infant_animal_source_note"] input'),
  ).toBeDisabled();
  await formula.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: "../../outputs/mdm-professional/特医食品专业字段.png",
    fullPage: true,
  });
});

test("old configuration drafts copy into current version without losing rules or new fields", async ({
  page,
  context,
}) => {
  await wholesale(context, "demo.owner");
  const current = await read(context, "/api/v1/catalog/master/schema");
  const previous = JSON.parse(
    await fs.readFile(
      "../../contracts/mdm/product-master-form.v1.json",
      "utf8",
    ),
  );
  previous.rules.food.brand.label = "旧草稿品牌";
  const id = "d1e169bf-3c3e-48c2-a622-5e058ef725c1";
  await page.route("**/api/v1/catalog/master/schema", (route) =>
    route.fulfill({
      json: {
        data: {
          ...current,
          history: [
            { id, state: "DRAFT", revision: 0, version: 2, published_at: null },
          ],
        },
      },
    }),
  );
  await page.route(`**/api/v1/catalog/master/schema/${id}`, (route) =>
    route.fulfill({
      json: {
        data: {
          id,
          state: "DRAFT",
          version: 2,
          base_revision: current.revision - 1,
          payload: previous,
          preview: null,
        },
      },
    }),
  );
  let saved: any;
  await page.route("**/api/v1/catalog/master/schema/draft", async (route) => {
    saved = route.request().postDataJSON();
    await route.fulfill({
      json: {
        data: {
          id,
          version: 1,
          preview: { errors: [], impacts: [], preview_token: "x".repeat(64) },
        },
      },
    });
  });
  await page.goto("/");
  await nav(page, "商品字段设置");
  await page.getByRole("button", { name: "版本与发布", exact: true }).click();
  await page.getByRole("button", { name: "加载草稿", exact: true }).click();
  await expect(page.locator(".master-toolbar")).toContainText("新配置草稿");
  await page
    .getByRole("button", { name: "保存草稿并预览影响", exact: true })
    .click();
  await expect
    .poll(() => saved?.payload.fields.length)
    .toBe(current.payload.fields.length);
  expect(saved.draft_id).toBeNull();
  expect(saved.base_revision).toBe(current.revision);
  expect(saved.payload.rules.food.brand.label).toBe("旧草稿品牌");
  expect(saved.payload.rules.fsmp.fsmp_guidance_notice.applicable).toBe(true);
  expect(saved.payload.rules.service.service_expected_output.applicable).toBe(
    true,
  );
  expect(saved.payload.slots.slice(0, 15)).toEqual(previous.slots.slice(0, 15));
});
