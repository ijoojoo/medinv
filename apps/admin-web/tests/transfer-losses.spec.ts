import { test, expect, type BrowserContext, type Page } from "@playwright/test";
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

async function inTransit(
  context: BrowserContext,
  owner: any,
  reviewer: BrowserContext,
  checker: any,
) {
  const f = await stockedCustomer(context, owner, reviewer, checker);
  const dest = await command(context, owner, "/api/v1/warehouses", {
    client_operation_id: randomUUID(),
    entity_id: f.entity,
    code: "LOSS-" + f.suffix,
    name: "在途损失验收仓-" + f.suffix,
    location_code: "LOSS-A",
  });
  const candidates = await read(
    context,
    `/api/v1/transfers/candidates?entity_id=${f.entity}&warehouse_id=${f.warehouse}&search=${f.lot}`,
  );
  const transfer = await command(context, owner, "/api/v1/transfers", {
    client_operation_id: randomUUID(),
    entity_id: f.entity,
    source_warehouse_id: f.warehouse,
    destination_warehouse_id: dest.id,
    destination_location_id: dest.location_id,
    reason: "在途差异闭环验收",
    lines: [{ balance_id: candidates.items[0].id, qty: "10" }],
  });
  await command(reviewer, checker, `/api/v1/transfers/${transfer.id}/review`, {
    expected_version: "1",
    decision: "APPROVE",
    reason: "核对调拨",
  });
  await command(context, owner, `/api/v1/transfers/${transfer.id}/dispatch`, {
    expected_version: "2",
    reason: "实物发出",
  });
  const detail = await read(context, `/api/v1/transfers/${transfer.id}`);
  return { ...f, dest, transfer, line: detail.lines[0] };
}
async function openTransfer(page: Page, number: string) {
  await page.goto("/");
  await nav(page, "仓库调拨");
  await page
    .getByRole("row")
    .filter({ hasText: number })
    .getByRole("button", { name: "调拨详情", exact: true })
    .click();
}
async function saveLoss(page: Page, reason: string) {
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("操作说明", { exact: true }).fill(reason);
  await dialog
    .getByRole("button", { name: "保存损失处理", exact: true })
    .click();
}

