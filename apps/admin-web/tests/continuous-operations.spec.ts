import { test, expect, type BrowserContext, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { resolve } from "node:path";
import { writeFile } from "node:fs/promises";
import { login, headers } from "./helpers";
import { command, read, nav, origin } from "./business-helpers";

type Actor = { context: BrowserContext; page: Page; session: any };
const units = (value: string) => {
  const [whole, fraction = ""] = value.replace(/^-/, "").split(".");
  return (
    (value.startsWith("-") ? -1n : 1n) *
    BigInt(whole! + fraction.padEnd(8, "0"))
  );
};
const sum = (rows: any[], field: string) =>
  rows.reduce(
    (total: bigint, row: any) => total + units(row[field] || "0"),
    0n,
  );

async function original(a: Actor, number: string, kind: string) {
  await a.page.goto("/");
  const queue = a.page.getByRole("region", { name: "进销存业务待办" });
  await queue.getByLabel("待办环节").selectOption(kind);
  await expect(
    queue.getByRole("button", { name: "查询待办", exact: true }),
  ).toBeEnabled();
  await queue.getByLabel("查找待办").fill(number);
  await queue.getByRole("button", { name: "查询待办", exact: true }).click();
  const row = queue.getByRole("row").filter({ hasText: number });
  await expect(row).toHaveCount(1);
  await row.getByRole("button", { name: "查看原单" }).click();
}
async function confirm(a: Actor, reason: string, button = "确认保存") {
  const dialog = a.page.getByRole("dialog");
  await dialog.getByLabel("操作说明").fill(reason);
  await dialog.getByRole("button", { name: button, exact: true }).click();
  await expect(dialog).not.toBeVisible();
}

test("one enterprise continues trading across seven roles, lost responses, returns, stock counts and settlement", async ({
  browser,
}) => {
  test.setTimeout(240000);
  const run = randomUUID(),
    suffix = run.slice(0, 8),
    year = new Date().getFullYear();
  const roles: Record<string, string> = {
    owner: "OWNER",
    purchase: "PURCHASER",
    sales: "SALES",
    warehouse: "WAREHOUSE",
    quality: "QUALITY",
    cashier: "CASHIER",
    finance: "FINANCE",
  };
  const team: Record<string, Actor> = {},
    errors: string[] = [];
  const documents: Record<string, any> = {},
    checkpoints: any[] = [],
    recoveries: any[] = [];
  const python = resolve(
    process.platform === "win32"
      ? "../../backend/.venv/Scripts/python.exe"
      : "../../backend/.venv/bin/python",
  );
  const execute = (args: string[]) =>
    promisify(execFile)(python, args, {
      cwd: resolve("../.."),
      env: { ...process.env, PYTHONUTF8: "1" },
      timeout: 30000,
    });
  const ids = JSON.parse(
    (
      await execute([
        "-c",
        "import json; from uuid import uuid5,NAMESPACE_URL; from scripts.dev_accounts import ACCOUNTS; print(json.dumps({n:str(uuid5(NAMESPACE_URL,'medinv-dev/'+n)) for n,_ in ACCOUNTS}))",
      ])
    ).stdout,
  );
  const manifest = {
    request_id: run,
    tenant_name: "连续营业企业-" + suffix,
    entity_name: "连续营业主体",
    credit_code: "DEMO-" + suffix,
    warehouse_code: "MAIN",
    warehouse_name: "营业总仓",
    location_code: "A1",
    reason: "同企业跨岗营业与中断恢复验收",
    members: Object.entries(roles).map(([name, role]) => ({
      principal_id: ids["demo." + name],
      role_codes: [role],
    })),
  };
  const manifestPath = resolve(
    "../../artifacts/continuous-operations-manifest.json",
  );
  await writeFile(manifestPath, JSON.stringify(manifest, null, 2) + "\n");
  const opened = JSON.parse(
    (await execute(["scripts/provision_enterprise.py", manifestPath])).stdout,
  );
  const entity = opened.entity_id,
    lot = "DAILY-" + suffix,
    name = "连续营业器械-" + suffix;
  try {
    for (const role of Object.keys(roles)) {
      const context = await browser.newContext(),
        page = await context.newPage();
      page.on("pageerror", (e) => errors.push(e.message));
      team[role] = { context, page, session: null };
      const current = await login(context, "demo." + role);
      const switched = await context.request.post(origin + "/auth/tenant", {
        headers: headers(current),
        data: { tenant_id: opened.tenant_id },
      });
      expect(switched.status()).toBe(200);
      team[role]!.session = (await switched.json()).data;
      expect(team[role]!.session.role_codes).toEqual([roles[role]]);
    }
    const owner = team.owner!,
      purchase = team.purchase!,
      sales = team.sales!,
      warehouse = team.warehouse!,
      quality = team.quality!,
      cashier = team.cashier!,
      finance = team.finance!;
    const send = (a: Actor, path: string, body: unknown) =>
      command(a.context, a.session, path, body);
    const get = (a: Actor, path: string) => read(a.context, path);

    // Ordinary preparation uses real role-scoped APIs. Critical handoffs and recovery use the UI.
    const product = await send(purchase, "/api/v1/catalog/products", {
      code: "DAILY-P",
      name,
      category: "DEVICE",
      manufacturer_name: "软件测试厂家",
      specification: "单件",
      approval_number: "DEMO-ONLY",
      valid_from: `${year}-01-01`,
      valid_until: `${year + 2}-12-31`,
      sku_code: "DAILY-SKU",
      base_unit: "个",
      qty_scale: 0,
    });
    const party = await send(purchase, "/api/v1/catalog/parties", {
      entity_id: entity,
      code: "DAILY-PARTY",
      name: "连续营业往来单位",
      credit_code: "DEMO-ONLY",
      kind: "BOTH",
      license_no: "DEMO-ONLY",
      valid_from: `${year}-01-01`,
      valid_until: `${year + 2}-12-31`,
      categories: ["DEVICE"],
    });
    for (const [kind, id] of [
      ["products", product.id],
      ["parties", party.id],
    ])
      await send(quality, `/api/v1/catalog/${kind}/${id}/review`, {
        expected_version: "1",
        decision: "APPROVE",
        evidence_ref: "DEMO",
        reason: "独立核对软件验证资料",
      });
    const account = await send(owner, "/api/v1/settlements/accounts", {
      entity_id: entity,
      code: "DAILY-CASH",
      name: "营业现金账户",
      method: "CASH",
    });
    const destination = await send(owner, "/api/v1/warehouses", {
      client_operation_id: randomUUID(),
      entity_id: entity,
      code: "STORE",
      name: "营业门店仓",
      location_code: "B1",
    });
    await send(
      owner,
      `/api/v1/customer-credit/${party.id}?entity_id=${entity}`,
      {
        expected_version: "0",
        credit_limit: "1000",
        valid_until: `${year + 1}-12-31`,
        reason: "批准营业试用额度",
      },
    );
    const availability = (warehouseId: string) =>
      get(
        owner,
        `/api/v1/inventory/availability?entity_id=${entity}&warehouse_id=${warehouseId}&sku_id=${product.sku_id}`,
      );
    const items = async (kind: string) =>
      (
        await get(
          finance,
          `/api/v1/settlements/items?entity_id=${entity}&kind=${kind}&party_id=${party.id}`,
        )
      ).items;
    let transit = "";
    async function check(
      label: string,
      quantities: string[],
      cost: string,
      ar: string,
      ap: string,
    ) {
      const warehouses = [
        opened.warehouse_id,
        destination.id,
        ...(transit ? [transit] : []),
      ];
      expect(quantities).toHaveLength(warehouses.length);
      for (const [index, id] of warehouses.entries()) {
        const balances = (await availability(id)).items;
        expect(sum(balances, "on_hand"), label + " physical stock").toBe(
          units(quantities[index]!),
        );
        expect(sum(balances, "reserved"), label + " reservations").toBe(0n);
        expect(sum(balances, "blocked"), label + " blocked").toBe(0n);
      }
      const ledger = await get(
        owner,
        `/api/v1/inventory/ledger?entity_id=${entity}`,
      );
      expect(ledger.has_more).toBe(false);
      expect(sum(ledger.items, "qty_delta")).toBe(
        quantities.reduce((n, q) => n + units(q), 0n),
      );
      expect(sum(ledger.items, "amount_delta")).toBe(units(cost));
      expect(sum(await items("AR"), "outstanding_amount")).toBe(units(ar));
      expect(sum(await items("AP"), "outstanding_amount")).toBe(units(ap));
      checkpoints.push({ label, quantities, cost, ar, ap });
    }
    async function loseResponse(
      a: Actor,
      path: string,
      action: () => Promise<void>,
      committed: () => Promise<void>,
    ) {
      const attempts: { key: string | undefined; body: string | null }[] = [];
      let first: any;
      await a.page.route("**" + path, async (route) => {
        if (route.request().method() !== "POST") return route.continue();
        attempts.push({
          key: route.request().headers()["idempotency-key"],
          body: route.request().postData(),
        });
        const response = await route.fetch({ maxRetries: 0 });
        expect(response.status()).toBe(200);
        const data = (await response.json()).data;
        if (attempts.length === 1) {
          first = data;
          return route.abort("failed"); // Server has committed; only the response is lost.
        }
        expect(data).toEqual(first);
        await route.fulfill({ response });
      });
      await action();
      const pending = a.page.getByRole("region", {
        name: "待确认提交",
        exact: true,
      });
      await expect(
        pending.getByRole("button", { name: "继续原提交" }),
      ).toBeEnabled();
      await committed(); // Observe business effect before attempting any replay.
      await a.page.reload();
      await expect(pending).toBeVisible();
      expect(attempts).toHaveLength(1); // Reload itself must never submit again.
      await pending.getByRole("button", { name: "继续原提交" }).click();
      await expect(pending).toHaveCount(0);
      expect(attempts).toHaveLength(2);
      expect(attempts[0]!.key).toBeTruthy();
      expect(attempts[1]).toEqual(attempts[0]);
      await committed();
      recoveries.push({
        path,
        attempts: attempts.length,
        identical_request: true,
        original_result: first,
      });
      await a.page.unroute("**" + path);
      return first;
    }

    await test.step("采购员制单、仓储收货、质管入库响应丢失后恢复原提交", async () => {
      const po = await send(purchase, "/api/v1/purchase-orders", {
        entity_id: entity,
        supplier_id: party.id,
        warehouse_id: opened.warehouse_id,
        lines: [
          {
            sku_id: product.sku_id,
            qty: "10",
            unit_price: "13",
            tax_rate: "0.13",
          },
        ],
      });
      documents.purchase = po;
      await send(quality, `/api/v1/purchase-orders/${po.id}/approve`, {
        expected_version: "1",
        reason: "独立审核采购",
      });
      let detail = await get(purchase, `/api/v1/purchase-orders/${po.id}`);
      const receipt = await send(
        warehouse,
        `/api/v1/purchase-orders/${po.id}/receipts`,
        {
          expected_version: detail.order.version,
          shipment_ref: "DAILY-DELIVERY",
          transport_note: "核对实物及随货单",
          lines: [
            {
              order_line_id: detail.lines[0].id,
              location_id: opened.location_id,
              qty: "10",
              lot_no: lot,
              mfg_date: `${year}-01-01`,
              expiry_date: `${year + 1}-12-31`,
            },
          ],
        },
      );
      detail = await get(purchase, `/api/v1/purchase-orders/${po.id}`);
      const inspection = await send(
        quality,
        `/api/v1/receipts/${receipt.id}/inspections`,
        {
          expected_version: "1",
          note: "独立验收10件合格",
          lines: [
            {
              receipt_line_id: detail.receipts[0].lines[0].id,
              qualified_qty: "10",
              rejected_qty: "0",
            },
          ],
        },
      );
      await original(quality, po.number, "purchase_post");
      await loseResponse(
        quality,
        `/api/v1/receipt-postings/${inspection.receipt_posting_id}/post`,
        async () => {
          await quality.page
            .getByRole("button", { name: "合格入库", exact: true })
            .click();
          await quality.page
            .getByRole("dialog")
            .getByRole("button", { name: "确认入库", exact: true })
            .click();
        },
        async () => {
          await check(
            "采购已入账且恢复不重复",
            ["10", "0"],
            "115.04",
            "0",
            "130",
          );
          expect(await items("AP")).toHaveLength(1);
        },
      );
      detail = await get(purchase, `/api/v1/purchase-orders/${po.id}`);
      expect(detail.order.created_by).toBe(purchase.session.principal_id);
      expect(detail.receipts[0].created_by).toBe(
        warehouse.session.principal_id,
      );
    });

    await test.step("销售、仓储与质管接着办理批发4件", async () => {
      const so = await send(sales, "/api/v1/sales-orders", {
        entity_id: entity,
        customer_id: party.id,
        warehouse_id: opened.warehouse_id,
        delivery_address: "虚拟营业交付点",
        lines: [
          {
            sku_id: product.sku_id,
            qty: "4",
            unit_price: "20",
            tax_rate: "0.13",
          },
        ],
      });
      documents.wholesale = so;
      await send(quality, `/api/v1/sales-orders/${so.id}/approve`, {
        expected_version: "1",
        reason: "核对授信及交易",
      });
      let detail = await get(sales, `/api/v1/sales-orders/${so.id}`);
      const shipment = await send(
        sales,
        `/api/v1/sales-orders/${so.id}/shipments`,
        {
          expected_version: detail.order.version,
          client_operation_id: randomUUID(),
          lines: [{ order_line_id: detail.lines[0].id, qty: "4" }],
        },
      );
      documents.shipment = shipment;
      await original(warehouse, shipment.number, "sales_pick");
      await warehouse.page
        .getByRole("button", { name: "登记拣货", exact: true })
        .click();
      const dialog = warehouse.page.getByRole("dialog");
      await dialog.getByLabel("实物批号").fill(lot);
      await dialog.getByLabel("实物数量").fill("4");
      await dialog.getByLabel("核查说明").fill("仓储按原单拣货");
      await dialog
        .getByRole("button", { name: "保存拣货", exact: true })
        .click();
      await expect(dialog).not.toBeVisible();
      detail = await get(sales, `/api/v1/sales-orders/${so.id}`);
      expect(detail.order.created_by).toBe(sales.session.principal_id);
      await send(quality, `/api/v1/shipments/${shipment.id}/verify`, {
        expected_version: "2",
        note: "独立核对实物",
        shipping_ref: "DAILY-SHIP",
        lines: detail.shipments[0].lines.map((r: any) => ({
          shipment_line_id: r.id,
          qty: r.qty,
          lot_no: r.lot_no,
        })),
      });
      await send(quality, `/api/v1/shipments/${shipment.id}/post`, {
        expected_version: "3",
      });
      await check("批发4件出库", ["6", "0"], "69.024", "80", "130");
    });

    async function retail(warehouseId: string, qty: string, recover = false) {
      const sale = await send(cashier, "/api/v1/retail/sales", {
        client_operation_id: randomUUID(),
        entity_id: entity,
        warehouse_id: warehouseId,
        lines: [
          { sku_id: product.sku_id, qty, unit_price: "20", tax_rate: "0.13" },
        ],
      });
      if (recover) {
        await original(cashier, sale.number, "retail_pay");
        await loseResponse(
          cashier,
          `/api/v1/retail/sales/${sale.id}/cash`,
          async () => {
            await cashier.page
              .getByRole("button", { name: "登记现金收款", exact: true })
              .click();
            const dialog = cashier.page.getByRole("dialog");
            await dialog
              .getByLabel("现金账户", { exact: true })
              .selectOption(account.id);
            await dialog
              .getByLabel("顾客交付现金（元）", { exact: true })
              .fill("50");
            await dialog.getByLabel("我已核对现金实收与找零").check();
            await dialog
              .getByLabel("操作说明")
              .fill("实收50元找零10元，验证丢失响应恢复");
            await dialog
              .getByRole("button", { name: "确认保存", exact: true })
              .click();
          },
          async () => {
            const saved = await get(cashier, `/api/v1/retail/sales/${sale.id}`);
            expect(saved.payments).toHaveLength(1);
            expect(sum(saved.payments, "amount")).toBe(units("40"));
            expect(
              sum((await availability(warehouseId)).items, "on_hand"),
            ).toBe(units("6"));
          },
        );
        await original(cashier, sale.number, "retail_complete");
      } else {
        await send(cashier, `/api/v1/retail/sales/${sale.id}/cash`, {
          client_operation_id: randomUUID(),
          expected_version: "1",
          account_id: account.id,
          tendered: "20",
          confirmed: true,
          reason: "盘点后恢复营业实收20元",
        });
      }
      const detail = await get(cashier, `/api/v1/retail/sales/${sale.id}`);
      const posted = await send(
        cashier,
        `/api/v1/retail/sales/${sale.id}/complete`,
        {
          expected_version: "2",
          reason: "核对实物并完成原单交付",
          lines: detail.allocations.map((a: any) => ({
            allocation_id: a.id,
            qty: a.allocated_qty,
            lot_no: a.lot_no,
          })),
        },
      );
      return { ...sale, posting_id: posted.posting_id };
    }
    await test.step("收银40元响应丢失，恢复后从原待办完成交付", async () => {
      documents.retail = await retail(opened.warehouse_id, "2", true);
      await check("零售2件出库且只收款一次", ["4", "0"], "46.016", "80", "130");
    });

    async function returnOne(
      key: string,
      a: Actor,
      posting: string,
      kind: string,
    ) {
      const source = await get(a, `/api/v1/returns/sources/${posting}`);
      const doc = await send(a, "/api/v1/returns", {
        client_operation_id: randomUUID(),
        source_posting_id: posting,
        reason: "连续营业按原单退1件",
        lines: [
          {
            source_posting_line_id: source.lines.find(
              (r: any) => r.lot_no === lot,
            ).id,
            qty: "1",
          },
        ],
      });
      documents[key] = doc;
      if (kind !== "PURCHASE") {
        await original(warehouse, doc.number, "return_receive");
        await warehouse.page
          .getByRole("button", { name: "登记隔离收货", exact: true })
          .click();
        const dialog = warehouse.page.getByRole("dialog");
        await dialog.getByLabel("收货数量 1", { exact: true }).fill("1");
        await dialog.getByLabel("实物批号 1", { exact: true }).fill(lot);
        await dialog.getByLabel("隔离保管位置").fill("营业退货隔离柜");
        await dialog.getByLabel("我已核对本次全部实物交接完成").check();
        await confirm(warehouse, "实物已交接，隔离待验");
      }
      await original(
        quality,
        doc.number,
        kind === "PURCHASE" ? "purchase_return_review" : "return_review",
      );
      await quality.page
        .getByRole("button", { name: "独立复核退货", exact: true })
        .click();
      const dialog = quality.page.getByRole("dialog");
      await dialog.getByLabel("实物核对数量 1", { exact: true }).fill("1");
      await dialog.getByLabel("实物批号 1", { exact: true }).fill(lot);
      await confirm(quality, "独立验收质量合格，核对数量批号");
      await quality.page
        .getByRole("button", {
          name: kind === "PURCHASE" ? "确认退货出库" : "确认退货入库",
          exact: true,
        })
        .click();
      await confirm(quality, "确认退货实物及原单过账");
      const saved = await get(owner, `/api/v1/returns/${doc.id}`);
      expect(saved.document.state).toBe("POSTED");
      expect(saved.document.created_by).toBe(a.session.principal_id);
      expect(saved.document.reviewed_by).toBe(quality.session.principal_id);
      if (kind !== "PURCHASE")
        expect(saved.custody[0].created_by).toBe(
          warehouse.session.principal_id,
        );
      if (kind === "RETAIL") {
        await original(finance, doc.number, "retail_return_refund");
        await finance.page
          .getByRole("button", { name: "登记本次零售退款", exact: true })
          .click();
        const refund = finance.page.getByRole("dialog");
        await refund.getByLabel("退款核对说明").fill("已实际退还测试现金20元");
        await refund.getByLabel("我已按本次退货金额实际退还现金").check();
        await refund
          .getByRole("button", { name: "确认登记零售退款", exact: true })
          .click();
        await expect(refund).not.toBeVisible();
      }
    }
    await test.step("销售员、收银员、采购员各退1件，仓储质管财务接续原单", async () => {
      await returnOne(
        "wholesale_return",
        sales,
        (await items("AR"))[0].posting_id,
        "SALES",
      );
      await check("批发退货1件", ["5", "0"], "57.52", "60", "130");
      await returnOne(
        "retail_return",
        cashier,
        documents.retail.posting_id,
        "RETAIL",
      );
      await check("零售退货1件并退款", ["6", "0"], "69.024", "60", "130");
      await returnOne(
        "purchase_return",
        purchase,
        (await items("AP"))[0].posting_id,
        "PURCHASE",
      );
      await check("采购退货1件", ["5", "0"], "57.52", "60", "117");
    });

    await test.step("仓储调拨3件分次收货，退出后从原待办继续", async () => {
      const candidates = await get(
        warehouse,
        `/api/v1/transfers/candidates?entity_id=${entity}&warehouse_id=${opened.warehouse_id}&search=${lot}`,
      );
      const doc = await send(warehouse, "/api/v1/transfers", {
        client_operation_id: randomUUID(),
        entity_id: entity,
        source_warehouse_id: opened.warehouse_id,
        destination_warehouse_id: destination.id,
        destination_location_id: destination.location_id,
        reason: "调拨门店继续营业",
        lines: [{ balance_id: candidates.items[0].id, qty: "3" }],
      });
      documents.transfer = doc;
      await send(quality, `/api/v1/transfers/${doc.id}/review`, {
        expected_version: "1",
        decision: "APPROVE",
        reason: "独立审核调拨",
      });
      await send(warehouse, `/api/v1/transfers/${doc.id}/dispatch`, {
        expected_version: "2",
        reason: "实物发运3件",
      });
      transit = (await get(warehouse, `/api/v1/transfers/${doc.id}`)).document
        .transit_warehouse_id;
      await check("调拨3件在途", ["2", "0", "3"], "57.52", "60", "117");
      for (const qty of ["2", "1"]) {
        await original(warehouse, doc.number, "transfer_receive");
        await warehouse.page
          .getByRole("button", { name: "登记本次收货", exact: true })
          .click();
        const dialog = warehouse.page.getByRole("dialog");
        await dialog.getByLabel("本次收货数量 1", { exact: true }).fill(qty);
        await dialog.getByLabel("实物批号 1", { exact: true }).fill(lot);
        await dialog
          .getByLabel("收货质量 1", { exact: true })
          .selectOption("QUALIFIED");
        await confirm(warehouse, "核对本次实收数量", "保存");
        await check(
          "调拨分次实收" + qty,
          ["2", qty === "2" ? "2" : "3", qty === "2" ? "1" : "0"],
          "57.52",
          "60",
          "117",
        );
      }
    });

    await test.step("门店盘亏1件冻结跨仓销售，质管结单后继续零售1件", async () => {
      const candidates = await get(
        warehouse,
        `/api/v1/stock-counts/candidates?entity_id=${entity}&warehouse_id=${destination.id}&search=${lot}`,
      );
      const count = await send(warehouse, "/api/v1/stock-counts", {
        client_operation_id: randomUUID(),
        entity_id: entity,
        warehouse_id: destination.id,
        balance_ids: [candidates.items[0].id],
        reason: "营业门店实盘",
      });
      documents.count = count;
      for (const id of [opened.warehouse_id, destination.id]) {
        expect((await availability(id)).items[0].ineligibility_codes).toContain(
          "COUNT_FROZEN",
        );
        const blocked = await cashier.context.request.post(
          origin + "/api/v1/retail/sales",
          {
            headers: {
              ...headers(cashier.session),
              "Idempotency-Key": randomUUID(),
            },
            data: {
              client_operation_id: randomUUID(),
              entity_id: entity,
              warehouse_id: id,
              lines: [
                {
                  sku_id: product.sku_id,
                  qty: "1",
                  unit_price: "20",
                  tax_rate: "0.13",
                },
              ],
            },
          },
        );
        expect(blocked.status()).toBe(409);
        expect((await blocked.json()).error.code).toBe("COUNT_FROZEN");
      }
      await original(warehouse, count.number, "count_enter");
      await warehouse.page
        .getByRole("button", { name: "录入实盘并提交", exact: true })
        .click();
      const dialog = warehouse.page.getByRole("dialog");
      await dialog.getByLabel("实盘数量 1", { exact: true }).fill("2");
      await dialog.getByLabel("实物批号 1", { exact: true }).fill(lot);
      await dialog
        .getByLabel("实盘依据 1", { exact: true })
        .fill("复盘核实实存2件");
      await confirm(warehouse, "实盘录入完整", "保存");
      await original(quality, count.number, "count_review");
      await quality.page
        .getByRole("button", { name: "独立审核盘点", exact: true })
        .click();
      await confirm(quality, "独立核查盘点差异", "保存");
      await original(quality, count.number, "count_post");
      await quality.page
        .getByRole("button", { name: "确认盘点过账", exact: true })
        .click();
      await confirm(quality, "确认差异并恢复营业", "保存");
      await check("门店盘亏1件", ["2", "2", "0"], "46.016", "60", "117");
      documents.retail_after_count = await retail(destination.id, "1");
      await check("盘点后继续销售1件", ["2", "1", "0"], "34.512", "60", "117");
    });

    await test.step("财务结清往来，核对库存资金与全企业待办", async () => {
      for (const [kind, amount, direction] of [
        ["AR", "60", "RECEIPT"],
        ["AP", "117", "PAYMENT"],
      ]) {
        const item = (await items(kind!))[0];
        const cash = await send(finance, "/api/v1/settlements/cash-documents", {
          client_operation_id: randomUUID(),
          entity_id: entity,
          party_id: party.id,
          account_id: account.id,
          direction,
          amount,
          reference: "DAILY-" + kind + suffix,
          occurred_on: new Intl.DateTimeFormat("en-CA", {
            timeZone: "Asia/Shanghai",
            year: "numeric",
            month: "2-digit",
            day: "2-digit",
          }).format(new Date()),
          note: "财务已核对虚拟收付凭据",
          confirmed: true,
        });
        documents[kind === "AR" ? "collection" : "payment"] = cash;
        await send(finance, "/api/v1/settlements/allocations", {
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
      }
      await check("营业全部结清", ["2", "1", "0"], "34.512", "0", "0");
      const queue = await get(owner, `/api/v1/work-queue?entity_id=${entity}`);
      expect(queue.has_more).toBe(false);
      expect(queue.items).toEqual([]);
      const detail = await get(
        cashier,
        `/api/v1/retail/sales/${documents.retail.id}`,
      );
      expect(detail.payments).toHaveLength(1);
      expect(detail.returns_summary).toEqual({
        returned_amount: "20.00",
        refunded_amount: "20.00",
        pending_refund_amount: "0.00",
      });
      await owner.page.goto("/");
      await nav(owner.page, "库存台账");
      await expect(
        owner.page.getByRole("row").filter({ hasText: lot }),
      ).toHaveCount(14);
      await owner.page.screenshot({
        path: "../../artifacts/continuous-operations-ledger.png",
        fullPage: true,
        animations: "disabled",
      });
      expect(errors).toEqual([]);
      const proof = resolve(
        "../../artifacts/continuous-operations-reconciliation.json",
      );
      await writeFile(
        proof,
        JSON.stringify(
          {
            recorded_at: new Date().toISOString(),
            run,
            tenant_name: manifest.tenant_name,
            tenant_id: opened.tenant_id,
            entity_id: entity,
            sku_id: product.sku_id,
            party_id: party.id,
            account_id: account.id,
            product: name,
            lot,
            actors: Object.fromEntries(
              Object.entries(team).map(([key, a]) => [
                key,
                { principal_id: a.session.principal_id, role: roles[key] },
              ]),
            ),
            warehouses: {
              source: { id: opened.warehouse_id },
              destination,
              transit_id: transit,
            },
            documents,
            checkpoints,
            recoveries,
            final_stock_qty: "3",
            final_cost_balance: "34.512",
            ar_outstanding: "0",
            ap_outstanding: "0",
            cash_record_net_change: "-17",
            coverage:
              "Seven real role-scoped HTTP sessions, browser work handoffs and response-loss recovery; not every form is entered through UI.",
          },
          null,
          2,
        ) + "\n",
      );
      // Independent persisted-data check: normal runtime role, tenant RLS, read-only repeatable-read transaction.
      await execute([
        "scripts/verify_mainline_trial.py",
        "--input",
        proof,
        "--output",
        resolve("../../artifacts/continuous-operations-db.json"),
      ]);
    });
  } finally {
    // Do not leave a failed acceptance run holding a stock-count freeze.
    if (documents.count && team.warehouse?.session) {
      const a = team.warehouse;
      try {
        const detail = await read(
          a.context,
          `/api/v1/stock-counts/${documents.count.id}`,
        );
        if (
          ["COUNTING", "SUBMITTED", "APPROVED"].includes(detail.document.state)
        )
          await command(
            a.context,
            a.session,
            `/api/v1/stock-counts/${documents.count.id}/cancel`,
            {
              expected_version: detail.document.version,
              reason: "结束未完成的连续营业验收盘点",
            },
          );
      } finally {
        for (const actor of Object.values(team)) await actor.context.close();
      }
    } else {
      for (const actor of Object.values(team)) await actor.context.close();
    }
  }
});
