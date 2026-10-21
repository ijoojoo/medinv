import { chooseWarehouse } from "./business-helpers";
import { chooseParty } from "./business-helpers";
import { chooseEntrySku } from "./business-helpers";
import { test, expect, type BrowserContext, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { resolve } from "node:path";
import { writeFile } from "node:fs/promises";
import { login, headers } from "./helpers";
import { command, read, nav, origin } from "./business-helpers";

type Actor = { context: BrowserContext; page: Page; session: any };

test("six roles partially receive, inspect and ship two products, replenish rejected goods and settle original documents", async ({
  browser,
}) => {
  test.setTimeout(240000);
  const run = randomUUID(),
    suffix = run.slice(0, 8),
    year = new Date().getFullYear();
  const roles: Record<string, string> = {
    owner: "OWNER",
    purchase: "PURCHASER",
    warehouse: "WAREHOUSE",
    quality: "QUALITY",
    sales: "SALES",
    finance: "FINANCE",
  };
  const team: Record<string, Actor> = {},
    errors: string[] = [],
    stages: any[] = [];
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
    tenant_name: "分批履约企业-" + suffix,
    entity_name: "分批履约经营主体",
    credit_code: "DEMO-" + suffix,
    warehouse_code: "MAIN",
    warehouse_name: "药械营业仓",
    location_code: "A1",
    reason: "虚拟多商品分批履约与缺货补货验收",
    members: Object.entries(roles).map(([name, role]) => ({
      principal_id: ids["demo." + name],
      role_codes: [role],
    })),
  };
  const manifestPath = resolve(
    "../../artifacts/partial-fulfillment-manifest.json",
  );
  await writeFile(manifestPath, JSON.stringify(manifest, null, 2) + "\n");
  const opened = JSON.parse(
    (await execute(["scripts/provision_enterprise.py", manifestPath])).stdout,
  );
  const entity = opened.entity_id;
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
      buyer = team.purchase!,
      keeper = team.warehouse!,
      quality = team.quality!,
      seller = team.sales!,
      finance = team.finance!;
    const send = (actor: Actor, path: string, body: unknown) =>
      command(actor.context, actor.session, path, body);
    const get = (path: string) => read(owner.context, path);
    const goods: any[] = [];
    for (const [code, name, category, base] of [
      ["OTC", "分批验收OTC药品", "DRUG", "盒"],
      ["DEVICE", "分批验收普通器械", "DEVICE", "支"],
    ]) {
      const product = await send(buyer, "/api/v1/catalog/products", {
        code,
        name: name + "-" + suffix,
        category,
        drug_sale_class: category === "DRUG" ? "OTC" : "UNKNOWN",
        manufacturer_name: "软件测试厂家",
        specification: "虚拟测试规格",
        approval_number: "DEMO-ONLY",
        valid_from: `${year}-01-01`,
        valid_until: `${year + 2}-12-31`,
        sku_code: code + "-SKU",
        base_unit: base,
        qty_scale: 0,
      });
      await send(quality, `/api/v1/catalog/products/${product.id}/review`, {
        expected_version: "1",
        decision: "APPROVE",
        evidence_ref: "MIXED-TEST",
        reason: "独立核对虚拟资料",
      });
      goods.push({
        ...product,
        code,
        name: name + "-" + suffix,
        base,
        lot: code + "-" + suffix,
      });
    }
    const drug = goods[0],
      device = goods[1];
    const party = await send(buyer, "/api/v1/catalog/parties", {
      entity_id: entity,
      code: "MIXED-PARTY",
      name: "虚拟药械往来单位",
      credit_code: "DEMO-ONLY",
      kind: "BOTH",
      license_no: "DEMO-ONLY",
      valid_from: `${year}-01-01`,
      valid_until: `${year + 2}-12-31`,
      categories: ["DRUG", "DEVICE"],
    });
    await send(quality, `/api/v1/catalog/parties/${party.id}/review`, {
      expected_version: "1",
      decision: "APPROVE",
      evidence_ref: "MIXED-TEST",
      reason: "独立核对药械经营范围",
    });
    const account = await send(owner, "/api/v1/settlements/accounts", {
      entity_id: entity,
      code: "CASH",
      name: "虚拟营业现金",
      method: "CASH",
    });
    await send(
      owner,
      `/api/v1/customer-credit/${party.id}?entity_id=${entity}`,
      {
        expected_version: "0",
        credit_limit: "10000",
        valid_until: `${year + 1}-12-31`,
        reason: "批准虚拟营业额度",
      },
    );
    await seller.page.goto("/");
    await nav(seller.page, "批发销售");
    await seller.page
      .getByRole("button", { name: "新建销售单", exact: true })
      .click();
    const saleDialog = seller.page.getByRole("dialog");
    await chooseParty(
      saleDialog.getByRole("combobox", { name: /^客户/ }),
      party.id,
    );
    await chooseWarehouse(
      saleDialog.getByRole("combobox", { name: /^发货仓库/ }),
      opened.warehouse_id,
    );
    await saleDialog.getByLabel("收货地址").fill("虚拟分批交付点");
    for (const [i, g] of goods.entries()) {
      if (i)
        await saleDialog
          .getByRole("button", { name: "添加商品行", exact: true })
          .click();
      const row = saleDialog.locator(".line-editor").nth(i);
      await chooseEntrySku(row, g.sku_id);
      await row.getByLabel("销售数量", { exact: true }).fill(i ? "8" : "10");
      await row
        .getByLabel("含税单价（元）", { exact: true })
        .fill(i ? "33.90" : "16.95");
    }
    const saleResponse = seller.page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/v1/sales-orders") &&
        r.request().method() === "POST",
    );
    await saleDialog
      .getByRole("button", { name: "保存销售单", exact: true })
      .click();
    const savedSale = await saleResponse;
    expect(savedSale.status(), await savedSale.text()).toBe(200);
    const so = (await savedSale.json()).data;
    await expect(saleDialog).not.toBeVisible();
    await send(quality, `/api/v1/sales-orders/${so.id}/approve`, {
      expected_version: "1",
      reason: "独立核对分批交付约定",
    });
    const replenishment = `/api/v1/sales-orders/${so.id}/replenishment`;
    const purchases: any[] = [],
      receipts: any[] = [],
      postings: any[] = [],
      shipments: any[] = [];
    async function original(actor: Actor) {
      await actor.page.goto("/");
      const queue = actor.page.getByRole("region", { name: "进销存业务待办" });
      await expect(
        queue.getByRole("button", { name: "刷新业务待办" }),
      ).toBeEnabled();
      await queue.getByLabel("查找待办").fill(so.number);
      await queue
        .getByRole("button", { name: "查询待办", exact: true })
        .click();
      await queue.getByRole("button", { name: "查看原单" }).click();
      await expect(actor.page.locator(".document-number").first()).toHaveText(
        so.number,
      );
    }
    async function panel(actor: Actor) {
      await actor.page
        .getByRole("button", { name: "缺货与补货", exact: true })
        .click();
      const area = actor.page.getByRole("region", { name: "销售缺货与补货" });
      await expect(
        area.getByRole("button", { name: "刷新补货建议" }),
      ).toBeEnabled();
      return area;
    }
    async function suggestion(
      label: string,
      remaining: number[],
      available: number[],
      pending: number[],
      suggested: number[],
    ) {
      const result = await get(replenishment);
      expect(result.items).toHaveLength(2);
      for (const [i, g] of goods.entries()) {
        const row = result.items.find((r: any) => r.sku_id === g.sku_id);
        for (const [field, values] of Object.entries({
          remaining_qty: remaining,
          available_qty: available,
          pending_purchase_qty: pending,
          suggested_qty: suggested,
        }))
          expect(Number(row[field]), label + " " + g.code + " " + field).toBe(
            values[i],
          );
      }
      stages.push({ label, remaining, available, pending, suggested });
    }
    async function stock(
      label: string,
      quantities: number[],
      cost: number,
      ar: number,
      ap: number,
    ) {
      for (const [i, g] of goods.entries()) {
        const items = (
          await get(
            `/api/v1/inventory/availability?entity_id=${entity}&warehouse_id=${opened.warehouse_id}&sku_id=${g.sku_id}`,
          )
        ).items;
        expect(
          items.reduce((n: number, r: any) => n + Number(r.on_hand), 0),
          label,
        ).toBe(quantities[i]);
        expect(
          items.reduce((n: number, r: any) => n + Number(r.reserved), 0),
          label,
        ).toBe(0);
      }
      const ledger = (await get(`/api/v1/inventory/ledger?entity_id=${entity}`))
        .items;
      expect(
        ledger.reduce(
          (n: number, r: any) =>
            n + Math.round(Number(r.amount_delta || 0) * 100),
          0,
        ),
      ).toBe(cost * 100);
      for (const [kind, value] of [
        ["AR", ar],
        ["AP", ap],
      ] as const) {
        const items = (
          await get(
            `/api/v1/settlements/items?entity_id=${entity}&kind=${kind}&party_id=${party.id}`,
          )
        ).items;
        expect(
          items.reduce(
            (n: number, r: any) =>
              n + Math.round(Number(r.outstanding_amount) * 100),
            0,
          ),
        ).toBe(Math.round(value * 100));
      }
      stages.push({ label, stock: quantities, cost, ar, ap });
    }
    async function createReplenishment(expected: number[]) {
      await original(buyer);
      const area = await panel(buyer);
      for (const [i, qty] of expected.entries()) {
        expect(
          Number(
            await area
              .getByLabel(`本次补货数量 ${i + 1}`, { exact: true })
              .inputValue(),
          ),
        ).toBe(qty);
        if (qty)
          await area
            .getByLabel(`基本单位含税进价 ${i + 1}`, { exact: true })
            .fill(i ? "22.60" : "11.30");
      }
      await chooseParty(area.getByLabel("补货供应商"), party.id);
      const response = buyer.page.waitForResponse(
        (r) =>
          r.url().endsWith(replenishment) && r.request().method() === "POST",
      );
      await area.getByRole("button", { name: "保存补货采购单" }).click();
      const saved = await response;
      expect(saved.status(), await saved.text()).toBe(200);
      const po = (await saved.json()).data;
      purchases.push(po);
      await expect(buyer.page.locator(".document-number").first()).toHaveText(
        po.number,
      );
      expect(
        saved
          .request()
          .postDataJSON()
          .lines.map((r: any) => Number(r.qty)),
      ).toEqual(expected.filter((q) => q > 0));
      await expect(
        buyer.page.getByRole("button", { name: "审核采购单", exact: true }),
      ).toHaveCount(0);
      await buyer.page.getByRole("button", { name: "返回原销售单" }).click();
      await expect(buyer.page.locator(".document-number").first()).toHaveText(
        so.number,
      );
      const next = await panel(buyer);
      await expect(
        next.getByRole("button", { name: "保存补货采购单" }),
      ).toHaveCount(0);
      await send(quality, `/api/v1/purchase-orders/${po.id}/approve`, {
        expected_version: "1",
        reason: "独立核对补货供应商与进价",
      });
      return po;
    }
    async function receive(po: any, quantities: number[]) {
      const doc = await get(`/api/v1/purchase-orders/${po.id}`);
      const created = await send(
        keeper,
        `/api/v1/purchase-orders/${po.id}/receipts`,
        {
          expected_version: doc.order.version,
          shipment_ref: `PART-${receipts.length + 1}`,
          transport_note: "按基本单位核对本次到货",
          lines: goods.flatMap((g, i) =>
            quantities[i]
              ? [
                  {
                    order_line_id: doc.lines.find(
                      (l: any) => l.sku_id === g.sku_id,
                    ).id,
                    location_id: opened.location_id,
                    qty: String(quantities[i]),
                    lot_no: g.lot,
                    mfg_date: `${year}-01-01`,
                    expiry_date: `${year + 1}-12-31`,
                  },
                ]
              : [],
          ),
        },
      );
      receipts.push({ ...created, purchase_id: po.id });
      return created;
    }
    async function inspect(
      po: any,
      rec: any,
      qualified: number[],
      rejected: number[],
    ) {
      const doc = await get(`/api/v1/purchase-orders/${po.id}`);
      const receipt = doc.receipts.find((r: any) => r.id === rec.id);
      return send(quality, `/api/v1/receipts/${rec.id}/inspections`, {
        expected_version: receipt.version,
        note: "分次核验，只处理本次完成部分",
        lines: goods.flatMap((g, i) =>
          qualified[i] || rejected[i]
            ? [
                {
                  receipt_line_id: receipt.lines.find(
                    (l: any) => l.lot_no === g.lot,
                  ).id,
                  qualified_qty: String(qualified[i]),
                  rejected_qty: String(rejected[i]),
                },
              ]
            : [],
        ),
      });
    }
    async function post(inspection: any) {
      const result = await send(
        quality,
        `/api/v1/receipt-postings/${inspection.receipt_posting_id}/post`,
        {
          expected_version: "1",
          inspection_id: inspection.inspection_id,
          posting_segment_id: inspection.receipt_posting_id,
        },
      );
      postings.push(result);
    }
    async function ship(quantities: number[], suggested: number[]) {
      await original(seller);
      await seller.page
        .getByRole("button", { name: "分配发货批次", exact: true })
        .click();
      const dialog = seller.page.getByRole("dialog");
      await dialog
        .getByRole("button", { name: "按可售量填入本次发货" })
        .click();
      for (const [i, qty] of quantities.entries()) {
        const field = dialog.getByLabel("本次发货数量", { exact: true }).nth(i);
        await expect
          .poll(async () => Number(await field.inputValue()))
          .toBe(suggested[i]);
        await field.fill(String(qty));
      }
      const response = seller.page.waitForResponse(
        (r) =>
          r.url().endsWith(`/sales-orders/${so.id}/shipments`) &&
          r.request().method() === "POST",
      );
      await dialog
        .getByRole("button", { name: "确认分配", exact: true })
        .click();
      const saved = await response;
      expect(saved.status(), await saved.text()).toBe(200);
      const shipment = (await saved.json()).data;
      await expect(dialog).not.toBeVisible();
      let doc = await get(`/api/v1/sales-orders/${so.id}`);
      const current = doc.shipments.find((s: any) => s.id === shipment.id);
      const lines = current.lines.map((l: any) => ({
        shipment_line_id: l.id,
        qty: l.qty,
        lot_no: l.lot_no,
      }));
      await send(keeper, `/api/v1/shipments/${shipment.id}/pick`, {
        expected_version: "1",
        lines,
        note: "分批拣货核对数量批号",
      });
      await send(quality, `/api/v1/shipments/${shipment.id}/verify`, {
        expected_version: "2",
        lines,
        note: "独立复核本次发货",
        shipping_ref: `PART-SHIP-${shipments.length + 1}`,
      });
      const result = await send(
        quality,
        `/api/v1/shipments/${shipment.id}/post`,
        { expected_version: "3" },
      );
      shipments.push({ ...shipment, ...result, quantities });
      doc = await get(`/api/v1/sales-orders/${so.id}`);
      expect(doc.shipments.every((s: any) => s.state === "POSTED")).toBe(true);
    }
    await suggestion("无库存销售需求", [10, 8], [0, 0], [0, 0], [10, 8]);
    await original(seller);
    const salesPanel = await panel(seller);
    await expect(
      salesPanel.getByRole("button", { name: "保存补货采购单" }),
    ).toHaveCount(0);
    const denied = await seller.context.request.post(origin + replenishment, {
      headers: { ...headers(seller.session), "Idempotency-Key": randomUUID() },
      data: {
        expected_version: "2",
        supplier_id: party.id,
        lines: [{ sku_id: drug.sku_id, qty: "10", unit_price: "11.30" }],
      },
    });
    expect(denied.status()).toBe(403);
    const first = await createReplenishment([10, 8]);
    await suggestion("关联采购避免重复补货", [10, 8], [0, 0], [10, 8], [0, 0]);
    const duplicate = await buyer.context.request.post(origin + replenishment, {
      headers: { ...headers(buyer.session), "Idempotency-Key": randomUUID() },
      data: {
        expected_version: "2",
        supplier_id: party.id,
        lines: [{ sku_id: drug.sku_id, qty: "1", unit_price: "11.30" }],
      },
    });
    expect(duplicate.status()).toBe(409);
    expect((await duplicate.json()).error.code).toBe("REPLENISHMENT_CHANGED");
    const arrival = await receive(first, [4, 3]);
    await suggestion("到货尚未验收不可售", [10, 8], [0, 0], [10, 8], [0, 0]);
    const initialInspection = await inspect(first, arrival, [3, 2], [0, 1]);
    await suggestion(
      "部分合格待入库与拒收缺口",
      [10, 8],
      [0, 0],
      [10, 7],
      [0, 1],
    );
    await stock("验收未过账不变动账本", [0, 0], 0, 0, 0);
    await post(initialInspection);
    await stock("第一批合格入库", [3, 2], 70, 0, 79.1);
    await suggestion("待验与未到货保留抵扣", [10, 8], [3, 2], [7, 5], [0, 1]);
    await ship([2, 2], [3, 2]);
    await stock("第一批发货", [1, 0], 10, 101.7, 79.1);
    await suggestion("部分发货后补拒收缺口", [8, 6], [1, 0], [7, 5], [0, 1]);
    const replacement = await createReplenishment([0, 1]);
    await suggestion("只补器械一支", [8, 6], [1, 0], [7, 6], [0, 0]);
    const pendingInspection = await inspect(first, arrival, [1, 0], [0, 0]);
    await post(pendingInspection);
    const rest = await receive(first, [6, 5]);
    await suggestion("首采购到齐尚未入库", [8, 6], [2, 0], [6, 6], [0, 0]);
    await post(await inspect(first, rest, [6, 5], [0, 0]));
    await stock("首采购可用部分全部入库", [8, 5], 180, 101.7, 271.2);
    await ship([5, 3], [8, 5]);
    await stock("第二批发货", [3, 2], 70, 288.15, 271.2);
    await suggestion("最后一支补货尚未到货", [3, 3], [3, 2], [0, 1], [0, 0]);
    const replacementReceipt = await receive(replacement, [0, 1]);
    await post(await inspect(replacement, replacementReceipt, [0, 1], [0, 0]));
    await stock("补拒收缺口入库", [3, 3], 90, 288.15, 293.8);
    await ship([3, 3], [3, 3]);
    expect((await get(`/api/v1/sales-orders/${so.id}`)).order.state).toBe(
      "FULFILLED",
    );
    await suggestion("原销售单发齐", [0, 0], [0, 0], [0, 0], [0, 0]);
    await stock("原销售单三批发齐", [0, 0], 0, 440.7, 293.8);
    const originalPurchase = await get(`/api/v1/purchase-orders/${first.id}`);
    const rejectedReceipt = originalPurchase.receipts.find(
      (r: any) => r.id === arrival.id,
    );
    const rejectedLine = rejectedReceipt.lines.find(
      (l: any) => l.lot_no === device.lot,
    );
    const handover = await send(
      keeper,
      `/api/v1/receipts/${arrival.id}/rejected-handovers`,
      {
        expected_version: rejectedReceipt.version,
        handover_ref: "PART-REJECTED",
        recipient: "虚拟供应商",
        reason: "核对拒收的一支器械已实际交还",
        lines: [{ receipt_line_id: rejectedLine.id, qty: "1" }],
      },
    );
    for (const [kind, amount, direction] of [
      ["AR", "440.70", "RECEIPT"],
      ["AP", "293.80", "PAYMENT"],
    ]) {
      const items = (
        await get(
          `/api/v1/settlements/items?entity_id=${entity}&kind=${kind}&party_id=${party.id}`,
        )
      ).items;
      expect(items).toHaveLength(kind === "AR" ? 3 : 4);
      const cash = await send(finance, "/api/v1/settlements/cash-documents", {
        client_operation_id: randomUUID(),
        entity_id: entity,
        party_id: party.id,
        account_id: account.id,
        direction,
        amount,
        reference: `PART-${kind}-${suffix}`,
        occurred_on: new Intl.DateTimeFormat("en-CA", {
          timeZone: "Asia/Shanghai",
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
        }).format(new Date()),
        note: "核对分批过账往来后合并登记并逐笔核销",
        confirmed: true,
      });
      await send(finance, "/api/v1/settlements/allocations", {
        client_operation_id: randomUUID(),
        cash_document_id: cash.id,
        expected_cash_version: "1",
        allocations: items.map((r: any) => ({
          settlement_item_id: r.id,
          expected_item_version: r.version,
          amount: r.outstanding_amount,
        })),
      });
    }
    await stock("分批往来全部核销", [0, 0], 0, 0, 0);
    expect((await get(`/api/v1/work-queue?entity_id=${entity}`)).items).toEqual(
      [],
    );
    await seller.page.getByRole("button", { name: "刷新销售详情" }).click();
    await expect(seller.page.locator(".el-tag").first()).toHaveText("已发齐");
    await seller.page.screenshot({
      path: "../../artifacts/partial-fulfillment-sales.png",
      fullPage: true,
      animations: "disabled",
    });
    await owner.page.goto("/");
    await nav(owner.page, "库存台账");
    await expect(
      owner.page.getByRole("row").filter({ hasText: suffix }),
    ).toHaveCount(12);
    await owner.page.screenshot({
      path: "../../artifacts/partial-fulfillment-ledger.png",
      fullPage: true,
      animations: "disabled",
    });
    expect(errors).toEqual([]);
    await writeFile(
      resolve("../../artifacts/partial-fulfillment-reconciliation.json"),
      JSON.stringify(
        {
          recorded_at: new Date().toISOString(),
          run,
          ...opened,
          tenant_name: manifest.tenant_name,
          goods,
          party_id: party.id,
          account_id: account.id,
          sales: so,
          purchases,
          receipts,
          postings,
          shipments,
          handover,
          stages,
          actors: Object.fromEntries(
            Object.entries(team).map(([name, a]) => [
              name,
              { principal_id: a.session.principal_id, role: roles[name] },
            ]),
          ),
        },
        null,
        2,
      ) + "\n",
    );
    await execute(["scripts/verify_partial_fulfillment.py"]);
  } finally {
    for (const actor of Object.values(team)) await actor.context.close();
  }
});
