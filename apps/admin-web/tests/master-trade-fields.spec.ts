import { test, expect, type Page, type BrowserContext } from "@playwright/test";
import { wholesale, nav, read, command } from "./business-helpers";

async function createForm(
  page: Page,
  context: BrowserContext,
  profile = "daily",
) {
  const schema = await read(context, "/api/v1/catalog/master/schema");
  await page.goto("/");
  await nav(page, "商品档案");
  await page
    .getByRole("button", { name: "新建商品 / 项目", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "明确选择建档品类" });
  await dialog
    .getByLabel("新建商品品类")
    .selectOption(
      schema.payload.nodes.find((n: any) => n.node_code === profile).node_id,
    );
  await dialog.getByRole("button", { name: "进入建档" }).click();
  await page
    .locator('[data-field="name"] input')
    .fill(`基本单位录入验收-${Date.now().toString(36)}`);
  await page
    .locator('[data-field="base_unit"] input')
    .fill(profile === "service" ? "次" : "盒");
}
async function save(page: Page) {
  const response = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/v1/catalog/master/products") &&
      r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "保存资料草稿", exact: true }).click();
  const result = await response;
  expect(result.status(), await result.text()).toBe(200);
  const saved = (await result.json()).data;
  await expect(page.locator(".master-state")).not.toContainText("已修改");
  return saved;
}
const tabs = (page: Page) =>
  page.getByRole("navigation", { name: "商品资料分组" });

