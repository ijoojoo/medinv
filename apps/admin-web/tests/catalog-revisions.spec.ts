import { test, expect, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { headers } from "./helpers";
import {
  wholesale,
  stockedCustomer,
  command,
  read,
  nav,
  origin,
} from "./business-helpers";

async function openRecord(page: Page, menu: string, name: string) {
  await page.goto("/");
  await nav(page, menu);
  await page.getByLabel("查询资料", { exact: true }).fill(name);
  await page.getByRole("button", { name: "查询", exact: true }).click();
  await page
    .getByRole("row")
    .filter({ hasText: name })
    .getByRole("button", { name: "资料详情", exact: true })
    .click();
  await expect(
    page
      .getByRole("region", { name: "资料详情", exact: true })
      .getByRole("heading", { name, exact: true }),
  ).toBeVisible();
}

test("product and party revisions require independent review, retain history, and restore purchasing and retail eligibility", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(150000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext();
  const checker = await wholesale(reviewer, "demo.quality");
  const quality = await reviewer.newPage();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  quality.on("pageerror", (e) => errors.push(e.message));
  try {
    const f = await stockedCustomer(context, owner, reviewer, checker);
    const productName = f.name + "-修订";
    const partyName = f.customer + "-续期";
    for (const [kind, menu, originalName, revisedName, id] of [
      ["products", "商品档案", f.name, productName, f.product.id],
      ["parties", "往来单位", f.customer, partyName, f.party.id],
    ]) {
      await openRecord(page, menu, originalName);
      let dialog = page.getByRole("region", { name: "资料详情", exact: true });
      await dialog
        .getByRole("button", { name: "修订资料", exact: true })
        .click();
      dialog = page.getByRole("region", { name: "修订资料", exact: true });
      await expect(
        dialog.getByLabel(kind === "products" ? "商品编码" : "编码", {
          exact: true,
        }),
      ).toBeDisabled();
      if (kind === "products") {
        await expect(
          dialog.getByLabel("基本单位", { exact: true }),
        ).toBeDisabled();
        await dialog
          .getByLabel("规格型号", { exact: true })
          .fill("单件（资料更正）");
      } else {
        await expect(
          dialog.getByLabel("统一社会信用代码", { exact: true }),
        ).toBeDisabled();
        await dialog
          .getByLabel("经营许可 / 备案编号", { exact: true })
          .fill("DEMO-RENEWED");
      }
      await dialog.getByLabel("名称", { exact: true }).fill(revisedName);
      await dialog
        .getByLabel("资质截止日期", { exact: true })
        .fill(`${f.year + 3}-12-31`);
      await dialog
        .getByLabel("修订原因", { exact: true })
        .fill("软件验收：核对原档案并续期");
      await dialog
        .getByRole("button", { name: "保存修订并重新送审", exact: true })
        .click();
      dialog = page.getByRole("region", { name: "资料详情", exact: true });
      await expect(
        dialog.getByRole("heading", { name: revisedName, exact: true }),
      ).toBeVisible();
      await dialog.getByRole("tab", { name: "质量管控", exact: true }).click();
      await expect(
        dialog.getByRole("button", { name: "提交审核", exact: true }),
      ).toBeDisabled();
      await dialog.getByRole("tab", { name: "变更记录", exact: true }).click();
      await expect(dialog.getByTestId("catalog-history")).toContainText(
        "修订前",
      );
      await expect(dialog.getByTestId("catalog-history")).toContainText(
        originalName,
      );
      const pending = await read(context, `/api/v1/catalog/${kind}/${id}`);
      expect(pending.record.approval_state || pending.record.state).toBe(
        "DRAFT",
      );
      const denied = await context.request.post(
        origin + `/api/v1/catalog/${kind}/${id}/review`,
        {
          headers: { ...headers(owner), "Idempotency-Key": randomUUID() },
          data: {
            expected_version: pending.record.version,
            decision: "APPROVE",
            evidence_ref: "SELF",
            reason: "本人不能通过",
          },
        },
      );
      expect(denied.status()).toBe(403);
      const choices = await read(
        context,
        `/api/v1/purchasing/options?entity_id=${f.entity}`,
      );
      expect(
        (kind === "products" ? choices.skus : choices.suppliers).some(
          (r: any) => r.id === (kind === "products" ? f.product.sku_id : id),
        ),
      ).toBe(false);
      await openRecord(quality, menu, revisedName);
      dialog = quality.getByRole("region", { name: "资料详情", exact: true });
      await expect(
        dialog.getByRole("button", { name: "修订资料", exact: true }),
      ).toHaveCount(0);
      await dialog
        .getByLabel("核验凭据编号", { exact: true })
        .fill("REVIEW-" + f.suffix);
      await dialog
        .getByLabel("审核说明", { exact: true })
        .fill("独立核对更正内容及续期凭据");
      await dialog
        .getByRole("button", { name: "提交审核", exact: true })
        .click();
      await expect(dialog).not.toBeVisible();
      const approved = await read(context, `/api/v1/catalog/${kind}/${id}`);
      expect(approved.record.approval_state || approved.record.state).toBe(
        "APPROVED",
      );
    }
    const choices = await read(
      context,
      `/api/v1/purchasing/options?entity_id=${f.entity}`,
    );
    expect(choices.skus.some((r: any) => r.id === f.product.sku_id)).toBe(true);
    expect(choices.suppliers.some((r: any) => r.id === f.party.id)).toBe(true);
    const sale = await command(context, owner, "/api/v1/retail/sales", {
      client_operation_id: randomUUID(),
      entity_id: f.entity,
      warehouse_id: f.warehouse,
      lines: [
        {
          sku_id: f.product.sku_id,
          qty: "1",
          unit_price: "20",
          tax_rate: "0.13",
        },
      ],
    });
    await command(context, owner, `/api/v1/retail/sales/${sale.id}/cancel`, {
      expected_version: "1",
      reason: "资料修订验收预占释放",
    });
    await openRecord(quality, "商品档案", productName);
    await quality.getByRole("tab", { name: "变更记录", exact: true }).click();
    const productBefore = quality
      .getByTestId("catalog-history")
      .getByRole("cell", { name: f.name, exact: true });
    await productBefore.scrollIntoViewIfNeeded();
    await expect(productBefore).toBeVisible();
    await expect(quality.locator(".el-message")).toHaveCount(0, {
      timeout: 8000,
    });
    await quality.screenshot({
      path: "../../artifacts/catalog-revision-history.png",
      fullPage: true,
      animations: "disabled",
    });
    await openRecord(page, "往来单位", partyName);
    await page.getByRole("tab", { name: "变更记录", exact: true }).click();
    const partyBefore = page
      .getByTestId("catalog-history")
      .getByRole("cell", { name: f.customer, exact: true });
    await partyBefore.scrollIntoViewIfNeeded();
    await expect(partyBefore).toBeVisible();
    await page.screenshot({
      path: "../../artifacts/party-revision-history.png",
      fullPage: true,
      animations: "disabled",
    });
    expect(errors).toEqual([]);
  } finally {
    await reviewer.close();
  }
});

test("viewer can inspect revision history but cannot revise through UI or direct HTTP", async ({
  page,
  context,
}) => {
  const viewer = await wholesale(context, "demo.viewer");
  const products = await read(context, "/api/v1/catalog/products");
  const row = products.items[0];
  await openRecord(page, "商品档案", row.name);
  await expect(
    page
      .getByRole("region", { name: "资料详情", exact: true })
      .getByRole("button", { name: "修订资料", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("tab", { name: "变更记录", exact: true }).click();
  await expect(page.getByTestId("catalog-history")).toBeVisible();
  const response = await context.request.post(
    origin + `/api/v1/catalog/products/${row.id}/revise`,
    {
      headers: { ...headers(viewer), "Idempotency-Key": randomUUID() },
      data: {
        expected_version: row.version,
        name: row.name + "非法",
        valid_from: row.valid_from,
        valid_until: row.valid_until,
        manufacturer_name: row.manufacturer_name,
        specification: row.specification,
        approval_number: row.approval_number,
        drug_sale_class: row.drug_sale_class,
        reason: "拒绝越权",
      },
    },
  );
  expect(response.status()).toBe(403);
});
