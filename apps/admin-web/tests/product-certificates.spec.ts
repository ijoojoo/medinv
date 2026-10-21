import { test, expect, type Page } from "@playwright/test";
import {
  wholesale,
  read,
  command,
  nav,
  approveCatalogParent,
} from "./business-helpers";

async function openProduct(page: Page, code: string) {
  await page.goto("/");
  await nav(page, "商品档案");
  await page.getByLabel("检索商品主数据", { exact: true }).fill(code);
  await page.getByRole("button", { name: "查询", exact: true }).click();
  await page
    .locator(".master-list-table tr")
    .filter({ hasText: code })
    .getByRole("button", { name: "维护", exact: true })
    .click();
  await page
    .getByRole("button", { name: "证件详情与维护", exact: true })
    .click();
  await page
    .getByText("维护证件原件 / 续期（随商品审核）", { exact: true })
    .click();
  return page.getByRole("region", { name: "商品证照档案", exact: true });
}

test("certificate drafts and renewals share parent approval while live product remains effective", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(120000);
  const owner = await wholesale(context, "demo.owner"),
    suffix = Date.now().toString(36),
    year = new Date().getFullYear();
  const code = `CERT-${suffix}`;
  const product = await command(context, owner, "/api/v1/catalog/products", {
    code,
    name: `证照档案验收-${suffix}`,
    category: "DEVICE",
    device_class: "I",
    manufacturer_name: "软件测试厂家",
    specification: "测试型号",
    base_unit: "个",
    approval_number: "LEGACY-TEST",
    valid_from: `${year}-01-01`,
    valid_until: `${year + 2}-12-31`,
  });
  const reviewer = await browser.newContext();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    // The certificate and MDM identity are approved together with the parent.
    // Keep that base certificate separate from the supplemental record exercised below.
    const schema = await read(context, "/api/v1/catalog/master/schema");
    const baseCertificate = await command(
      context,
      owner,
      `/api/v1/catalog/products/${product.id}/certificates`,
      {
        certificate_type: "DEVICE_FILING",
        number: "LEGACY-TEST",
        holder_name: "软件测试厂家",
        issuer: "软件测试机关",
        validity_mode: "FIXED",
        valid_from: `${year}-01-01`,
        valid_until: `${year + 2}-12-31`,
        evidence_ref: "BASE-TEST",
        reason: "软件验收准入资料",
      },
    );
    const current = (
      await read(context, `/api/v1/catalog/products/${product.id}`)
    ).record;
    const master = await command(
      context,
      owner,
      "/api/v1/catalog/master/products",
      {
        product_id: product.id,
        category_node_id: schema.payload.nodes.find(
          (n: any) => n.node_code === "device",
        ).node_id,
        form_revision: schema.revision,
        expected_version: Number(current.version),
        values: {
          regulatory_subtype: "MEDICAL_DEVICE",
          device_class: "I",
          regulatory_subject: [
            { organization_name: "软件测试厂家", role_code: "DEVICE_FILER" },
          ],
          manufacturer: [
            { organization_name: "软件测试厂家", role_code: "MANUFACTURER" },
          ],
          product_certificates: [
            {
              certificate_id: baseCertificate.id,
              certificate_version: Number(baseCertificate.version),
              business_use: true,
            },
          ],
        },
        reason: "软件验收补齐监管身份",
      },
    );
    product.version = String(master.version);

    await command(
      reviewer,
      checker,
      `/api/v1/catalog/products/${product.id}/review`,
      {
        expected_version: product.version,
        decision: "APPROVE",
        evidence_ref: "TEST-LEGACY",
        reason: "软件验收",
      },
    );
    const parent = (
      await read(context, `/api/v1/catalog/products/${product.id}`)
    ).record;
    const card = await openProduct(page, code);
    await expect(card).toContainText("LEGACY-TEST");
    await card.getByRole("button", { name: "新增证照", exact: true }).click();
    const form = card.getByRole("form", { name: "证照编辑", exact: true });
    await form
      .getByRole("combobox", { name: "证照类型", exact: true })
      .selectOption("DEVICE_FILING");
    for (const [label, value] of [
      ["证照编号", "TEST-" + suffix],
      ["持有人 / 注册人 / 备案人", "测试备案企业"],
      ["发证 / 备案机关", "测试备案机关"],
      ["原件凭据编号", "FILE-TEST"],
      ["覆盖规格型号", "A1、A2"],
      ["证照操作说明", "原件建档验收"],
    ])
      await form.getByLabel(label, { exact: true }).fill(value);
    await form
      .getByRole("combobox", { name: "有效期方式", exact: true })
      .selectOption("FIXED");
    const issued = form.getByRole("textbox", {
      name: "签发 / 备案日期",
      exact: true,
    });
    const from = form.getByRole("textbox", {
      name: "有效期起始日期",
      exact: true,
    });
    const until = form.getByRole("textbox", {
      name: "有效期截止日期",
      exact: true,
    });
    let writes = 0;
    page.on("request", (r) => {
      if (
        r.method() === "POST" &&
        new URL(r.url()).pathname ===
          `/api/v1/catalog/products/${product.id}/certificates`
      )
        writes++;
    });
    await issued.fill("20230229");
    await from.fill("20260101");
    await until.fill("20281231");
    await form.evaluate((f: HTMLFormElement) => f.requestSubmit());
    await expect(issued).toHaveAttribute("aria-invalid", "true");
    await expect(issued).toBeFocused();
    expect(writes).toBe(0);
    await issued.fill("20240229");
    await until.fill("20251231");
    await form.evaluate((f: HTMLFormElement) => f.requestSubmit());
    await expect(from).toHaveAttribute("aria-invalid", "true");
    expect(writes).toBe(0);
    await until.fill("20281231");
    const container = page.getByRole("dialog").filter({ has: card });
    await container.locator(".el-dialog__headerbtn").click();
    const leave = page.getByRole("dialog", {
      name: "关闭维护窗口",
      exact: true,
    });
    await leave.getByRole("button", { name: "继续维护", exact: true }).click();
    await expect(form.getByLabel("证照编号", { exact: true })).toHaveValue(
      "TEST-" + suffix,
    );
    await page
      .getByRole("button", { name: "应用证件关联", exact: true })
      .click();
    await expect(form).toBeVisible();
    await form
      .getByRole("button", { name: "保存证照草稿", exact: true })
      .click();
    await expect(form).not.toBeVisible();
    let records = await read(
      context,
      `/api/v1/catalog/products/${product.id}/certificates`,
    );
    expect(records.items).toHaveLength(2);
    const cert = records.items.find((r: any) => r.number === "TEST-" + suffix);
    expect(cert.state).toBe("DRAFT");
    expect(writes).toBe(1);
    expect(cert).toMatchObject({
      issued_on: "2024-02-29",
      valid_from: "2026-01-01",
      valid_until: "2028-12-31",
    });
    await expect(
      card.getByRole("button", { name: "提交证照核验" }),
    ).toHaveCount(0);

    const qualityPage = await reviewer.newPage();
    qualityPage.on("pageerror", (e) => errors.push(e.message));
    const qualityCard = await openProduct(qualityPage, code);
    await expect(
      qualityPage.locator('[data-field="product_certificates"] input'),
    ).toBeDisabled();
    await expect(
      qualityCard.getByRole("button", { name: "新增证照", exact: true }),
    ).toHaveCount(0);
    await qualityCard
      .locator("tbody tr")
      .filter({ hasText: "TEST-" + suffix })
      .getByRole("button", { name: "查看证照", exact: true })
      .click();
    await approveCatalogParent(reviewer, checker, "products", product.id);
    await openProduct(qualityPage, code);
    await expect(qualityCard.locator("tbody").first()).toContainText("已核验");
    await card.getByRole("button", { name: "刷新证照", exact: true }).click();
    await expect(card.getByRole("region", { name: "证照详情" })).toContainText(
      "第 1 版",
    );
    await card.getByRole("button", { name: "修订证照", exact: true }).click();
    await form.getByLabel("覆盖规格型号", { exact: true }).fill("A1、A2、A3");
    await form
      .getByRole("combobox", { name: "有效期方式", exact: true })
      .selectOption("NOT_STATED");
    await expect(
      form.getByRole("textbox", { name: "有效期截止日期", exact: true }),
    ).toHaveCount(0);
    await issued.fill("");
    await form.getByLabel("证照操作说明", { exact: true }).fill("补充覆盖型号");
    await form
      .getByRole("button", { name: "保存证照草稿", exact: true })
      .click();
    await expect(form).not.toBeVisible();
    await expect(card.locator("tbody").first()).toContainText("随主体待审核");
    await card.getByText("证照变更记录", { exact: true }).click();
    await card
      .getByTestId("catalog-history")
      .locator("summary")
      .filter({ hasText: "证照修订" })
      .click();
    await expect(card.getByTestId("catalog-history")).toContainText(
      "A1、A2、A3",
    );
    await expect(page.locator(".el-message")).toHaveCount(0, { timeout: 8000 });
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: "../../artifacts/mdm-certificate-card.png",
      fullPage: true,
      animations: "disabled",
    });
    await page.setViewportSize({ width: 390, height: 760 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
    const after = (
      await read(context, `/api/v1/catalog/products/${product.id}`)
    ).record;
    expect(Number(after.version)).toBeGreaterThan(Number(parent.version));
    expect(after.has_changes).toBe(true);
    expect(after.approval_state).toBe("APPROVED");
    records = await read(
      context,
      `/api/v1/catalog/products/${product.id}/certificates/${cert.id}`,
    );
    expect(records.history).toHaveLength(3);
    expect(records.record).toMatchObject({
      issued_on: null,
      valid_from: null,
      valid_until: null,
      validity_mode: "NOT_STATED",
    });
    expect(errors).toEqual([]);
  } finally {
    await reviewer.close();
  }
});