test("basic barcode and prices are entered directly; other packaging keeps its conversion and barcode", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(90000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext();
  const faults: string[] = [];
  page.on("pageerror", (e) => faults.push(e.message));
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    await createForm(page, context);
    const barcode = `000-${Date.now().toString(36)}`;
    await page.getByLabel("条码（基本单位）", { exact: true }).fill(barcode);
    await expect(
      page.getByRole("button", { name: "维护其他包装", exact: true }),
    ).toBeDisabled();
    const colors = await page
      .locator(
        '[data-field="name"] > label,[data-field="service_department"] > label',
      )
      .evaluateAll((els) => els.map((el) => getComputedStyle(el).color));
    expect(colors[0]).not.toBe(colors[1]);
    await tabs(page)
      .getByRole("button", { name: "价格信息", exact: true })
      .click();
    await page.getByLabel("参考采购价", { exact: true }).fill("0");
    await page.getByLabel("参考批发价", { exact: true }).fill("1.123456");
    await page.getByLabel("建议零售价", { exact: true }).fill("12.50");
    await page.getByLabel("价格备注", { exact: true }).fill("基本单位参考价");
    await expect(page.locator(".master-inline-prices:visible")).toContainText(
      "元／盒",
    );
    await page.screenshot({
      path: "../../outputs/mdm-basic-fields/价格直接录入.png",
      fullPage: true,
    });
    const product = await save(page);
    let detail = await read(
      context,
      `/api/v1/catalog/master/products/${product.id}`,
    );
    const sku = detail.skus[0].id;
    expect(detail.values.barcodes[0].external_code).toBe(barcode);
    expect(detail.values.barcodes[0].packaging_id).toBeNull();
    expect(detail.values.reference_prices[0].purchase_price).toBe("0.000000");
    expect(detail.values.reference_prices[0].wholesale_price).toBe("1.123456");
    await expect(
      page.getByLabel("条码（基本单位）", { exact: true }),
    ).toHaveValue(barcode);
    await page.screenshot({
      path: "../../outputs/mdm-basic-fields/基本信息与其他包装.png",
      fullPage: true,
    });
    await page
      .getByRole("button", { name: "维护其他包装", exact: true })
      .click();
    const packaging = page.getByRole("dialog", {
      name: "其他包装与换算",
      exact: true,
    });
    await expect(packaging).not.toContainText("SKU");
    await packaging
      .getByRole("button", { name: "添加其他包装", exact: true })
      .click();
    await packaging.getByLabel("包装名称", { exact: true }).fill("箱");
    await packaging.getByLabel("所含盒数", { exact: true }).fill("100");
    await packaging
      .getByLabel("包装维护原因", { exact: true })
      .fill("虚构测试包装核对");
    await expect(packaging).toContainText("1 箱 ＝ 100 盒");
    await page.screenshot({
      path: "../../outputs/mdm-basic-fields/包装换算录入.png",
      fullPage: true,
    });
    await packaging
      .getByRole("button", { name: "保存待审包装", exact: true })
      .click();
    await expect(packaging.getByRole("cell", { name: /待审核/ })).toBeVisible();
    const packPath = `/api/v1/catalog/skus/${sku}/packaging`;
    const pack = (await read(context, packPath)).items[0];
    expect(pack.numerator).toBe("100");
    await command(reviewer, checker, `${packPath}/${pack.id}/review`, {
      expected_version: pack.version,
      decision: "APPROVE",
      reason: "核对一箱100盒",
      evidence_ref: "DEMO-ONLY",
    });
    await packaging.getByRole("button", { name: "关闭此对话框" }).click();
    await page
      .getByRole("button", { name: "其他条码与历史", exact: true })
      .click();
    const codes = page.getByRole("dialog", {
      name: "其他条码与历史",
      exact: true,
    });
    await codes.getByRole("button", { name: "新增编码", exact: true }).click();
    await codes
      .getByRole("combobox", { name: "条码对应单位", exact: true })
      .selectOption(pack.id);
    await codes.getByLabel("条码", { exact: true }).fill(barcode + "-case");
    await expect(codes).not.toContainText("SKU");
    await codes.getByRole("button", { name: "保存编码", exact: true }).click();
    await expect(
      codes.getByRole("row").filter({ hasText: barcode + "-case" }),
    ).toContainText("箱");
    await codes.getByRole("button", { name: "关闭此对话框" }).click();
    await expect(
      page.getByLabel("条码（基本单位）", { exact: true }),
    ).toHaveValue(barcode);
    await page
      .getByLabel("条码（基本单位）", { exact: true })
      .fill(barcode + "-new");
    await save(page);
    detail = await read(
      context,
      `/api/v1/catalog/master/products/${product.id}`,
    );
    expect(
      detail.values.barcodes.map((c: any) => c.external_code).sort(),
    ).toEqual([barcode + "-case", barcode + "-new"].sort());
    const history = await read(
      context,
      `/api/v1/catalog/skus/${sku}/codes?schemes=BARCODE`,
    );
    expect(
      history.items.find((c: any) => c.external_code === barcode).state,
    ).toBe("INACTIVE");
    await tabs(page)
      .getByRole("button", { name: "价格信息", exact: true })
      .click();
    await expect(page.getByLabel("建议零售价", { exact: true })).toHaveValue(
      "12.500000",
    );
    await page.setViewportSize({ width: 480, height: 900 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
    expect(faults).toEqual([]);
  } finally {
    await reviewer.close();
  }
});

test("a stale price protects pending inline edits and preserves the original barcode", async ({
  page,
  context,
}) => {
  const owner = await wholesale(context, "demo.owner");
  await createForm(page, context);
  const value = `000-${Date.now().toString(36)}`;
  await page.getByLabel("条码（基本单位）", { exact: true }).fill(value);
  const product = await save(page);
  const initial = await read(
    context,
    `/api/v1/catalog/master/products/${product.id}`,
  );
  await command(
    context,
    owner,
    `/api/v1/catalog/skus/${initial.skus[0].id}/prices`,
    { expected_version: "0", retail_price: "99", reason: "模拟另一窗口改价" },
  );
  await page
    .getByLabel("条码（基本单位）", { exact: true })
    .fill(value + "-unsaved");
  await tabs(page)
    .getByRole("button", { name: "价格信息", exact: true })
    .click();
  await page.getByLabel("建议零售价", { exact: true }).fill("10");
  await page.getByRole("button", { name: "保存资料草稿", exact: true }).click();
  await expect(page.locator(".el-message--error:visible")).toContainText(
    "参考价格已变化",
  );
  await expect(page.getByLabel("建议零售价", { exact: true })).toHaveValue(
    "10",
  );
  await tabs(page)
    .getByRole("button", { name: "基本信息", exact: true })
    .click();
  await expect(
    page.getByLabel("条码（基本单位）", { exact: true }),
  ).toHaveValue(value + "-unsaved");
  const latest = await read(
    context,
    `/api/v1/catalog/master/products/${product.id}`,
  );
  expect(latest.version).toBe(initial.version);
  expect(latest.values.barcodes[0].external_code).toBe(value);
  await page.getByRole("button", { name: "关闭商品档案", exact: true }).click();
  await expect(
    page.getByRole("dialog", { name: "关闭前保存修改" }),
  ).toBeVisible();
});

test("service fields stay readable but inapplicable barcode and physical prices are dimmed and disabled", async ({
  page,
  context,
}) => {
  await wholesale(context, "demo.owner");
  await createForm(page, context, "service");
  await expect(page.locator('[data-field="barcodes"]')).toHaveClass(
    /not-applicable/,
  );
  await expect(page.locator('[data-field="barcodes"] input')).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "维护其他包装", exact: true }),
  ).toHaveCount(0);
  await tabs(page)
    .getByRole("button", { name: "价格信息", exact: true })
    .click();
  await expect(
    page.locator('[data-field="reference_prices"] input'),
  ).toBeDisabled();
  await page.locator('[data-field="service_price"] input').fill("35");
  const result = await save(page);
  const detail = await read(
    context,
    `/api/v1/catalog/master/products/${result.id}`,
  );
  expect(detail.skus).toHaveLength(0);
  expect(detail.values.service_price).toBe("35.000000");
});
