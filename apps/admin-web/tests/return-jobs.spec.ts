import { test, expect, type BrowserContext, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { resolve } from "node:path";
import { writeFile } from "node:fs/promises";
import { login, headers } from "./helpers";
import {
  command,
  read,
  nav,
  origin,
  stockedCustomer,
} from "./business-helpers";

type Actor = { context: BrowserContext; page: Page; session: any };
async function original(actor: Actor, number: string, kind: string) {
  await actor.page.goto("/");
  const queue = actor.page.getByRole("region", { name: "进销存业务待办" });
  await queue.getByLabel("待办环节").selectOption(kind);
  await expect(
    queue.getByRole("button", { name: "查询待办", exact: true }),
  ).toBeEnabled();
  await queue.getByLabel("查找待办").fill(number);
  await queue.getByRole("button", { name: "查询待办", exact: true }).click();
  await queue
    .getByRole("row")
    .filter({ hasText: number })
    .getByRole("button", { name: "查看原单" })
    .click();
}
async function confirm(page: Page, reason: string) {
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("操作说明").fill(reason);
  await dialog.getByRole("button", { name: "确认保存", exact: true }).click();
  await expect(dialog).not.toBeVisible();
}
const scaled = (value: string) => {
  const negative = value.startsWith("-");
  const [whole, fraction = ""] = value.replace(/^-/, "").split(".");
  return (negative ? -1n : 1n) * BigInt(whole! + fraction.padEnd(8, "0"));
};

test("purchaser, sales, cashier, warehouse, quality and finance hand off three original returns and rejected goods", async ({
  browser,
}) => {
  test.setTimeout(240000);
  const team: Record<string, Actor> = {};
  const run = randomUUID(),
    suffix = run.slice(0, 8),
    year = new Date().getFullYear();
  const roles: Record<string, string> = {
    owner: "OWNER",
    quality: "QUALITY",
    purchase: "PURCHASER",
    sales: "SALES",
    warehouse: "WAREHOUSE",
    cashier: "CASHIER",
    finance: "FINANCE",
  };
  const errors: string[] = [];
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
    tenant_name: "退货协作企业-" + suffix,
    entity_name: "退货协作主体",
    credit_code: "DEMO-" + suffix,
    warehouse_code: "RT",
    warehouse_name: "退货协作仓",
    location_code: "A1",
    reason: "三类原单退货岗位交接验证",
    members: Object.entries(roles).map(([name, role]) => ({
      principal_id: ids["demo." + name],
      role_codes: [role],
    })),
  };
  const manifestPath = resolve("../../artifacts/return-jobs-manifest.json");
  await writeFile(
    manifestPath,
    JSON.stringify(manifest, null, 2) + "\n",
    "utf8",
  );
  const opened = JSON.parse(
    (await execute(["scripts/provision_enterprise.py", manifestPath])).stdout,
  );
  try {
    for (const name of Object.keys(roles)) {
      const context = await browser.newContext(),
        page = await context.newPage();
      page.on("pageerror", (e) => errors.push(e.message));
      team[name] = { context, page, session: null };
      const initial = await login(context, "demo." + name);
      const response = await context.request.post(origin + "/auth/tenant", {
        headers: headers(initial),
        data: { tenant_id: opened.tenant_id },
      });
      expect(response.status()).toBe(200);
      team[name]!.session = (await response.json()).data;
      expect(team[name]!.session.role_codes).toEqual([roles[name]]);
    }
    const owner = team.owner!,
      quality = team.quality!,
      purchase = team.purchase!,
      sales = team.sales!,
      warehouse = team.warehouse!,
      cashier = team.cashier!,
      finance = team.finance!;
    const send = (a: Actor, path: string, body: unknown) =>
      command(a.context, a.session, path, body);
    const get = (a: Actor, path: string) => read(a.context, path);
    const deny = async (a: Actor, path: string, body: unknown) => {
      const response = await a.context.request.post(origin + path, {
        headers: { ...headers(a.session), "Idempotency-Key": randomUUID() },
        data: body,
      });
      expect(response.status(), path + ": " + (await response.text())).toBe(
        403,
      );
    };
    const f = await stockedCustomer(
      owner.context,
      owner.session,
      quality.context,
      quality.session,
      { warehouse: opened.warehouse_id, location: opened.location_id },
    );
    const account = await send(owner, "/api/v1/settlements/accounts", {
      entity_id: f.entity,
      code: "RT-CASH",
      name: "退货验证现金",
      method: "CASH",
    });
    await send(
      owner,
      `/api/v1/customer-credit/${f.party.id}?entity_id=${f.entity}`,
      {
        expected_version: "0",
        credit_limit: "1000",
        valid_until: `${year + 1}-12-31`,
        reason: "软件验证授信",
      },
    );
    const so = await send(sales, "/api/v1/sales-orders", {
      entity_id: f.entity,
      customer_id: f.party.id,
      warehouse_id: f.warehouse,
      delivery_address: "软件测试交付地址",
      lines: [
        {
          sku_id: f.product.sku_id,
          qty: "4",
          unit_price: "20",
          tax_rate: "0.13",
        },
      ],
    });
    await send(quality, `/api/v1/sales-orders/${so.id}/approve`, {
      expected_version: "1",
      reason: "独立审核原销售",
    });
    let order = await get(sales, `/api/v1/sales-orders/${so.id}`);
    const shipment = await send(
      sales,
      `/api/v1/sales-orders/${so.id}/shipments`,
      {
        expected_version: order.order.version,
        client_operation_id: randomUUID(),
        lines: [{ order_line_id: order.lines[0].id, qty: "4" }],
      },
    );
    order = await get(sales, `/api/v1/sales-orders/${so.id}`);
    const lines = order.shipments[0].lines.map((r: any) => ({
      shipment_line_id: r.id,
      qty: r.qty,
      lot_no: r.lot_no,
    }));
    await send(warehouse, `/api/v1/shipments/${shipment.id}/pick`, {
      expected_version: "1",
      lines,
      note: "核对原单拣货",
    });
    await send(quality, `/api/v1/shipments/${shipment.id}/verify`, {
      expected_version: "2",
      lines,
      note: "独立复核",
      shipping_ref: "RETURN-JOBS",
    });
    await send(quality, `/api/v1/shipments/${shipment.id}/post`, {
      expected_version: "3",
    });
    const pos = await send(cashier, "/api/v1/retail/sales", {
      client_operation_id: randomUUID(),
      entity_id: f.entity,
      warehouse_id: f.warehouse,
      lines: [
        {
          sku_id: f.product.sku_id,
          qty: "2",
          unit_price: "20",
          tax_rate: "0.13",
        },
      ],
    });
    await send(cashier, `/api/v1/retail/sales/${pos.id}/cash`, {
      client_operation_id: randomUUID(),
      expected_version: "1",
      account_id: account.id,
      tendered: "40",
      confirmed: true,
      reason: "实际收款核对",
    });
    const retail = await get(cashier, `/api/v1/retail/sales/${pos.id}`);
    const retailPosted = await send(
      cashier,
      `/api/v1/retail/sales/${pos.id}/complete`,
      {
        expected_version: "2",
        reason: "核对原销售交付",
        lines: retail.allocations.map((a: any) => ({
          allocation_id: a.id,
          qty: a.allocated_qty,
          lot_no: a.lot_no,
        })),
      },
    );
    const items = async (kind: string) =>
      (
        await get(
          finance,
          `/api/v1/settlements/items?entity_id=${f.entity}&kind=${kind}&party_id=${f.party.id}`,
        )
      ).items;
    const cashIds: Record<string, string> = {},
      sources: Record<string, string> = { RETAIL: retailPosted.posting_id };
    const today = new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(new Date());
    async function allocate(kind: string, amount: string) {
      const item = (await items(kind === "SALES" ? "AR" : "AP"))[0];
      const cash = (
        await get(
          finance,
          `/api/v1/settlements/cash-documents/${cashIds[kind]}`,
        )
      ).cash;
      await send(finance, "/api/v1/settlements/allocations", {
        client_operation_id: randomUUID(),
        cash_document_id: cash.id,
        expected_cash_version: cash.version,
        allocations: [
          {
            settlement_item_id: item.id,
            expected_item_version: item.version,
            amount,
          },
        ],
      });
    }
    for (const kind of ["PURCHASE", "SALES"]) {
      const item = (await items(kind === "SALES" ? "AR" : "AP"))[0];
      sources[kind] = item.posting_id;
      const amount = kind === "SALES" ? "80" : "130";
      const cash = await send(finance, "/api/v1/settlements/cash-documents", {
        client_operation_id: randomUUID(),
        entity_id: f.entity,
        party_id: f.party.id,
        account_id: account.id,
        direction: kind === "SALES" ? "RECEIPT" : "PAYMENT",
        amount,
        reference: "RT-" + kind + suffix,
        occurred_on: today,
        note: "原单实际收付款软件验证",
        confirmed: true,
      });
      cashIds[kind] = cash.id;
      await allocate(kind, amount);
    }
    const checkpoints: any[] = [],
      documents: any[] = [];
    async function check(label: string, qty: string, cost: string) {
      const stock = await get(
        owner,
        `/api/v1/inventory/availability?entity_id=${f.entity}&warehouse_id=${f.warehouse}&sku_id=${f.product.sku_id}`,
      );
      expect(scaled(stock.items[0].on_hand)).toBe(scaled(qty));
      expect(scaled(stock.items[0].reserved)).toBe(0n);
      const ledger = await get(
        owner,
        `/api/v1/inventory/ledger?entity_id=${f.entity}`,
      );
      expect(ledger.has_more).toBe(false);
      expect(
        ledger.items.reduce(
          (sum: bigint, r: any) => sum + scaled(r.amount_delta || "0"),
          0n,
        ),
      ).toBe(scaled(cost));
      checkpoints.push({ label, qty, cost });
    }
    await check("原采购与两种销售已完成", "4", "46.016");
    async function apply(kind: string, a: Actor) {
      await a.page.goto("/");
      if (kind === "RETAIL") {
        await nav(a.page, "零售收银");
        await a.page
          .getByRole("row")
          .filter({ hasText: pos.number })
          .getByRole("button", { name: "零售详情" })
          .click();
        await a.page
          .getByRole("button", { name: "办理原单退货", exact: true })
          .click();
      } else {
        await nav(a.page, kind === "PURCHASE" ? "采购入库" : "批发销售");
        await a.page
          .getByRole("row")
          .filter({
            hasText: kind === "PURCHASE" ? f.purchase.number : so.number,
          })
          .getByRole("button", {
            name: kind === "PURCHASE" ? "查看采购单" : "查看销售单",
          })
          .click();
        await a.page
          .getByRole("button", {
            name:
              kind === "PURCHASE" ? "本次入库退货与进度" : "本次出库退货与进度",
            exact: true,
          })
          .click();
        await a.page
          .getByRole("button", { name: "申请本次退货", exact: true })
          .click();
      }
      const dialog = a.page.getByRole("dialog");
      await dialog.getByLabel("本次退货数量 1", { exact: true }).fill("1");
      await dialog.getByLabel("退货原因").fill("按实际岗位申请原单退一件");
      const response = a.page.waitForResponse(
        (r) =>
          r.request().method() === "POST" &&
          r.url().endsWith("/api/v1/returns") &&
          r.status() === 200,
      );
      await dialog
        .getByRole("button", { name: "创建退货申请", exact: true })
        .click();
      const result = (await (await response).json()).data;
      await expect(dialog).not.toBeVisible();
      await expect(
        a.page.getByRole("button", { name: "独立复核退货", exact: true }),
      ).toHaveCount(0);
      await expect(
        a.page.getByRole("button", { name: "登记隔离收货", exact: true }),
      ).toHaveCount(0);
      await expect(
        a.page.getByRole("button", { name: "撤销原核销", exact: true }),
      ).toHaveCount(0);
      documents.push({ kind, ...result });
      return result;
    }
    async function receive(doc: any) {
      await original(warehouse, doc.number, "return_receive");
      await expect(
        warehouse.page.getByRole("button", {
          name: "独立复核退货",
          exact: true,
        }),
      ).toHaveCount(0);
      await warehouse.page
        .getByRole("button", { name: "登记隔离收货", exact: true })
        .click();
      const dialog = warehouse.page.getByRole("dialog");
      await dialog.getByLabel("收货数量 1", { exact: true }).fill("1");
      await dialog.getByLabel("实物批号 1", { exact: true }).fill(f.lot);
      await dialog.getByLabel("隔离保管位置").fill("仓储实际退货隔离柜");
      await dialog.getByLabel("我已核对本次全部实物交接完成").check();
      await confirm(warehouse.page, "仓储已收取实物，隔离待验");
    }
    for (const [kind, a, qty, cost] of [
      ["PURCHASE", purchase, "3", "34.512"],
      ["SALES", sales, "4", "46.016"],
      ["RETAIL", cashier, "5", "57.52"],
    ] as const) {
      for (const wrong of [purchase, sales, cashier, warehouse]) {
        if (wrong === a) continue;
        await deny(wrong, "/api/v1/returns", {
          client_operation_id: randomUUID(),
          source_posting_id: sources[kind],
          reason: "跨岗申请拦截验证",
        });
      }
      const doc = await apply(kind, a);
      if (kind !== "PURCHASE") await receive(doc);
      await original(
        quality,
        doc.number,
        kind === "PURCHASE" ? "purchase_return_review" : "return_review",
      );
      await quality.page
        .getByRole("button", { name: "独立复核退货", exact: true })
        .click();
      let dialog = quality.page.getByRole("dialog");
      await dialog.getByLabel("实物核对数量 1", { exact: true }).fill("1");
      await dialog.getByLabel("实物批号 1", { exact: true }).fill(f.lot);
      await confirm(quality.page, "独立质管核对数量批号及质量合格");
      if (kind !== "RETAIL") {
        // Paid and fully allocated sources must wait for finance; no hidden automatic reversal.
        await quality.page
          .getByRole("button", {
            name: "确认退货" + (kind === "PURCHASE" ? "出库" : "入库"),
            exact: true,
          })
          .click();
        dialog = quality.page.getByRole("dialog");
        await dialog.getByLabel("操作说明").fill("检查原核销拦截");
        const blocked = quality.page.waitForResponse(
          (r) =>
            r.url().endsWith(`/returns/${doc.id}/post`) && r.status() === 409,
        );
        await dialog
          .getByRole("button", { name: "确认保存", exact: true })
          .click();
        expect((await (await blocked).json()).error.code).toBe(
          "RETURN_REVERSE_ALLOCATIONS",
        );
        await dialog.getByRole("button", { name: "Close this dialog" }).click();
        await original(
          finance,
          doc.number,
          kind === "PURCHASE" ? "purchase_return_post" : "return_post",
        );
        await finance.page
          .getByRole("button", { name: "撤销原核销", exact: true })
          .click();
        await confirm(finance.page, "财务核对后撤销原核销，供退货及退款办理");
      }
      await quality.page
        .getByRole("button", {
          name: "确认退货" + (kind === "PURCHASE" ? "出库" : "入库"),
          exact: true,
        })
        .click();
      await confirm(quality.page, "已核对退货实物交接，确认过账");
      await check(kind + "退货过账", qty, cost);
      await original(
        finance,
        doc.number,
        kind === "RETAIL" ? "retail_return_refund" : "return_refund_check",
      );
      if (kind === "RETAIL") {
        await finance.page
          .getByRole("button", { name: "登记本次零售退款", exact: true })
          .click();
        dialog = finance.page.getByRole("dialog");
        await dialog.getByLabel("退款核对说明").fill("已向顾客退还现金20元");
        await dialog.getByLabel("我已按本次退货金额实际退还现金").check();
        await dialog
          .getByRole("button", { name: "确认登记零售退款", exact: true })
          .click();
        await expect(dialog).not.toBeVisible();
        await expect(
          finance.page.getByRole("button", {
            name: "登记本次零售退款",
            exact: true,
          }),
        ).toBeDisabled();
      } else {
        await finance.page
          .getByRole("button", { name: "登记实际退款", exact: true })
          .click();
        dialog = finance.page.getByRole("dialog");
        await dialog
          .getByRole("combobox", { name: "原收付款单", exact: true })
          .selectOption(cashIds[kind]!);
        await dialog
          .getByLabel("实际退款金额（元）")
          .fill(kind === "PURCHASE" ? "13" : "20");
        await dialog.getByLabel("退款凭据编号").fill("RETURN-" + kind + suffix);
        await dialog
          .getByLabel("退款核对说明")
          .fill("实际收到供应商退款或退还客户现金，虚拟验证");
        await dialog.getByLabel("我已核对退款实际发生及退款凭据").check();
        await dialog
          .getByRole("button", { name: "确认保存退款记录", exact: true })
          .click();
        await expect(dialog).not.toBeVisible();
        await allocate(kind, kind === "PURCHASE" ? "117" : "60");
        expect(
          (await items(kind === "PURCHASE" ? "AP" : "AR"))[0]
            .outstanding_amount,
        ).toBe("0.00");
      }
      const saved = await get(owner, `/api/v1/returns/${doc.id}`);
      expect(saved.document.created_by).toBe(a.session.principal_id);
      expect(saved.document.reviewed_by).toBe(quality.session.principal_id);
      if (kind !== "PURCHASE")
        expect(saved.custody[0].created_by).toBe(
          warehouse.session.principal_id,
        );
      const queue = await get(
        a,
        `/api/v1/work-queue?entity_id=${f.entity}&search=${doc.number}`,
      );
      expect(queue.items).toEqual([]);
    }
    const rejected = await apply("RETAIL", cashier);
    await receive(rejected);
    await original(quality, rejected.number, "return_review");
    await quality.page
      .getByRole("button", { name: "独立复核退货", exact: true })
      .click();
    await quality.page
      .getByRole("dialog")
      .getByLabel("复核结论")
      .selectOption("REJECT");
    await confirm(quality.page, "包装异常，拒收并交还客户");
    await original(warehouse, rejected.number, "return_handback");
    await warehouse.page
      .getByRole("button", { name: "登记交还客户", exact: true })
      .click();
    await warehouse.page
      .getByRole("dialog")
      .getByLabel("我已核对本次全部实物交接完成")
      .check();
    await confirm(warehouse.page, "全部拒收实物已交还客户");
    await check("拒收交还不改变库存成本", "5", "57.52");
    const finalRetail = await get(cashier, `/api/v1/retail/sales/${pos.id}`);
    expect(finalRetail.returns_summary).toEqual({
      returned_amount: "20.00",
      refunded_amount: "20.00",
      pending_refund_amount: "0.00",
    });
    expect(
      (
        await get(
          owner,
          `/api/v1/work-queue?entity_id=${f.entity}&search=${rejected.number}`,
        )
      ).items,
    ).toEqual([]);
    for (const kind of ["PURCHASE", "SALES"]) {
      const cash = (
        await get(
          finance,
          `/api/v1/settlements/cash-documents/${cashIds[kind]}`,
        )
      ).cash;
      expect(cash.remaining_amount).toBe("0.00");
      expect(cash.refunded_amount).toBe(
        kind === "PURCHASE" ? "13.00" : "20.00",
      );
    }
    await owner.page.goto("/");
    await nav(owner.page, "库存台账");
    await expect(
      owner.page.getByRole("row").filter({ hasText: f.lot }),
    ).toHaveCount(6);
    await owner.page.screenshot({
      path: "../../artifacts/return-jobs-ledger.png",
      fullPage: true,
    });
    await expect(finance.page.locator(".el-message")).toHaveCount(0);
    await finance.page.screenshot({
      path: "../../artifacts/return-jobs-refunded.png",
      fullPage: true,
    });
    expect(errors).toEqual([]);
    await writeFile(
      "../../artifacts/return-jobs-reconciliation.json",
      JSON.stringify(
        {
          recorded_at: new Date().toISOString(),
          tenant_name: manifest.tenant_name,
          ...opened,
          actors: Object.fromEntries(
            Object.entries(team).map(([name, a]) => [
              name,
              { principal_id: a.session.principal_id, role: roles[name] },
            ]),
          ),
          documents,
          checkpoints,
          final_qty: "5",
          final_cost: "57.52",
          final_ar: "0",
          final_ap: "0",
          refunds: {
            supplier_received: "13",
            customer_paid: "20",
            retail_paid: "20",
          },
          checks: [
            "three job-specific original application UIs",
            "warehouse custody distinct from maker and reviewer",
            "quality posting waits for finance reversal",
            "refunds on original accounts",
            "rejected custody handback has no inventory effect",
            "all scenario return work items cleared",
            "cross-job HTTP 403 before creation",
          ],
        },
        null,
        2,
      ) + "\n",
      "utf8",
    );
  } finally {
    await Promise.all(Object.values(team).map((a) => a.context.close()));
  }
});