test("partial physical receipt plus independently approved loss closes transit with correct cost in UI", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(180000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext();
  const checker = await wholesale(reviewer, "demo.quality");
  const quality = await reviewer.newPage();
  try {
    const f = await inTransit(context, owner, reviewer, checker);
    await openTransfer(page, f.transfer.number);
    await page
      .getByRole("button", { name: "登记本次收货", exact: true })
      .click();
    let dialog = page.getByRole("dialog");
    await dialog.getByLabel("本次收货数量 1", { exact: true }).fill("8");
    await dialog.getByLabel("实物批号 1", { exact: true }).fill(f.lot);
    await dialog
      .getByLabel("操作说明", { exact: true })
      .fill("实收8件，运输短少2件待核查");
    await dialog.getByRole("button", { name: "保存", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await expect(page.locator(".el-tag")).toHaveText("部分收货");
    await page
      .getByRole("button", { name: "申请在途损失", exact: true })
      .click();
    dialog = page.getByRole("dialog");
    await dialog.getByLabel("损失数量", { exact: true }).fill("2");
    await dialog.getByLabel("确认批号", { exact: true }).fill(f.lot);
    await dialog
      .getByLabel("损失原因", { exact: true })
      .fill("承运核实运输短少2件");
    await dialog
      .getByLabel("核查依据", { exact: true })
      .fill("软件验收运输差异记录 LOSS-" + f.suffix);
    await dialog
      .getByRole("button", { name: "提交损失申请", exact: true })
      .click();
    await expect(dialog.getByTestId("loss-state")).toHaveText("待审核");
    await expect(
      dialog.getByRole("button", { name: "独立审核损失", exact: true }),
    ).toHaveCount(0);
    const claims = await read(
      context,
      `/api/v1/transfers/${f.transfer.id}/losses`,
    );
    const claim = claims.items[0];
    await dialog.getByRole("button", { name: "关闭详情", exact: true }).click();
    await openTransfer(quality, f.transfer.number);
    await quality
      .getByRole("button", { name: claim.number, exact: true })
      .click();
    dialog = quality.getByRole("dialog");
    await dialog
      .getByRole("button", { name: "独立审核损失", exact: true })
      .click();
    await saveLoss(quality, "独立核对发运、实收及承运核查记录");
    await expect(dialog.getByTestId("loss-state")).toHaveText("待核销");
    await dialog
      .getByRole("button", { name: "确认损失核销", exact: true })
      .click();
    await saveLoss(quality, "确认已核实2件损失，核销在途及成本");
    await expect(dialog.getByTestId("loss-state")).toHaveText("已核销");
    await expect(
      dialog.getByText("23.00800000 元", { exact: true }),
    ).toBeVisible();
    await expect(quality.locator(".el-message")).toHaveCount(0, {
      timeout: 8000,
    });
    await quality.screenshot({
      path: "../../artifacts/transfer-loss-posted.png",
      fullPage: true,
      animations: "disabled",
    });
    await dialog.getByRole("button", { name: "关闭详情", exact: true }).click();
    await expect(quality.locator(".el-tag")).toHaveText("已结清（含损失）");
    await expect(
      quality.getByRole("button", { name: "登记本次收货", exact: true }),
    ).toHaveCount(0);
    const doc = await read(context, `/api/v1/transfers/${f.transfer.id}`);
    expect(doc.lines[0].received_qty).toBe("8.000000");
    expect(doc.lines[0].lost_qty).toBe("2.000000");
    expect(doc.lines[0].remaining_qty).toBe("0.000000");
    expect(doc.receipts).toHaveLength(1);
    await quality.evaluate(() => window.scrollTo(0, 0));
    await quality.screenshot({
      path: "../../artifacts/transfer-loss-closed.png",
      fullPage: true,
      animations: "disabled",
    });
    const repeat = await command(
      reviewer,
      checker,
      `/api/v1/transfers/${f.transfer.id}/losses/${claim.id}/post`,
      { expected_version: "2", reason: "确认已核实2件损失，核销在途及成本" },
    );
    expect(repeat.loss_cost).toBe("23.00800000");
    const stock = await read(
      context,
      `/api/v1/inventory/availability?entity_id=${f.entity}&warehouse_id=${f.dest.id}&sku_id=${f.product.sku_id}`,
    );
    expect(stock.items.find((r: any) => r.eligible).available).toBe("8.000000");
    const over = await context.request.post(
      origin + `/api/v1/transfers/${f.transfer.id}/receive`,
      {
        headers: { ...headers(owner), "Idempotency-Key": randomUUID() },
        data: {
          client_operation_id: randomUUID(),
          expected_version: doc.document.version,
          reason: "不能将损失再记收货",
          lines: [
            {
              line_id: f.line.id,
              qty: "1",
              lot_no: f.lot,
              quality_status: "QUALIFIED",
            },
          ],
        },
      },
    );
    expect(over.status()).toBe(409);
  } finally {
    await reviewer.close();
  }
});

test("late physical arrival leaves pending loss cancellable without recording a false loss", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(120000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext();
  const checker = await wholesale(reviewer, "demo.quality");
  try {
    const f = await inTransit(context, owner, reviewer, checker);
    const claim = await command(
      context,
      owner,
      `/api/v1/transfers/${f.transfer.id}/losses`,
      {
        client_operation_id: randomUUID(),
        expected_version: "3",
        line_id: f.line.id,
        qty: "2",
        lot_no: f.lot,
        reason: "初步运输短少",
        evidence: "待进一步核对",
      },
    );
    await command(
      context,
      owner,
      `/api/v1/transfers/${f.transfer.id}/receive`,
      {
        client_operation_id: randomUUID(),
        expected_version: "3",
        reason: "延迟货物找到，实收10件",
        lines: [
          {
            line_id: f.line.id,
            qty: "10",
            lot_no: f.lot,
            quality_status: "QUALIFIED",
          },
        ],
      },
    );
    await openTransfer(page, f.transfer.number);
    await expect(page.locator(".el-tag")).toHaveText("已收齐");
    await page.getByRole("button", { name: claim.number, exact: true }).click();
    const dialog = page.getByRole("dialog");
    await expect(
      dialog.getByText(/调拨已结清，本申请不能继续核销/),
    ).toBeVisible();
    await dialog
      .getByRole("button", { name: "取消损失申请", exact: true })
      .click();
    await saveLoss(page, "延迟货物已实收，取消损失申请");
    await expect(dialog.getByTestId("loss-state")).toHaveText("已取消");
    const data = await read(context, `/api/v1/transfers/${f.transfer.id}`);
    expect(data.lines[0].lost_qty).toBe("0");
    expect(data.lines[0].received_qty).toBe("10.000000");
  } finally {
    await reviewer.close();
  }
});

test("viewer cannot request or post a transit loss even through direct API", async ({
  context,
}) => {
  const viewer = await wholesale(context, "demo.viewer");
  const transfers = await read(
    context,
    `/api/v1/transfers?entity_id=${viewer.entities[0].id}`,
  );
  const tid = transfers.items[0].id;
  expect(
    (await read(context, `/api/v1/transfers/${tid}/losses`)).items,
  ).toBeDefined();
  for (const [path, data] of [
    [
      `/api/v1/transfers/${tid}/losses`,
      {
        expected_version: "1",
        reason: "拒绝越权",
        client_operation_id: randomUUID(),
        line_id: randomUUID(),
        qty: "1",
        lot_no: "X",
        evidence: "X",
      },
    ],
    [
      `/api/v1/transfers/${tid}/losses/${randomUUID()}/post`,
      { expected_version: "1", reason: "拒绝越权" },
    ],
  ] as const) {
    const response = await context.request.post(origin + path, {
      headers: { ...headers(viewer), "Idempotency-Key": randomUUID() },
      data,
    });
    expect(response.status()).toBe(403);
  }
});
