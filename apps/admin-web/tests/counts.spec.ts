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

async function save(page: Page, reason: string) {
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("操作说明").fill(reason);
  await dialog.getByRole("button", { name: "保存", exact: true }).click();
  await expect(dialog).not.toBeVisible();
}
async function capture(page: Page, name: string) {
  await expect(page.locator(".el-message")).toHaveCount(0, { timeout: 8000 });
  await expect(page.locator(".el-loading-mask:visible")).toHaveCount(0);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: "../../artifacts/" + name + ".png",
    fullPage: true,
    animations: "disabled",
  });
}

test("count loss and gain through independent approval, stock/cost posting and rejection in real UI", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(180000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext();
  const checker = await wholesale(reviewer, "demo.quality");
  const qualityPage = await reviewer.newPage();
  const ids: string[] = [];
  try {
    const f = await stockedCustomer(context, owner, reviewer, checker);
    for (const [step, actual] of [
      ["loss", "8"],
      ["gain", "10"],
      ["reject", "7"],
    ]) {
      await page.goto("/");
      await nav(page, "库存盘点");
      await page.getByRole("button", { name: "新建盘点", exact: true }).click();
      await page.getByLabel("搜索商品或批号").fill(f.name);
      await page.getByRole("button", { name: "查询批次", exact: true }).click();
      await page.getByRole("checkbox", { name: new RegExp(f.name) }).check();
      const reason = "盘点浏览器验收-" + f.suffix + "-" + step;
      await page.getByLabel("盘点说明", { exact: true }).fill(reason);
      await page
        .getByRole("button", { name: "建立盘点快照", exact: true })
        .click();
      await expect(
        page.getByRole("button", { name: "录入实盘并提交", exact: true }),
      ).toBeVisible();
      const list = await read(
        context,
        `/api/v1/stock-counts?entity_id=${f.entity}`,
      );
      const doc = list.items.find((r: any) => r.reason === reason);
      expect(doc).toBeTruthy();
      ids.push(doc.id);
      const frozen = await read(
        context,
        `/api/v1/inventory/availability?entity_id=${f.entity}&warehouse_id=${f.warehouse}&sku_id=${f.product.sku_id}`,
      );
      expect(frozen.items[0].available).toBe("0.000000");
      expect(frozen.items[0].ineligibility_codes).toContain("COUNT_FROZEN");
      const attemptedSale = await context.request.post(
        origin + "/api/v1/retail/sales",
        {
          headers: { ...headers(owner), "Idempotency-Key": randomUUID() },
          data: {
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
          },
        },
      );
      expect(attemptedSale.status()).toBe(409);
      expect((await attemptedSale.json()).error.code).toBe("COUNT_FROZEN");

      await page
        .getByRole("button", { name: "录入实盘并提交", exact: true })
        .click();
      const dialog = page.getByRole("dialog");
      await dialog.getByLabel("实盘数量 1", { exact: true }).fill(actual!);
      await dialog.getByLabel("实物批号 1", { exact: true }).fill(f.lot);
      await dialog
        .getByLabel("实盘依据 1", { exact: true })
        .fill("实际清点并复核，差异用于软件验收");
      await save(page, "已核对本次全部库位批次");
      await expect(page.locator(".el-tag")).toHaveText("待审核");
      await expect(
        page.getByRole("button", { name: "独立审核盘点", exact: true }),
      ).toHaveCount(0);
      const stockBefore = await read(
        context,
        `/api/v1/inventory/availability?entity_id=${f.entity}&warehouse_id=${f.warehouse}&sku_id=${f.product.sku_id}`,
      );
      expect(stockBefore.items[0].on_hand).toBe(
        step === "gain" ? "8.000000" : "10.000000",
      );
      await qualityPage.goto("/");
      await nav(qualityPage, "库存盘点");
      await qualityPage
        .getByRole("row")
        .filter({ hasText: doc.number })
        .getByRole("button", { name: "盘点详情" })
        .click();
      await qualityPage
        .getByRole("button", { name: "独立审核盘点", exact: true })
        .click();
      if (step === "reject")
        await qualityPage
          .getByRole("dialog")
          .getByLabel("审核结论")
          .selectOption("REJECT");
      await save(
        qualityPage,
        step === "reject" ? "证据不充分，退回复盘" : "独立核查数量及成本依据",
      );
      if (step === "reject") {
        await expect(qualityPage.locator(".el-tag")).toHaveText("已取消");
        await capture(qualityPage, "count-rejected");
      } else {
        await qualityPage
          .getByRole("button", { name: "确认盘点过账", exact: true })
          .click();
        await save(qualityPage, "核对库存与成本差异后过账");
        await expect(qualityPage.locator(".el-tag")).toHaveText("已过账");
        await expect(qualityPage.getByTestId("count-result")).toContainText(
          "23.008 元",
        );
        await capture(qualityPage, "count-" + step + "-posted");
      }
      const after = await read(
        context,
        `/api/v1/inventory/availability?entity_id=${f.entity}&warehouse_id=${f.warehouse}&sku_id=${f.product.sku_id}`,
      );
      expect(after.items[0].on_hand).toBe(
        step === "loss" ? "8.000000" : "10.000000",
      );
      const detail = await read(context, `/api/v1/stock-counts/${doc.id}`);
      if (step !== "reject")
        expect(detail.result.cost_delta).toBe(
          step === "loss" ? "-23.00800000" : "23.00800000",
        );
    }
    await page.goto("/");
    await nav(page, "库存台账");
    await expect(
      page
        .getByRole("row")
        .filter({ hasText: f.name })
        .filter({ hasText: "盘亏出库" }),
    ).toHaveCount(1);
    await expect(
      page
        .getByRole("row")
        .filter({ hasText: f.name })
        .filter({ hasText: "盘盈入库" }),
    ).toHaveCount(1);
  } finally {
    for (const id of ids) {
      const data = await read(context, "/api/v1/stock-counts/" + id);
      if (["COUNTING", "SUBMITTED", "APPROVED"].includes(data.document.state))
        await command(context, owner, `/api/v1/stock-counts/${id}/cancel`, {
          expected_version: data.document.version,
          reason: "验收结束取消未完成测试盘点",
        });
    }
    await reviewer.close();
  }
});

test("viewer can inspect counts but cannot create or post through direct HTTP", async ({
  page,
  context,
}) => {
  const viewer = await wholesale(context, "demo.viewer");
  await page.goto("/");
  await nav(page, "库存盘点");
  await expect(
    page.getByRole("button", { name: "新建盘点", exact: true }),
  ).toHaveCount(0);
  const options = await read(
    context,
    `/api/v1/stock-counts/options?entity_id=${viewer.entities[0].id}`,
  );
  const response = await context.request.post(origin + "/api/v1/stock-counts", {
    headers: { ...headers(viewer), "Idempotency-Key": randomUUID() },
    data: {
      client_operation_id: randomUUID(),
      entity_id: viewer.entities[0].id,
      warehouse_id: options.warehouses[0].id,
      balance_ids: [randomUUID()],
      reason: "越权必须拒绝",
    },
  });
  expect(response.status()).toBe(403);
  const post = await context.request.post(
    origin + `/api/v1/stock-counts/${randomUUID()}/post`,
    {
      headers: { ...headers(viewer), "Idempotency-Key": randomUUID() },
      data: { expected_version: "1", reason: "越权过账必须拒绝" },
    },
  );
  expect(post.status()).toBe(403);
});
