import { test, expect, type BrowserContext, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { resolve } from "node:path";
import { writeFile } from "node:fs/promises";
import { wholesale, command, read, nav, origin } from "./business-helpers";
import { headers, login } from "./helpers";

async function switchTo(context: BrowserContext, current: any, tenant: string) {
  const response = await context.request.post(origin + "/auth/tenant", {
    headers: headers(current),
    data: { tenant_id: tenant },
  });
  expect(response.status()).toBe(200);
  return (await response.json()).data;
}

test("seven roles open an empty enterprise, hand off opening lots and settle first purchase, retail and wholesale", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(240000);
  let owner = await wholesale(context, "demo.owner");
  const original = owner,
    reviewer = await browser.newContext(),
    outsider = await browser.newContext();
  const errors: string[] = [];
  const team: Record<
    string,
    { context: BrowserContext; page: Page; session: any }
  > = {};
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    let checker = await wholesale(reviewer, "demo.quality");
    const viewer = await wholesale(outsider, "demo.viewer");
    const roles: Record<string, string> = {
      purchase: "PURCHASER",
      warehouse: "WAREHOUSE",
      sales: "SALES",
      cashier: "CASHIER",
      finance: "FINANCE",
    };
    for (const role of Object.keys(roles)) {
      const roleContext = await browser.newContext();
      const rolePage = await roleContext.newPage();
      rolePage.on("pageerror", (e) => errors.push(e.message));
      team[role] = { context: roleContext, page: rolePage, session: null };
      team[role]!.session = await login(roleContext, "demo." + role);
    }
    const run = randomUUID(),
      suffix = run.slice(0, 8),
      year = new Date().getFullYear();
    const manifest = {
      request_id: run,
      tenant_name: "试用开账企业-" + suffix,
      entity_name: "开账经营主体-" + suffix,
      credit_code: "DEMO-" + suffix,
      warehouse_code: "START",
      warehouse_name: "开账仓库-" + suffix,
      location_code: "A1",
      reason: "全新企业开账验收，仅虚拟测试资料",
      members: [
        { principal_id: owner.principal_id, role_codes: ["OWNER"] },
        { principal_id: checker.principal_id, role_codes: ["QUALITY"] },
        ...Object.entries(team).map(([role, a]) => ({
          principal_id: a.session.principal_id,
          role_codes: [roles[role]],
        })),
      ],
    };
    const path = resolve("../../artifacts/enterprise-opening-manifest.json");
    await writeFile(path, JSON.stringify(manifest, null, 2) + "\n", "utf8");
    const provision = async () => {
      const result = await promisify(execFile)(
        resolve(
          process.platform === "win32"
            ? "../../backend/.venv/Scripts/python.exe"
            : "../../backend/.venv/bin/python",
        ),
        [resolve("../../scripts/provision_enterprise.py"), path],
        { env: { ...process.env, PYTHONUTF8: "1" }, timeout: 30000 },
      );
      return JSON.parse(result.stdout);
    };
    const opened = await provision();
    expect(await provision()).toEqual(opened);
    owner = await switchTo(context, owner, opened.tenant_id);
    checker = await switchTo(reviewer, checker, opened.tenant_id);
    for (const [role, actor] of Object.entries(team)) {
      actor.session = await switchTo(
        actor.context,
        actor.session,
        opened.tenant_id,
      );
      expect(actor.session.role_codes).toEqual([roles[role]]);
    }
    const buyer = team.purchase!,
      keeper = team.warehouse!,
      seller = team.sales!,
      cashier = team.cashier!,
      finance = team.finance!;
    expect(owner.entities).toEqual([
      { id: opened.entity_id, name: manifest.entity_name },
    ]);
    const entity = opened.entity_id;
    const memberData = await read(context, "/control/members");
    expect(memberData.items).toHaveLength(7);
    expect(
      memberData.items.find((r: any) => r.principal_id === checker.principal_id)
        .role_codes,
    ).toEqual(["QUALITY"]);
    expect(checker.permissions).not.toContain("opening.write");
    expect(checker.permissions).toContain("opening.post");
    const initial = await read(
      context,
      `/api/v1/purchasing/options?entity_id=${entity}`,
    );
    expect(initial.skus).toHaveLength(0);
    expect(initial.warehouses).toHaveLength(1);
    expect(initial.locations).toHaveLength(1);
    expect(
      (await read(context, `/api/v1/inventory/ledger?entity_id=${entity}`))
        .items,
    ).toEqual([]);
    expect(
      (await read(context, `/api/v1/work-queue?entity_id=${entity}`)).items,
    ).toEqual([]);
    const forbidden = await outsider.request.get(
      origin + `/api/v1/inventory/ledger?entity_id=${entity}`,
      { headers: headers(viewer) },
    );
    expect(forbidden.status()).toBe(404);
    const stale = await context.request.get(
      origin + `/api/v1/inventory/ledger?entity_id=${original.entities[0].id}`,
      { headers: headers(original) },
    );
    expect(stale.status()).toBe(409);
    const priorTenant = await context.request.get(
      origin + `/api/v1/inventory/ledger?entity_id=${original.entities[0].id}`,
      { headers: headers(owner) },
    );
    expect(priorTenant.status()).toBe(404);

    const product = await command(
      buyer.context,
      buyer.session,
      "/api/v1/catalog/products",
      {
        code: "START-P",
        name: "开账验收器械-" + suffix,
        category: "DEVICE",
        manufacturer_name: "软件测试厂家",
        specification: "单件",
        approval_number: "DEMO-ONLY",
        valid_from: `${year}-01-01`,
        valid_until: `${year + 2}-12-31`,
        sku_code: "START-SKU",
        base_unit: "个",
        qty_scale: 0,
      },
    );
    const party = await command(
      buyer.context,
      buyer.session,
      "/api/v1/catalog/parties",
      {
        entity_id: entity,
        code: "START-PARTY",
        name: "开账往来单位-" + suffix,
        credit_code: "DEMO-ONLY",
        kind: "BOTH",
        license_no: "DEMO-ONLY",
        valid_from: `${year}-01-01`,
        valid_until: `${year + 2}-12-31`,
        categories: ["DEVICE"],
      },
    );
    for (const [kind, id] of [
      ["products", product.id],
      ["parties", party.id],
    ])
      await command(reviewer, checker, `/api/v1/catalog/${kind}/${id}/review`, {
        expected_version: "1",
        decision: "APPROVE",
        evidence_ref: "DEMO-EVIDENCE",
        reason: "核对虚拟资料并独立审核",
      });

    const openingPage = keeper.page;
    await openingPage.goto("/");
    await nav(openingPage, "期初库存");
    await openingPage
      .getByRole("button", { name: "新建期初库存", exact: true })
      .click();
    await openingPage
      .getByRole("combobox", { name: "启用仓库", exact: true })
      .selectOption(opened.warehouse_id);
    await openingPage
      .getByLabel("期初清单凭据", { exact: true })
      .fill("OPEN-" + suffix);
    const lot = "OPEN-" + suffix,
      lateLot = "LATE-" + suffix;
    await openingPage.getByLabel("期初CSV文件", { exact: true }).setInputFiles({
      name: "新企业期初.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(
        "SKU编码,库位编码,批号,生产日期,有效期至,期初数量,期初成本金额\r\n" +
          `START-SKU,A1,${lot},${year}-01-01,${year + 1}-06-30,4,40\r\n` +
          `START-SKU,A1,${lateLot},${year}-01-01,${year + 1}-09-30,6,90\r\n`,
        "utf8",
      ),
    });
    await openingPage
      .getByRole("button", {
        name: "核对后填入清单（替换现有行）",
        exact: true,
      })
      .click();
    await openingPage
      .getByLabel("开账说明")
      .fill("核对两批实物4件/6件、账面成本40元/90元");
    await openingPage
      .getByRole("button", { name: "保存期初清单", exact: true })
      .click();
    await expect(
      openingPage.getByRole("heading", { name: "期初库存详情", exact: true }),
    ).toBeVisible();
    const number = await openingPage.locator(".document-number").innerText();
    await expect(
      openingPage.getByRole("button", { name: "审核期初单", exact: true }),
    ).toHaveCount(0);
    const quality = await reviewer.newPage();
    quality.on("pageerror", (e) => errors.push(e.message));
    await quality.goto("/");
    const queue = quality.getByRole("region", { name: "进销存业务待办" });
    await queue.getByLabel("待办环节").selectOption("opening_review");
    await expect(
      queue.getByRole("button", { name: "查询待办", exact: true }),
    ).toBeEnabled();
    await queue.getByLabel("查找待办").fill("OPEN-" + suffix);
    await queue.getByRole("button", { name: "查询待办", exact: true }).click();
    await queue
      .getByRole("row")
      .filter({ hasText: number })
      .getByRole("button", { name: "查看原单" })
      .click();
    await expect(quality.locator(".document-number")).toHaveText(number);
    await quality
      .getByRole("button", { name: "审核期初单", exact: true })
      .click();
    let dialog = quality.getByRole("dialog");
    await dialog.getByLabel("核验凭据").fill("OPEN-CHECK-" + suffix);
    await dialog.getByLabel("操作说明").fill("独立核对实物及成本");
    await dialog.getByRole("button", { name: "提交审核", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await quality
      .getByRole("button", { name: "返回业务待办", exact: true })
      .click();
    await expect(queue.getByLabel("查找待办")).toHaveValue("OPEN-" + suffix);
    await expect(
      queue.getByRole("row").filter({ hasText: number }),
    ).toHaveCount(0);
    await queue.getByLabel("待办环节").selectOption("opening_post");
    await queue
      .getByRole("row")
      .filter({ hasText: number })
      .getByRole("button", { name: "查看原单" })
      .click();
    await quality
      .getByRole("button", { name: "启用期初库存", exact: true })
      .click();
    dialog = quality.getByRole("dialog");
    await dialog.getByLabel("操作说明").fill("确认启用库存，开始正常业务");
    await dialog.getByRole("button", { name: "确认启用", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await expect(
      quality.getByRole("alert").filter({ hasText: "期初库存已启用" }),
    ).toBeVisible();
    const opening = (
      await read(context, `/api/v1/inventory-openings?entity_id=${entity}`)
    ).items[0];
    const stages: any[] = [];
    async function check(
      label: string,
      quantities: number[],
      cost: number,
      ar: string,
      ap: string,
    ) {
      for (const [i, id] of [
        opened.warehouse_id,
        emptyWarehouse.id,
      ].entries()) {
        const stock = (
          await read(
            context,
            `/api/v1/inventory/availability?entity_id=${entity}&warehouse_id=${id}&sku_id=${product.sku_id}`,
          )
        ).items;
        expect(
          stock.reduce((n: number, r: any) => n + Number(r.on_hand), 0),
        ).toBe(quantities[i]);
        expect(
          stock.reduce((n: number, r: any) => n + Number(r.reserved), 0),
        ).toBe(0);
      }
      const ledger = (
        await read(context, `/api/v1/inventory/ledger?entity_id=${entity}`)
      ).items;
      // Amounts in this fixture are exact cents; compare scaled integers.
      expect(
        ledger.reduce(
          (n: number, r: any) =>
            n + Math.round(Number(r.amount_delta || 0) * 100),
          0,
        ),
      ).toBe(cost);
      for (const [kind, expected] of [
        ["AR", ar],
        ["AP", ap],
      ]) {
        const data = (
          await read(
            context,
            `/api/v1/settlements/items?entity_id=${entity}&kind=${kind}&party_id=${party.id}`,
          )
        ).items;
        expect(
          data.reduce(
            (n: number, r: any) =>
              n + Math.round(Number(r.outstanding_amount) * 100),
            0,
          ),
        ).toBe(Number(expected) * 100);
      }
      stages.push({ label, quantities, cost: (cost / 100).toFixed(2), ar, ap });
    }
    const emptyWarehouse = await command(context, owner, "/api/v1/warehouses", {
      client_operation_id: randomUUID(),
      entity_id: entity,
      code: "EMPTY",
      name: "零库存采购仓",
      location_code: "B1",
    });
    await check("双批次期初启用", [10, 0], 13000, "0", "0");
    const purchase = await command(
      buyer.context,
      buyer.session,
      "/api/v1/purchase-orders",
      {
        entity_id: entity,
        supplier_id: party.id,
        warehouse_id: emptyWarehouse.id,
        lines: [
          {
            sku_id: product.sku_id,
            qty: "2",
            unit_price: "13",
            tax_rate: "0.13",
          },
        ],
      },
    );
    await command(
      reviewer,
      checker,
      `/api/v1/purchase-orders/${purchase.id}/approve`,
      { expected_version: "1", reason: "首笔采购独立审核" },
    );
    let incoming = await read(
      context,
      `/api/v1/purchase-orders/${purchase.id}`,
    );
    const receipt = await command(
      keeper.context,
      keeper.session,
      `/api/v1/purchase-orders/${purchase.id}/receipts`,
      {
        expected_version: incoming.order.version,
        shipment_ref: "START-DELIVERY",
        transport_note: "首笔到货核对",
        lines: [
          {
            order_line_id: incoming.lines[0].id,
            location_id: emptyWarehouse.location_id,
            qty: "2",
            lot_no: "BUY-" + suffix,
            mfg_date: `${year}-01-01`,
            expiry_date: `${year + 1}-12-31`,
          },
        ],
      },
    );
    incoming = await read(context, `/api/v1/purchase-orders/${purchase.id}`);
    const inspection = await command(
      reviewer,
      checker,
      `/api/v1/receipts/${receipt.id}/inspections`,
      {
        expected_version: "1",
        note: "首笔采购验收合格",
        lines: [
          {
            receipt_line_id: incoming.receipts[0].lines[0].id,
            qualified_qty: "2",
            rejected_qty: "0",
          },
        ],
      },
    );
    await command(
      reviewer,
      checker,
      `/api/v1/receipt-postings/${inspection.receipt_posting_id}/post`,
      {
        expected_version: "1",
        inspection_id: inspection.inspection_id,
        posting_segment_id: inspection.receipt_posting_id,
      },
    );
    await check("零库存仓直接采购入库", [10, 2], 15301, "0", "26");
    const account = await command(
      context,
      owner,
      "/api/v1/settlements/accounts",
      { entity_id: entity, code: "CASH", name: "门店现金", method: "CASH" },
    );
    const retailPage = cashier.page;
    await retailPage.goto("/");
    await nav(retailPage, "零售收银");
    await retailPage
      .getByRole("button", { name: "新建零售单", exact: true })
      .click();
    dialog = retailPage.getByRole("dialog");
    await dialog
      .getByRole("combobox", { name: /^出货仓库/ })
      .selectOption(opened.warehouse_id);
    await dialog.getByLabel("搜索商品").fill("开账验收器械-" + suffix);
    await dialog.getByRole("button", { name: "查找可售商品" }).click();
    await dialog.getByRole("button", { name: "加入", exact: true }).click();
    await dialog.getByLabel("零售数量 1", { exact: true }).fill("3");
    await dialog.getByLabel("零售单价 1", { exact: true }).fill("20");
    await dialog
      .getByRole("button", { name: "建单并预占库存", exact: true })
      .click();
    await expect(dialog).not.toBeVisible();
    await retailPage
      .getByRole("button", { name: "登记现金收款", exact: true })
      .click();
    dialog = retailPage.getByRole("dialog");
    await dialog
      .getByLabel("现金账户", { exact: true })
      .selectOption(account.id);
    await dialog.getByLabel("顾客交付现金（元）", { exact: true }).fill("100");
    await dialog.getByLabel("我已核对现金实收与找零").check();
    await dialog.getByLabel("操作说明").fill("首笔零售收60元、找零40元");
    await dialog.getByRole("button", { name: "确认保存", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await retailPage
      .getByRole("button", { name: "核对并完成出库", exact: true })
      .click();
    dialog = retailPage.getByRole("dialog");
    await dialog.getByLabel("交付数量 1", { exact: true }).fill("3");
    await dialog.getByLabel("交付批号 1", { exact: true }).fill(lot);
    await dialog.getByLabel("操作说明").fill("先出期初近效期批次，核对交付");
    await dialog.getByRole("button", { name: "确认保存", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await check("首笔零售先出近效期3件", [7, 2], 12301, "0", "26");
    const retailSale = (
      await read(cashier.context, `/api/v1/retail/sales?entity_id=${entity}`)
    ).items[0];
    await command(
      context,
      owner,
      `/api/v1/customer-credit/${party.id}?entity_id=${entity}`,
      {
        expected_version: "0",
        credit_limit: "1000",
        valid_until: `${year + 1}-12-31`,
        reason: "首次客户授信",
      },
    );
    const sale = await command(
      seller.context,
      seller.session,
      "/api/v1/sales-orders",
      {
        entity_id: entity,
        customer_id: party.id,
        warehouse_id: opened.warehouse_id,
        delivery_address: "开账验收点",
        lines: [
          {
            sku_id: product.sku_id,
            qty: "3",
            unit_price: "20",
            tax_rate: "0.13",
          },
        ],
      },
    );
    await command(
      reviewer,
      checker,
      `/api/v1/sales-orders/${sale.id}/approve`,
      { expected_version: "1", reason: "首笔批发独立审核" },
    );
    let detail = await read(context, `/api/v1/sales-orders/${sale.id}`);
    const shipment = await command(
      seller.context,
      seller.session,
      `/api/v1/sales-orders/${sale.id}/shipments`,
      {
        expected_version: detail.order.version,
        client_operation_id: randomUUID(),
        lines: [{ order_line_id: detail.lines[0].id, qty: "3" }],
      },
    );
    detail = await read(context, `/api/v1/sales-orders/${sale.id}`);
    const lines = detail.shipments[0].lines.map((r: any) => ({
      shipment_line_id: r.id,
      qty: r.qty,
      lot_no: r.lot_no,
    }));
    // Allocation quantities enforce FEFO; detail rows need not be in expiry order.
    expect(lines).toHaveLength(2);
    expect(
      Object.fromEntries(lines.map((r: any) => [r.lot_no, Number(r.qty)])),
    ).toEqual({ [lot]: 1, [lateLot]: 2 });
    await command(
      keeper.context,
      keeper.session,
      `/api/v1/shipments/${shipment.id}/pick`,
      {
        expected_version: "1",
        lines,
        note: "首笔拣货",
      },
    );
    await command(
      reviewer,
      checker,
      `/api/v1/shipments/${shipment.id}/verify`,
      {
        expected_version: "2",
        lines,
        note: "首笔独立复核",
        shipping_ref: "START-SHIP",
      },
    );
    await command(reviewer, checker, `/api/v1/shipments/${shipment.id}/post`, {
      expected_version: "3",
    });
    await check("首笔批发跨两个期初批次出库", [4, 2], 8301, "60", "26");
    for (const [kind, amount, direction] of [
      ["AR", "60", "RECEIPT"],
      ["AP", "26", "PAYMENT"],
    ]) {
      const item = (
        await read(
          finance.context,
          `/api/v1/settlements/items?entity_id=${entity}&kind=${kind}&party_id=${party.id}`,
        )
      ).items[0];
      const cash = await command(
        finance.context,
        finance.session,
        "/api/v1/settlements/cash-documents",
        {
          client_operation_id: randomUUID(),
          entity_id: entity,
          party_id: party.id,
          account_id: account.id,
          direction,
          amount,
          reference: "OPEN-" + kind + suffix,
          occurred_on: new Intl.DateTimeFormat("en-CA", {
            timeZone: "Asia/Shanghai",
            year: "numeric",
            month: "2-digit",
            day: "2-digit",
          }).format(new Date()),
          note: "首笔往来虚拟款项已核对",
          confirmed: true,
        },
      );
      await command(
        finance.context,
        finance.session,
        "/api/v1/settlements/allocations",
        {
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
        },
      );
    }
    await check("财务结清首笔应收应付", [4, 2], 8301, "0", "0");
    expect(
      (await read(context, `/api/v1/work-queue?entity_id=${entity}`)).items,
    ).toEqual([]);
    const reopened = await provision();
    expect(reopened).toEqual(opened);
    await check("开通命令重试不覆盖营业数据", [4, 2], 8301, "0", "0");
    await page.goto("/");
    await nav(page, "库存台账");
    await expect(
      page.getByRole("row").filter({ hasText: "开账验收器械-" + suffix }),
    ).toHaveCount(6);
    await expect(page.locator(".el-message")).toHaveCount(0);
    await page.screenshot({
      path: "../../artifacts/enterprise-opening-ledger.png",
      fullPage: true,
      animations: "disabled",
    });
    expect(errors).toEqual([]);
    await writeFile(
      "../../artifacts/enterprise-opening-reconciliation.json",
      JSON.stringify(
        {
          recorded_at: new Date().toISOString(),
          run,
          ...opened,
          tenant_name: manifest.tenant_name,
          product_id: product.id,
          sku_id: product.sku_id,
          opening_id: opening.id,
          purchase_id: purchase.id,
          sales_id: sale.id,
          retail_id: retailSale.id,
          party_id: party.id,
          account_id: account.id,
          empty_warehouse_id: emptyWarehouse.id,
          lots: { early: lot, late: lateLot, purchased: "BUY-" + suffix },
          actors: {
            owner: { principal_id: owner.principal_id, role: "OWNER" },
            quality: { principal_id: checker.principal_id, role: "QUALITY" },
            ...Object.fromEntries(
              Object.entries(team).map(([name, a]) => [
                name,
                { principal_id: a.session.principal_id, role: roles[name] },
              ]),
            ),
          },
          stages,
          initial_products: 0,
          initial_stock_ledger: 0,
          expected_members: 7,
          checks: [
            "new tenant, no demo stock copied",
            "cross-tenant and stale-context fences",
            "independent qualification and opening review",
            "warehouse CSV opening, quality work queue review/post with preserved search",
            "first retail consumes early lot; wholesale spans early and late opening lots",
            "zero-stock warehouse directly receives its first purchase",
            "finance settles first AR/AP",
            "idempotent provisioning after business starts",
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
      [resolve("../../scripts/verify_opening_trial.py")],
      { env: { ...process.env, PYTHONUTF8: "1" }, timeout: 30000 },
    );
  } finally {
    await reviewer.close();
    await outsider.close();
    for (const actor of Object.values(team)) await actor.context.close();
  }
});
