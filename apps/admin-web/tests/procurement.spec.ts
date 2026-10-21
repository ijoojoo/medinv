import { chooseParty, command, read } from "./business-helpers";
import { chooseEntrySku, nav } from "./business-helpers";
import {
  test,
  expect,
  type Locator,
  type BrowserContext,
} from "@playwright/test";
import { login, headers } from "./helpers";

async function wholesale(context: BrowserContext, user: string) {
  let current = await login(context, user);
  const target = current.tenants.find((t: { name: string }) =>
    t.name.includes("批发"),
  );
  if (current.tenant_id !== target.id) {
    const response = await context.request.post(
      "http://127.0.0.1:5173/auth/tenant",
      {
        headers: headers(current),
        data: { tenant_id: target.id },
      },
    );
    expect(response.status()).toBe(200);
    current = (await response.json()).data;
  }
  return current;
}

async function assertQuantity(row: Locator, key: string, expected: number) {
  await expect
    .poll(async () =>
      Number(await row.locator(`[data-grid-cell="${key}"]`).innerText()),
    )
    .toBe(expected);
}

test("two roles complete purchase, partial inspection, posting and rejected handback in the UI", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(150000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext();
  const quality = await reviewer.newPage();
  quality.on("pageerror", (error) => errors.push(error.message));
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    const suffix = Date.now().toString(36);
    const product = "验收演示器械-" + suffix;
    const supplier = "验收演示供应商-" + suffix;
    const year = new Date().getFullYear();
    // Catalog editing has its own MDM regression suite. Prepare approved fixtures
    // through the same public APIs and keep this test focused on purchasing.
    const item = await command(context, owner, "/api/v1/catalog/products", {
      code: "P-" + suffix,
      sku_code: "S-" + suffix,
      name: product,
      category: "DEVICE",
      device_class: "I",
      manufacturer_name: "软件测试厂家",
      specification: "单件",
      approval_number: "DEMO-ONLY",
      valid_from: `${year}-01-01`,
      valid_until: `${year + 2}-12-31`,
      base_unit: "个",
      qty_scale: 0,
    });
    const party = await command(context, owner, "/api/v1/catalog/parties", {
      entity_id: owner.entities[0].id,
      code: "SUP-" + suffix,
      name: supplier,
      credit_code: "DEMO-CREDIT",
      kind: "SUPPLIER",
      license_no: "DEMO-ONLY",
      valid_from: `${year}-01-01`,
      valid_until: `${year + 2}-12-31`,
      categories: ["DEVICE"],
    });
    // Bring this older purchasing fixture up to the current MDM identity contract.
    const schema = await read(context, "/api/v1/catalog/master/schema");
    const cert = await command(
      context,
      owner,
      `/api/v1/catalog/products/${item.id}/certificates`,
      {
        certificate_type: "DEVICE_FILING",
        number: "TEST-FILING-" + suffix,
        holder_name: "软件测试厂家",
        issuer: "软件测试机关",
        validity_mode: "FIXED",
        valid_from: `${year}-01-01`,
        valid_until: `${year + 2}-12-31`,
        evidence_ref: "E2E-DEMO-FILE",
        reason: "软件测试资质",
      },
    );
    const current = (await read(context, `/api/v1/catalog/products/${item.id}`))
      .record;
    const identity = await command(
      context,
      owner,
      "/api/v1/catalog/master/products",
      {
        product_id: item.id,
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
              certificate_id: cert.id,
              certificate_version: Number(cert.version),
              business_use: true,
            },
          ],
        },
        reason: "补齐当前监管身份",
      },
    );
    for (const [kind, id] of [
      ["products", item.id],
      ["parties", party.id],
    ])
      await command(reviewer, checker, `/api/v1/catalog/${kind}/${id}/review`, {
        expected_version: kind === "products" ? String(identity.version) : "1",
        decision: "APPROVE",
        evidence_ref: "E2E-DEMO-FILE",
        reason: "仅供软件测试的虚拟资质",
      });
    await page.goto("/");
    await quality.goto("/");
    let dialog = page.getByRole("dialog");
    await nav(page, "采购入库");
    await page
      .getByRole("button", { name: "新建采购订单", exact: true })
      .click();
    dialog = page.getByRole("region", { name: "新建采购单", exact: true });
    await chooseParty(
      dialog.getByRole("combobox", { name: "供应商", exact: true }),
      { label: supplier },
    );
    await chooseEntrySku(dialog, { code: `S-${suffix}` });
    await dialog.getByLabel("采购数量").fill("10");
    await dialog.getByLabel("含税单价（元）").fill("13");
    await dialog.getByRole("button", { name: "保存采购单" }).click();
    await expect(dialog).not.toBeVisible();
    const orderNo = await page.locator(".document-number").first().innerText();
    await expect(
      page.getByRole("button", { name: "审核采购单", exact: true }),
    ).toBeDisabled();
    await nav(quality, "采购入库");
    if (
      await quality
        .getByRole("button", { name: "← 返回采购列表", exact: true })
        .isVisible()
    )
      await quality
        .getByRole("button", { name: "← 返回采购列表", exact: true })
        .click();
    await quality
      .getByRole("row")
      .filter({ hasText: orderNo })
      .getByRole("button", { name: "查看采购单" })
      .click();
    await quality
      .getByRole("button", { name: "审核采购单", exact: true })
      .click();
    dialog = quality.getByRole("dialog");
    await dialog.getByLabel("补充说明").fill("商品与供应商资质已核查");
    await dialog.getByRole("button", { name: "确认审核" }).click();
    await expect(dialog).not.toBeVisible();
    await page.getByRole("button", { name: "刷新单据" }).click();
    await page.getByRole("button", { name: "登记到货", exact: true }).click();
    dialog = page.getByRole("dialog");
    await dialog.getByLabel("随货单号").fill("DELIVERY-" + suffix);
    await dialog.getByLabel("运输及随货核查").fill("常温运输，随货凭据齐全");
    await dialog.getByLabel("到货数量").fill("10");
    await dialog.getByLabel("生产批号").fill("LOT-" + suffix);
    await dialog.getByLabel("生产日期", { exact: true }).fill(`${year}-01-01`);
    await dialog
      .getByLabel("有效期至", { exact: true })
      .fill(`${year + 1}-12-31`);
    await dialog.getByRole("button", { name: "保存到货记录" }).click();
    await expect(dialog).not.toBeVisible();
    await expect(
      page.getByRole("button", { name: "登记验收", exact: true }),
    ).toBeDisabled();

    await quality.getByRole("button", { name: "刷新单据" }).click();
    await quality
      .getByRole("button", { name: "登记验收", exact: true })
      .click();
    dialog = quality.getByRole("dialog");
    await dialog.getByLabel("本次合格数量").fill("6");
    await dialog.getByLabel("本次拒收数量").fill("2");
    await dialog
      .getByLabel("验收依据与结论")
      .fill("核验凭据 E2E：6 件合格，2 件包装破损拒收，2 件待验");
    await dialog.getByRole("button", { name: "保存验收记录" }).click();
    await expect(dialog).not.toBeVisible();
    const receiptRow = quality
      .locator("[data-testid^='receipt-lines-']")
      .getByRole("row")
      .filter({ hasText: product });
    await assertQuantity(receiptRow, "pending_qty", 2);
    await assertQuantity(receiptRow, "posted_qty", 0);
    await quality
      .getByRole("button", { name: "合格入库", exact: true })
      .click();
    dialog = quality.getByRole("dialog");
    await assertQuantity(dialog, "qualified_qty", 6);
    await expect(dialog.locator('[data-grid-cell="base_unit"]')).toHaveText(
      "个",
    );
    await dialog.getByRole("button", { name: "确认入库", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await assertQuantity(receiptRow, "posted_qty", 6);
    await assertQuantity(receiptRow, "custody_qty", 4);
    await expect(quality.locator(".el-message")).toHaveCount(0);
    await quality.screenshot({
      path: "../../artifacts/purchase-partial-inspection.png",
      fullPage: true,
      animations: "disabled",
    });

    await nav(quality, "库存台账");
    const ledgerRow = quality.getByRole("row").filter({ hasText: product });
    await expect(ledgerRow).toHaveCount(1);
    await expect(ledgerRow).toContainText("6 个");
    await expect(ledgerRow).toContainText("69.03");
    await ledgerRow.getByRole("button", { name: "查看过账应付" }).click();
    await expect(quality.getByRole("dialog")).toContainText("78.00 元");
    await expect(quality.getByRole("dialog")).toContainText("8.97 元");
    await quality.screenshot({
      path: "../../artifacts/purchase-payable.png",
      fullPage: true,
      animations: "disabled",
    });
    await quality.getByRole("dialog").locator(".el-dialog__headerbtn").click();
    await quality.screenshot({
      path: "../../artifacts/stock-ledger.png",
      fullPage: true,
      animations: "disabled",
    });
    await nav(quality, "库存查询");
    await quality.getByRole("combobox", { name: "库存商品" }).click();
    await quality
      .getByRole("option", { name: `${product} · S-${suffix}`, exact: true })
      .click();
    await quality
      .getByRole("button", { name: "查询库存", exact: true })
      .click();
    await expect(quality.locator(".available:visible")).toHaveText("6");

    // Complete physical custody in the same purchase order, without a stock return.
    await page.getByRole("button", { name: "刷新单据" }).click();
    const ownerRow = page
      .locator("[data-testid^='receipt-lines-']")
      .getByRole("row")
      .filter({ hasText: product });
    for (let index = 1; index <= 2; index++) {
      await page
        .getByRole("button", { name: "拒收退回供应商", exact: true })
        .click();
      dialog = page.getByRole("dialog");
      await expect(dialog).toContainText(`拒收待退 ${3 - index} 个`);
      await dialog.getByLabel("本次退回数量").fill("1");
      await dialog.getByLabel("交接凭据号").fill(`HANDOVER-${suffix}-${index}`);
      await dialog.getByLabel("供应商接收人或承运方").fill("软件验收承运方");
      await dialog.getByLabel("退回说明").fill("破损拒收品已当面清点交接");
      await dialog.getByRole("button", { name: "确认实物已退回" }).click();
      await expect(dialog).not.toBeVisible();
      await assertQuantity(ownerRow, "posted_qty", 6);
      await assertQuantity(ownerRow, "custody_qty", 4 - index);
      await assertQuantity(ownerRow, "rejected_returned_qty", index);
    }
    await expect(
      page.getByRole("button", { name: "拒收退回供应商", exact: true }),
    ).toHaveCount(0);
    await page.getByText("查看拒收退回记录", { exact: true }).click();
    await expect(
      page.getByText(`交接凭据 HANDOVER-${suffix}-1`, { exact: false }),
    ).toBeVisible();

    await nav(quality, "采购入库");
    if (
      await quality
        .getByRole("button", { name: "← 返回采购列表", exact: true })
        .isVisible()
    )
      await quality
        .getByRole("button", { name: "← 返回采购列表", exact: true })
        .click();
    await quality
      .getByRole("row")
      .filter({ hasText: orderNo })
      .getByRole("button", { name: "查看采购单" })
      .click();
    await quality
      .getByRole("button", { name: "刷新单据", exact: true })
      .click();
    await quality
      .getByRole("button", { name: "登记验收", exact: true })
      .click();
    dialog = quality.getByRole("dialog");
    await dialog.getByLabel("本次合格数量").fill("2");
    await dialog.getByLabel("本次拒收数量").fill("0");
    await dialog
      .getByLabel("验收依据与结论")
      .fill("剩余 2 件补齐凭据后验收合格");
    await dialog.getByRole("button", { name: "保存验收记录" }).click();
    await expect(dialog).not.toBeVisible();
    await quality
      .getByRole("button", { name: "合格入库", exact: true })
      .click();
    await quality
      .getByRole("dialog")
      .getByRole("button", { name: "确认入库", exact: true })
      .click();
    await expect(quality.getByRole("dialog")).not.toBeVisible();
    await assertQuantity(receiptRow, "posted_qty", 8);
    await assertQuantity(receiptRow, "custody_qty", 0);
    await assertQuantity(receiptRow, "rejected_returned_qty", 2);
    await quality.getByText("查看拒收退回记录", { exact: true }).click();
    await expect(quality.locator(".el-message")).toHaveCount(0);
    await quality.evaluate(() => window.scrollTo(0, 0));
    await quality.screenshot({
      path: "../../artifacts/purchase-custody-completed.png",
      fullPage: true,
      animations: "disabled",
    });
    expect(errors).toEqual([]);
  } finally {
    await reviewer.close();
  }
});

test("viewer can read purchasing and catalog but has no write controls", async ({
  page,
  context,
}) => {
  await wholesale(context, "demo.viewer");
  await page.goto("/");
  await nav(page, "商品档案");
  await expect(
    page.getByRole("button", { name: "新建商品 / 项目", exact: true }),
  ).toHaveCount(0);
  await nav(page, "采购入库");
  await expect(
    page.getByRole("button", { name: "新建采购订单", exact: true }),
  ).toHaveCount(0);
});
