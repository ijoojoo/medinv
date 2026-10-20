import { test, expect, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { writeFile } from "node:fs/promises";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { resolve } from "node:path";
import {
  wholesale,
  stockedCustomer,
  command,
  read,
  nav,
  origin,
} from "./business-helpers";
import { headers } from "./helpers";

// Compare persisted decimal strings exactly; no floating point money arithmetic.
function units(value: string, scale = 8): bigint {
  expect(value).toMatch(/^-?\d+(\.\d+)?$/);
  const negative = value.startsWith("-");
  const [whole, fraction = ""] = value.replace(/^-/, "").split(".");
  expect(fraction.length).toBeLessThanOrEqual(scale);
  const result = BigInt(whole! + fraction.padEnd(scale, "0"));
  return negative ? -result : result;
}
const sum = (rows: any[], field: string) =>
  rows.reduce(
    (value: bigint, row: any) => value + units(row[field] || "0"),
    0n,
  );

async function openWork(page: Page, number: string, stage: string) {
  await page.goto("/");
  const queue = page.getByRole("region", { name: "进销存业务待办" });
  await expect(
    queue.getByRole("button", { name: "刷新业务待办" }),
  ).toBeEnabled();
  await queue.getByLabel("查找待办").fill(number);
  await queue.getByRole("button", { name: "查询待办", exact: true }).click();
  const row = queue.getByRole("row").filter({ hasText: number });
  await expect(row).toContainText(stage);
  await row.getByRole("button", { name: "查看原单" }).click();
}
async function save(page: Page, reason: string, name = "确认保存") {
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("操作说明").fill(reason);
  await dialog.getByRole("button", { name, exact: true }).click();
  await expect(dialog).not.toBeVisible();
}

test("one lot completes procurement, wholesale, retail, all returns, partial transfer, count and cash settlement", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(240000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext();
  const countIds: string[] = [],
    errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    const quality = await reviewer.newPage();
    quality.on("pageerror", (e) => errors.push(e.message));
    const run = randomUUID().slice(0, 8),
      entity = owner.entities[0].id;
    const warehouses = [];
    for (const code of ["A", "B"])
      warehouses.push(
        await command(context, owner, "/api/v1/warehouses", {
          client_operation_id: randomUUID(),
          entity_id: entity,
          code: `ML-${run}-${code}`,
          name: `主线验收仓-${run}-${code}`,
          location_code: "L1",
        }),
      );
    const [sourceWarehouse, destination] = warehouses;
    const f = await stockedCustomer(context, owner, reviewer, checker, {
      warehouse: sourceWarehouse.id,
      location: sourceWarehouse.location_id,
    });
    const account = await command(
      context,
      owner,
      "/api/v1/settlements/accounts",
      {
        entity_id: entity,
        code: "ML-" + run,
        name: "主线验收现金-" + run,
        method: "CASH",
      },
    );
    const documents: Record<string, any> = { purchase: f.purchase };
    const checkpoints: any[] = [];
    let transitWarehouse = "";
    const items = async (kind: string) =>
      (
        await read(
          context,
          `/api/v1/settlements/items?entity_id=${entity}&kind=${kind}&party_id=${f.party.id}`,
        )
      ).items;
    const purchasePosting = (await items("AP"))[0].posting_id;
    const availability = async (warehouse: string) =>
      (
        await read(
          context,
          `/api/v1/inventory/availability?entity_id=${entity}&warehouse_id=${warehouse}&sku_id=${f.product.sku_id}`,
        )
      ).items;
    async function ledger() {
      const entries: any[] = [];
      // Read back to this scenario's purchase, avoiding assumptions about tenant pagination.
      for (let offset = 0; offset <= 100000; offset += 50) {
        const data = await read(
          context,
          `/api/v1/inventory/ledger?entity_id=${entity}&offset=${offset}`,
        );
        entries.push(
          ...data.items.filter(
            (r: any) => r.name === f.name && r.lot_no === f.lot,
          ),
        );
        if (entries.some((r) => r.posting_id === purchasePosting))
          return entries;
        expect(
          data.has_more,
          "The original purchase must remain in the ledger",
        ).toBe(true);
      }
      throw new Error(
        "Purchase entry not found within the supported ledger range",
      );
    }
    async function checkpoint(
      label: string,
      stock: string[],
      cost: string,
      ar: string,
      ap: string,
    ) {
      const locations = [
        sourceWarehouse.id,
        destination.id,
        ...(transitWarehouse ? [transitWarehouse] : []),
      ];
      const rows = await Promise.all(locations.map(availability));
      for (let i = 0; i < rows.length; i++) {
        expect(sum(rows[i]!, "on_hand"), label + " warehouse " + i).toBe(
          units(stock[i] || "0"),
        );
        expect(sum(rows[i]!, "reserved"), label + " reservations").toBe(0n);
      }
      const entries = await ledger();
      expect(sum(entries, "qty_delta"), label + " stock ledger").toBe(
        rows.flat().reduce((n, r) => n + units(r.on_hand), 0n),
      );
      expect(sum(entries, "amount_delta"), label + " cost ledger").toBe(
        units(cost),
      );
      expect(sum(await items("AR"), "outstanding_amount"), label + " AR").toBe(
        units(ar),
      );
      expect(sum(await items("AP"), "outstanding_amount"), label + " AP").toBe(
        units(ap),
      );
      checkpoints.push({
        label,
        source_qty: stock[0],
        destination_qty: stock[1],
        transit_qty: stock[2] || "0",
        cost_balance: cost,
        ar_outstanding: ar,
        ap_outstanding: ap,
        stock_ledger_rows: entries.length,
      });
    }
    const retailBody = (warehouse: string, qty: string) => ({
      client_operation_id: randomUUID(),
      entity_id: entity,
      warehouse_id: warehouse,
      lines: [
        { sku_id: f.product.sku_id, qty, unit_price: "20", tax_rate: "0.13" },
      ],
    });
    async function reject(path: string, body: unknown, code: string) {
      const response = await context.request.post(origin + path, {
        headers: { ...headers(owner), "Idempotency-Key": randomUUID() },
        data: body,
      });
      expect(response.status()).toBe(409);
      expect((await response.json()).error.code).toBe(code);
    }
    async function retail(warehouse: string, qty: string) {
      const sale = await command(
        context,
        owner,
        "/api/v1/retail/sales",
        retailBody(warehouse, qty),
      );
      await command(context, owner, `/api/v1/retail/sales/${sale.id}/cash`, {
        client_operation_id: randomUUID(),
        expected_version: "1",
        confirmed: true,
        reason: "主线验收已实际收取测试现金",
        account_id: account.id,
        tendered: "100",
      });
      const original = await read(context, `/api/v1/retail/sales/${sale.id}`);
      const body = {
        expected_version: original.sale.version,
        reason: "同批商品核对后交付",
        lines: original.allocations.map((a: any) => ({
          allocation_id: a.id,
          qty: a.allocated_qty,
          lot_no: a.lot_no,
        })),
      };
      const posted = await command(
        context,
        owner,
        `/api/v1/retail/sales/${sale.id}/complete`,
        body,
      );
      // A retry with a different transport key must return the stored posting, not sell again.
      const replay = await command(
        context,
        owner,
        `/api/v1/retail/sales/${sale.id}/complete`,
        body,
      );
      expect(replay).toEqual(posted);
      return { ...sale, posting_id: posted.posting_id };
    }
    async function returnOne(name: string, posting: string, kind: string) {
      const source = await read(context, `/api/v1/returns/sources/${posting}`);
      const originalLine = source.lines.find((r: any) => r.lot_no === f.lot);
      expect(originalLine).toBeTruthy();
      const doc = await command(context, owner, "/api/v1/returns", {
        client_operation_id: randomUUID(),
        source_posting_id: posting,
        reason: "同批连续试用退一件",
        lines: [{ source_posting_line_id: originalLine.id, qty: "1" }],
      });
      documents[name] = doc;
      if (kind !== "PURCHASE") {
        const before = await availability(sourceWarehouse.id);
        await openWork(page, doc.number, "退回品待收货");
        await page
          .getByRole("button", { name: "登记隔离收货", exact: true })
          .click();
        const dialog = page.getByRole("dialog");
        await dialog.getByLabel("收货数量 1", { exact: true }).fill("1");
        await dialog.getByLabel("实物批号 1", { exact: true }).fill(f.lot);
        await dialog.getByLabel("隔离保管位置").fill("主线验收隔离柜");
        await dialog.getByLabel("我已核对本次全部实物交接完成").check();
        await save(page, "实际收到退回品，隔离待验");
        expect(sum(await availability(sourceWarehouse.id), "on_hand")).toBe(
          sum(before, "on_hand"),
        );
      }
      await openWork(
        quality,
        doc.number,
        kind === "PURCHASE" ? "采购退货待审核" : "退回品待验收",
      );
      await quality
        .getByRole("button", { name: "独立复核退货", exact: true })
        .click();
      const dialog = quality.getByRole("dialog");
      await dialog.getByLabel("实物核对数量 1", { exact: true }).fill("1");
      await dialog.getByLabel("实物批号 1", { exact: true }).fill(f.lot);
      await save(quality, "独立核对退回数量、批号与质量");
      await quality
        .getByRole("button", {
          name: kind === "PURCHASE" ? "确认退货出库" : "确认退货入库",
          exact: true,
        })
        .click();
      await save(quality, "确认本次退货实物过账");
      const result = await read(context, `/api/v1/returns/${doc.id}`);
      expect(result.document.state).toBe("POSTED");
      if (kind === "RETAIL") {
        await openWork(page, doc.number, "零售退货待退款");
        await page
          .getByRole("button", { name: "登记本次零售退款", exact: true })
          .click();
        const refund = page.getByRole("dialog");
        await refund.getByLabel("退款核对说明").fill("按原单退还测试现金20元");
        await refund.getByLabel("我已按本次退货金额实际退还现金").check();
        await refund
          .getByRole("button", { name: "确认登记零售退款", exact: true })
          .click();
        await expect(refund).not.toBeVisible();
      }
    }

    await test.step("采购10件进入同一批次库存和供应商应付", async () => {
      await checkpoint("采购入库10件", ["10", "0"], "115.04", "0", "130");
    });
    await test.step("批发4件并独立复核出库", async () => {
      await command(
        context,
        owner,
        `/api/v1/customer-credit/${f.party.id}?entity_id=${entity}`,
        {
          expected_version: "0",
          credit_limit: "1000",
          valid_until: `${f.year + 1}-12-31`,
          reason: "主线验收授信",
        },
      );
      const order = await command(context, owner, "/api/v1/sales-orders", {
        entity_id: entity,
        customer_id: f.party.id,
        warehouse_id: sourceWarehouse.id,
        delivery_address: "主线验收收货点",
        lines: [
          {
            sku_id: f.product.sku_id,
            qty: "4",
            unit_price: "20",
            tax_rate: "0.13",
          },
        ],
      });
      documents.wholesale = order;
      await command(
        reviewer,
        checker,
        `/api/v1/sales-orders/${order.id}/approve`,
        { expected_version: "1", reason: "独立审核销售约定" },
      );
      let detail = await read(context, `/api/v1/sales-orders/${order.id}`);
      const shipment = await command(
        context,
        owner,
        `/api/v1/sales-orders/${order.id}/shipments`,
        {
          expected_version: detail.order.version,
          client_operation_id: randomUUID(),
          lines: [{ order_line_id: detail.lines[0].id, qty: "4" }],
        },
      );
      detail = await read(context, `/api/v1/sales-orders/${order.id}`);
      const lines = detail.shipments[0].lines.map((r: any) => ({
        shipment_line_id: r.id,
        qty: r.qty,
        lot_no: r.lot_no,
      }));
      await command(context, owner, `/api/v1/shipments/${shipment.id}/pick`, {
        expected_version: "1",
        lines,
        note: "批次实物拣货",
      });
      await command(
        reviewer,
        checker,
        `/api/v1/shipments/${shipment.id}/verify`,
        {
          expected_version: "2",
          lines,
          note: "独立核查",
          shipping_ref: "ML-" + run,
        },
      );
      const posted = await command(
        reviewer,
        checker,
        `/api/v1/shipments/${shipment.id}/post`,
        { expected_version: "3" },
      );
      documents.shipment = { ...shipment, posting_id: posted.posting_id };
      await checkpoint("批发出库4件", ["6", "0"], "69.024", "80", "130");
    });
    await test.step("零售2件、批发退1件、零售退1件并退现金", async () => {
      documents.retail = await retail(sourceWarehouse.id, "2");
      await checkpoint("零售交付2件", ["4", "0"], "46.016", "80", "130");
      await returnOne(
        "wholesale_return",
        documents.shipment.posting_id,
        "SALES",
      );
      await checkpoint("批发退回1件", ["5", "0"], "57.52", "60", "130");
      await returnOne("retail_return", documents.retail.posting_id, "RETAIL");
      await checkpoint(
        "零售退回1件并退款20元",
        ["6", "0"],
        "69.024",
        "60",
        "130",
      );
    });
    await test.step("采购退1件，冲减原应付", async () => {
      await returnOne("purchase_return", purchasePosting, "PURCHASE");
      await checkpoint("采购退货1件", ["5", "0"], "57.52", "60", "117");
    });
    await test.step("3件跨仓调拨，分两次收货并检查在途拦截", async () => {
      const candidates = await read(
        context,
        `/api/v1/transfers/candidates?entity_id=${entity}&warehouse_id=${sourceWarehouse.id}&search=${f.lot}`,
      );
      const doc = await command(context, owner, "/api/v1/transfers", {
        client_operation_id: randomUUID(),
        entity_id: entity,
        source_warehouse_id: sourceWarehouse.id,
        destination_warehouse_id: destination.id,
        destination_location_id: destination.location_id,
        reason: "同批商品调入门店",
        lines: [{ balance_id: candidates.items[0].id, qty: "3" }],
      });
      documents.transfer = doc;
      await openWork(quality, doc.number, "调拨待审核");
      await quality
        .getByRole("button", { name: "独立审核调拨", exact: true })
        .click();
      await save(quality, "独立核对调拨路线与数量", "保存");
      await openWork(page, doc.number, "调拨待发出");
      await page
        .getByRole("button", { name: "确认调拨发出", exact: true })
        .click();
      await save(page, "实际发运3件", "保存");
      const detail = await read(context, `/api/v1/transfers/${doc.id}`);
      transitWarehouse = detail.document.transit_warehouse_id;
      await checkpoint("调拨发出3件", ["2", "0", "3"], "57.52", "60", "117");
      expect(sum(await availability(transitWarehouse), "available")).toBe(0n);
      for (const qty of ["2", "1"]) {
        await openWork(page, doc.number, "调拨待收货");
        await page
          .getByRole("button", { name: "登记本次收货", exact: true })
          .click();
        const dialog = page.getByRole("dialog");
        await dialog.getByLabel("本次收货数量 1", { exact: true }).fill(qty);
        await dialog.getByLabel("实物批号 1", { exact: true }).fill(f.lot);
        await dialog
          .getByLabel("收货质量 1", { exact: true })
          .selectOption("QUALIFIED");
        await save(page, "按本次实际到货数量入库", "保存");
        await checkpoint(
          qty === "2" ? "首次调拨收货2件" : "再次调拨收齐1件",
          ["2", qty === "2" ? "2" : "3", qty === "2" ? "1" : "0"],
          "57.52",
          "60",
          "117",
        );
        if (qty === "2") {
          const candidates = await read(
            context,
            `/api/v1/stock-counts/candidates?entity_id=${entity}&warehouse_id=${destination.id}&search=${f.lot}`,
          );
          await reject(
            "/api/v1/stock-counts",
            {
              client_operation_id: randomUUID(),
              entity_id: entity,
              warehouse_id: destination.id,
              balance_ids: [candidates.items[0].id],
              reason: "仍有同批在途时禁止盘点",
            },
            "COUNT_IN_TRANSIT",
          );
        }
      }
    });
    await test.step("目的仓盘亏1件，验证同批跨仓冻结和过账恢复", async () => {
      const candidates = await read(
        context,
        `/api/v1/stock-counts/candidates?entity_id=${entity}&warehouse_id=${destination.id}&search=${f.lot}`,
      );
      const doc = await command(context, owner, "/api/v1/stock-counts", {
        client_operation_id: randomUUID(),
        entity_id: entity,
        warehouse_id: destination.id,
        balance_ids: [candidates.items[0].id],
        reason: "到店后盘点复核",
      });
      countIds.push(doc.id);
      documents.count = doc;
      for (const warehouse of [sourceWarehouse.id, destination.id]) {
        expect(
          (await availability(warehouse))[0].ineligibility_codes,
        ).toContain("COUNT_FROZEN");
        await reject(
          "/api/v1/retail/sales",
          retailBody(warehouse, "1"),
          "COUNT_FROZEN",
        );
      }
      await openWork(page, doc.number, "盘点待实盘");
      await page
        .getByRole("button", { name: "录入实盘并提交", exact: true })
        .click();
      const dialog = page.getByRole("dialog");
      await dialog.getByLabel("实盘数量 1", { exact: true }).fill("2");
      await dialog.getByLabel("实物批号 1", { exact: true }).fill(f.lot);
      await dialog
        .getByLabel("实盘依据 1", { exact: true })
        .fill("复盘确认2件，测试差异1件");
      await save(page, "录入本次全部实盘数量", "保存");
      await openWork(quality, doc.number, "盘点待审核");
      await quality
        .getByRole("button", { name: "独立审核盘点", exact: true })
        .click();
      await save(quality, "独立核对差异及成本", "保存");
      await quality
        .getByRole("button", { name: "确认盘点过账", exact: true })
        .click();
      await save(quality, "确认本次盘亏并结单", "保存");
      await checkpoint("目的仓盘亏1件", ["2", "2", "0"], "46.016", "60", "117");
      for (const warehouse of [sourceWarehouse.id, destination.id])
        expect(sum(await availability(warehouse), "available")).toBe(
          units("2"),
        );
      documents.retail_after_count = await retail(destination.id, "1");
      await checkpoint(
        "盘点后目的仓再售1件",
        ["2", "1", "0"],
        "34.512",
        "60",
        "117",
      );
    });
    await test.step("结清客户60元与供应商117元，核对所有已完成单据和资金登记", async () => {
      for (const [kind, amount, direction] of [
        ["AR", "60", "RECEIPT"],
        ["AP", "117", "PAYMENT"],
      ]) {
        const item = (await items(kind!))[0];
        const cash = await command(
          context,
          owner,
          "/api/v1/settlements/cash-documents",
          {
            client_operation_id: randomUUID(),
            entity_id: entity,
            party_id: f.party.id,
            account_id: account.id,
            direction,
            amount,
            reference: `ML-${run}-${kind}`,
            occurred_on: new Intl.DateTimeFormat("en-CA", {
              timeZone: "Asia/Shanghai",
              year: "numeric",
              month: "2-digit",
              day: "2-digit",
            }).format(new Date()),
            note: "仅软件验收，核对测试现金实际收付款",
            confirmed: true,
          },
        );
        await command(context, owner, "/api/v1/settlements/allocations", {
          client_operation_id: randomUUID(),
          cash_document_id: cash.id,
          expected_cash_version: "1",
          allocations: [
            {
              settlement_item_id: item.id,
              expected_item_version: item.version,
              amount,
            },
          ],
        });
        documents[kind === "AR" ? "collection" : "payment"] = cash;
      }
      await checkpoint("收付款及核销完成", ["2", "1", "0"], "34.512", "0", "0");
      const originalRetail = await read(
        context,
        `/api/v1/retail/sales/${documents.retail.id}`,
      );
      const secondRetail = await read(
        context,
        `/api/v1/retail/sales/${documents.retail_after_count.id}`,
      );
      expect(originalRetail.returns_summary).toEqual({
        returned_amount: "20.00",
        refunded_amount: "20.00",
        pending_refund_amount: "0.00",
      });
      expect(originalRetail.payments).toHaveLength(1);
      expect(secondRetail.payments).toHaveLength(1);
      expect(sum(originalRetail.payments, "amount")).toBe(units("40"));
      expect(sum(secondRetail.payments, "amount")).toBe(units("20"));
      for (const doc of Object.values(documents)) {
        const work = await read(
          context,
          `/api/v1/work-queue?entity_id=${entity}&search=${doc.number}`,
        );
        expect(work.items, doc.number + " must have no remaining work").toEqual(
          [],
        );
      }
      const entries = await ledger();
      expect(entries).toHaveLength(14);
      await page.goto("/");
      await nav(page, "库存台账");
      await expect(
        page.getByRole("row").filter({ hasText: f.lot }),
      ).toHaveCount(14);
      await expect(
        page
          .getByRole("row")
          .filter({ hasText: f.lot })
          .filter({ hasText: "盘亏出库" }),
      ).toHaveCount(1);
      await expect(page.locator(".el-message")).toHaveCount(0);
      await page.screenshot({
        path: "../../artifacts/mainline-trial-ledger.png",
        fullPage: true,
        animations: "disabled",
      });
      expect(errors).toEqual([]);
      await writeFile(
        "../../artifacts/mainline-trial-reconciliation.json",
        JSON.stringify(
          {
            recorded_at: new Date().toISOString(),
            run,
            tenant_id: owner.tenant_id,
            entity_id: entity,
            sku_id: f.product.sku_id,
            party_id: f.party.id,
            account_id: account.id,
            product: f.name,
            lot: f.lot,
            warehouses: {
              source: sourceWarehouse,
              destination,
              transit_id: transitWarehouse,
            },
            documents: Object.fromEntries(
              Object.entries(documents).map(([name, doc]) => [
                name,
                {
                  id: doc.id,
                  number: doc.number,
                  ...(doc.posting_id ? { posting_id: doc.posting_id } : {}),
                },
              ]),
            ),
            checkpoints,
            final_stock_qty: "3",
            final_cost_balance: "34.51200000",
            ar_outstanding: "0.00",
            ap_outstanding: "0.00",
            retail_net_receipts: "40.00",
            cash_receipt: "60.00",
            cash_payment: "117.00",
            cash_record_net_change: "-17.00",
            stock_ledger: entries,
            checks: [
              "real HTTP and browser UI; no direct database writes",
              "same entity, SKU and lot throughout",
              "independent reviewer",
              "stock/cost/AR/AP reconciled after every posting",
              "in-transit stock blocks counting",
              "count freezes both warehouses",
              "retail posting replay creates no duplicate movement",
              "all scenario work items closed",
            ],
          },
          null,
          2,
        ) + "\n",
        "utf8",
      );
      await promisify(execFile)(
        resolve(
          process.platform === "win32"
            ? "../../backend/.venv/Scripts/python.exe"
            : "../../backend/.venv/bin/python",
        ),
        [resolve("../../scripts/verify_mainline_trial.py")],
        { env: { ...process.env, PYTHONUTF8: "1" }, timeout: 30000 },
      );
    });
  } finally {
    for (const id of countIds) {
      const data = await read(context, `/api/v1/stock-counts/${id}`);
      if (["COUNTING", "SUBMITTED", "APPROVED"].includes(data.document.state))
        await command(context, owner, `/api/v1/stock-counts/${id}/cancel`, {
          expected_version: data.document.version,
          reason: "清理未完成的验收盘点冻结",
        });
    }
    await reviewer.close();
  }
});
