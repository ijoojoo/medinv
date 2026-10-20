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
type Trade = { sku: string; pack: string; qty: string; price: string };

test("seven roles buy and sell OTC drugs and devices in different packages without losing surviving row units", async ({
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
    cashier: "CASHIER",
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
    tenant_name: "药械包装营业企业-" + suffix,
    entity_name: "药械包装经营主体",
    credit_code: "DEMO-" + suffix,
    warehouse_code: "MAIN",
    warehouse_name: "药械营业仓",
    location_code: "A1",
    reason: "虚拟药械多商品多包装连续营业验收",
    members: Object.entries(roles).map(([name, role]) => ({
      principal_id: ids["demo." + name],
      role_codes: [role],
    })),
  };
  const manifestPath = resolve("../../artifacts/mixed-packaging-manifest.json");
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
      cashier = team.cashier!,
      finance = team.finance!;
    const send = (actor: Actor, path: string, body: unknown) =>
      command(actor.context, actor.session, path, body);
    const get = (path: string) => read(owner.context, path);
    const goods: any[] = [];
    for (const [code, name, category, base] of [
      ["OTC", "包装验收OTC药品", "DRUG", "盒"],
      ["DEVICE", "包装验收普通器械", "DEVICE", "支"],
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
    async function pack(sku: string, unit: string, count: string) {
      const path = `/api/v1/catalog/skus/${sku}/packaging`;
      const list = await get(path);
      const created = await send(buyer, path, {
        expected_sku_version: list.sku_version,
        unit_name: unit,
        numerator: count,
        denominator: "1",
        reason: "核对基本单位包装比例",
      });
      await send(quality, `${path}/${created.id}/review`, {
        expected_version: "1",
        decision: "APPROVE",
        evidence_ref: "PACK-" + suffix,
        reason: "独立核对包装实物比例",
      });
      return created.id as string;
    }
    const packs = {
      drug_box: await pack(drug.sku_id, "箱", "12"),
      drug_bundle: await pack(drug.sku_id, "中包", "3"),
      device_box: await pack(device.sku_id, "箱", "10"),
      device_bag: await pack(device.sku_id, "包", "5"),
    };
    // Enter a disposable first row, then two real rows. Deleting the first must
    // preserve the later rows' package, quantity and price before any write.
    async function enterOrder(
      actor: Actor,
      purchase: boolean,
      trades: Trade[],
    ) {
      const page = actor.page,
        endpoint = purchase
          ? "/api/v1/purchase-orders"
          : "/api/v1/sales-orders";
      await page.goto("/");
      await nav(page, purchase ? "采购入库" : "批发销售");
      await page
        .getByRole("button", {
          name: purchase ? "新建采购订单" : "新建销售单",
          exact: true,
        })
        .click();
      const dialog = page.locator(".document-editor:visible");
      await chooseParty(
        dialog.getByRole("combobox", { name: purchase ? /^供应商/ : /^客户/ }),
        party.id,
      );
      await dialog
        .getByRole("combobox", { name: purchase ? /^收货仓库/ : /^发货仓库/ })
        .selectOption(opened.warehouse_id);
      if (!purchase) await dialog.getByLabel("收货地址").fill("虚拟药械交付点");
      const all = [
        { sku: device.sku_id, pack: packs.device_box, qty: "1", price: "999" },
        ...trades,
      ];
      for (const [index, trade] of all.entries()) {
        if (index)
          await dialog
            .getByRole("button", { name: "添加商品行", exact: true })
            .click();
        const row = dialog.locator(".line-editor").nth(index);
        await chooseEntrySku(row, trade.sku);
        await row
          .getByRole("combobox", { name: /^交易单位/ })
          .selectOption(trade.pack);
        await row
          .getByLabel(purchase ? "采购数量" : "销售数量", { exact: true })
          .fill(trade.qty);
        await row
          .getByLabel("含税单价（元）", { exact: true })
          .fill(trade.price);
      }
      await dialog
        .locator(".line-editor")
        .first()
        .getByRole("button", { name: "移除此行", exact: true })
        .click();
      await expect(dialog.locator(".line-editor")).toHaveCount(2);
      for (const [index, trade] of trades.entries()) {
        const row = dialog.locator(".line-editor").nth(index);
        await expect(
          row.getByRole("combobox", { name: /^交易单位/ }),
        ).toBeEnabled();
        await expect(
          row.getByRole("combobox", { name: /^交易单位/ }),
          "Deleting another row must preserve this package",
        ).toHaveValue(trade.pack);
        await expect(
          row.getByLabel(purchase ? "采购数量" : "销售数量", { exact: true }),
        ).toHaveValue(trade.qty);
        await expect(
          row.getByLabel("含税单价（元）", { exact: true }),
        ).toHaveValue(trade.price);
      }
      // An intentional SKU change still resets that row's package, not its neighbour.
      const first = dialog.locator(".line-editor").first(),
        second = dialog.locator(".line-editor").nth(1);
      await chooseEntrySku(first, device.sku_id);
      await expect(
        first.getByRole("combobox", { name: /^交易单位/ }),
      ).toHaveValue("");
      await expect(
        second.getByRole("combobox", { name: /^交易单位/ }),
      ).toHaveValue(trades[1]!.pack);
      await chooseEntrySku(first, drug.sku_id);
      await first
        .getByRole("combobox", { name: /^交易单位/ })
        .selectOption(trades[0]!.pack);
      await expect(
        first.getByLabel(purchase ? "采购数量" : "销售数量", { exact: true }),
      ).toHaveValue("");
      await first
        .getByLabel(purchase ? "采购数量" : "销售数量", { exact: true })
        .fill(trades[0]!.qty);
      await first
        .getByLabel("含税单价（元）", { exact: true })
        .fill(trades[0]!.price);
      const response = page.waitForResponse(
        (r) => r.url().endsWith(endpoint) && r.request().method() === "POST",
      );
      await dialog
        .getByRole("button", {
          name: purchase ? "保存采购单" : "保存销售单",
          exact: true,
        })
        .click();
      const saved = await response;
      expect(saved.status(), await saved.text()).toBe(200);
      const submitted = saved.request().postDataJSON();
      expect(submitted.lines).toEqual(
        trades.map((t) => ({
          sku_id: t.sku,
          packaging_id: t.pack,
          qty: t.qty,
          unit_price: t.price,
          tax_rate: "0.13",
        })),
      );
      await expect(dialog).not.toBeVisible();
      return (await saved.json()).data;
    }
    const stocks = async (sku: string) =>
      (
        await get(
          `/api/v1/inventory/availability?entity_id=${entity}&warehouse_id=${opened.warehouse_id}&sku_id=${sku}`,
        )
      ).items;
    async function check(
      label: string,
      quantities: number[],
      cost: number,
      ar: number,
      ap: number,
    ) {
      for (const [i, g] of goods.entries()) {
        const stock = await stocks(g.sku_id);
        expect(
          stock.reduce((sum: number, r: any) => sum + Number(r.on_hand), 0),
        ).toBe(quantities[i]);
        expect(
          stock.reduce((sum: number, r: any) => sum + Number(r.reserved), 0),
        ).toBe(0);
      }
      const ledger = (await get(`/api/v1/inventory/ledger?entity_id=${entity}`))
        .items;
      expect(
        ledger.reduce(
          (sum: number, r: any) =>
            sum + Math.round(Number(r.amount_delta || "0") * 100),
          0,
        ),
      ).toBe(cost * 100);
      for (const [kind, amount] of [
        ["AR", ar],
        ["AP", ap],
      ]) {
        const rows = (
          await get(
            `/api/v1/settlements/items?entity_id=${entity}&kind=${kind}&party_id=${party.id}`,
          )
        ).items;
        expect(
          rows.reduce(
            (sum: number, r: any) =>
              sum + Math.round(Number(r.outstanding_amount) * 100),
            0,
          ),
        ).toBe(Math.round(Number(amount) * 100));
      }
      stages.push({
        label,
        drug_boxes: quantities[0],
        device_pieces: quantities[1],
        cost,
        ar,
        ap,
      });
    }
    const po = await enterOrder(buyer, true, [
      { sku: drug.sku_id, pack: packs.drug_box, qty: "2", price: "135.60" },
      { sku: device.sku_id, pack: packs.device_bag, qty: "3", price: "56.50" },
    ]);
    let purchase = await get(`/api/v1/purchase-orders/${po.id}`);
    expect(purchase.lines.map((r: any) => Number(r.qty))).toEqual([24, 15]);
    expect(purchase.lines.map((r: any) => r.gross_amount)).toEqual([
      "271.20",
      "169.50",
    ]);
    const replacement = await pack(drug.sku_id, "箱", "24");
    const active = await get(
      `/api/v1/catalog/skus/${drug.sku_id}/packaging?approved_only=true`,
    );
    expect(active.items.map((p: any) => p.id)).toContain(replacement);
    expect(active.items.map((p: any) => p.id)).not.toContain(packs.drug_box);
    await send(quality, `/api/v1/purchase-orders/${po.id}/approve`, {
      expected_version: purchase.order.version,
      reason: "原订单保留旧包装换算，独立审核",
    });
    purchase = await get(`/api/v1/purchase-orders/${po.id}`);
    const receipt = await send(
      keeper,
      `/api/v1/purchase-orders/${po.id}/receipts`,
      {
        expected_version: purchase.order.version,
        shipment_ref: "MIXED-DELIVERY",
        transport_note: "分别清点药品24盒与器械15支",
        lines: purchase.lines.map((r: any) => ({
          order_line_id: r.id,
          location_id: opened.location_id,
          qty: r.qty,
          lot_no: goods.find((g) => g.sku_id === r.sku_id).lot,
          mfg_date: `${year}-01-01`,
          expiry_date: `${year + 1}-12-31`,
        })),
      },
    );
    purchase = await get(`/api/v1/purchase-orders/${po.id}`);
    const inspection = await send(
      quality,
      `/api/v1/receipts/${receipt.id}/inspections`,
      {
        expected_version: "1",
        note: "独立核对两种商品与基本数量",
        lines: purchase.receipts[0].lines.map((r: any) => ({
          receipt_line_id: r.id,
          qualified_qty: r.qty,
          rejected_qty: "0",
        })),
      },
    );
    const incoming = await send(
      quality,
      `/api/v1/receipt-postings/${inspection.receipt_posting_id}/post`,
      {
        expected_version: "1",
        inspection_id: inspection.inspection_id,
        posting_segment_id: inspection.receipt_posting_id,
      },
    );
    await check("两种商品采购入库", [24, 15], 390, 0, 440.7);
    const so = await enterOrder(seller, false, [
      { sku: drug.sku_id, pack: packs.drug_bundle, qty: "2", price: "36" },
      { sku: device.sku_id, pack: packs.device_box, qty: "0.5", price: "120" },
    ]);
    let sale = await get(`/api/v1/sales-orders/${so.id}`);
    expect(sale.lines.map((r: any) => Number(r.qty))).toEqual([6, 5]);
    expect(sale.lines.map((r: any) => r.gross_amount)).toEqual([
      "72.00",
      "60.00",
    ]);
    await send(quality, `/api/v1/sales-orders/${so.id}/approve`, {
      expected_version: sale.order.version,
      reason: "独立核对包装价与基本单位需求",
    });
    sale = await get(`/api/v1/sales-orders/${so.id}`);
    const shipment = await send(
      seller,
      `/api/v1/sales-orders/${so.id}/shipments`,
      {
        expected_version: sale.order.version,
        client_operation_id: randomUUID(),
        lines: sale.lines.map((r: any) => ({
          order_line_id: r.id,
          qty: r.qty,
        })),
      },
    );
    sale = await get(`/api/v1/sales-orders/${so.id}`);
    const picked = sale.shipments[0].lines.map((r: any) => ({
      shipment_line_id: r.id,
      qty: r.qty,
      lot_no: r.lot_no,
    }));
    await send(keeper, `/api/v1/shipments/${shipment.id}/pick`, {
      expected_version: "1",
      lines: picked,
      note: "按药品盒数、器械支数清点",
    });
    await send(quality, `/api/v1/shipments/${shipment.id}/verify`, {
      expected_version: "2",
      lines: picked,
      note: "独立复核两种商品",
      shipping_ref: "MIXED-SHIP",
    });
    const outgoing = await send(
      quality,
      `/api/v1/shipments/${shipment.id}/post`,
      { expected_version: "3" },
    );
    await check("不同包装批发出库", [18, 10], 280, 132, 440.7);

    const page = cashier.page;
    await page.goto("/");
    await nav(page, "零售收银");
    await page.getByRole("button", { name: "新建零售单", exact: true }).click();
    let dialog = page.getByRole("dialog");
    await dialog
      .getByRole("combobox", { name: /^出货仓库/ })
      .selectOption(opened.warehouse_id);
    for (const g of goods) {
      await dialog.getByLabel("搜索商品").fill(g.name);
      await dialog
        .getByRole("button", { name: "查找可售商品", exact: true })
        .click();
      await dialog
        .getByRole("row")
        .filter({ hasText: g.name })
        .getByRole("button", { name: "加入", exact: true })
        .click();
    }
    await dialog.getByLabel("零售数量 1", { exact: true }).fill("3");
    await dialog.getByLabel("零售单价 1", { exact: true }).fill("15");
    await dialog
      .getByRole("combobox", { name: /^零售单位 2/ })
      .selectOption(packs.device_bag);
    await dialog.getByLabel("零售数量 2", { exact: true }).fill("1");
    await dialog.getByLabel("零售单价 2", { exact: true }).fill("65");
    // Removing another cart item also keeps the remaining device package.
    await dialog
      .locator(".retail-cart-row")
      .first()
      .getByRole("button", { name: "移除", exact: true })
      .click();
    await expect(
      dialog.getByRole("combobox", { name: /^零售单位 1/ }),
    ).toHaveValue(packs.device_bag);
    await dialog.getByLabel("搜索商品").fill(drug.name);
    await dialog
      .getByRole("button", { name: "查找可售商品", exact: true })
      .click();
    await dialog
      .getByRole("row")
      .filter({ hasText: drug.name })
      .getByRole("button", { name: "加入", exact: true })
      .click();
    await dialog.getByLabel("零售数量 2", { exact: true }).fill("3");
    await dialog.getByLabel("零售单价 2", { exact: true }).fill("15");
    const retailResponse = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/v1/retail/sales") &&
        r.request().method() === "POST",
    );
    await dialog
      .getByRole("button", { name: "建单并预占库存", exact: true })
      .click();
    const savedRetail = await retailResponse;
    expect(savedRetail.status()).toBe(200);
    const pos = (await savedRetail.json()).data;
    await expect(dialog).not.toBeVisible();
    await page
      .getByRole("button", { name: "登记现金收款", exact: true })
      .click();
    dialog = page.getByRole("dialog");
    await dialog
      .getByLabel("现金账户", { exact: true })
      .selectOption(account.id);
    await dialog.getByLabel("顾客交付现金（元）", { exact: true }).fill("120");
    await expect(dialog).toContainText("应找零：10.00 元");
    await dialog.getByLabel("我已核对现金实收与找零").check();
    await dialog
      .getByLabel("操作说明")
      .fill("两种商品合计110元，实收120找零10");
    await dialog.getByRole("button", { name: "确认保存", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    // Packaging maintenance must not double an already paid sale's allocation.
    const deviceReplacement = await pack(device.sku_id, "包", "10");
    const retail = await get(`/api/v1/retail/sales/${pos.id}`);
    expect(
      retail.lines.find((r: any) => r.sku_id === device.sku_id).package_snapshot
        .packaging_id,
    ).toBe(packs.device_bag);
    await page
      .getByRole("button", { name: "核对并完成出库", exact: true })
      .click();
    dialog = page.getByRole("dialog");
    for (const [i, r] of retail.allocations.entries()) {
      await dialog
        .getByLabel(`交付数量 ${i + 1}`, { exact: true })
        .fill(r.allocated_qty);
      await dialog
        .getByLabel(`交付批号 ${i + 1}`, { exact: true })
        .fill(r.lot_no);
    }
    await dialog
      .getByLabel("操作说明")
      .fill("按原包装快照交付5支器械和3盒药品");
    await dialog.getByRole("button", { name: "确认保存", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await check("混合零售完成交付", [15, 5], 200, 132, 440.7);
    for (const [kind, amount, direction] of [
      ["AR", "132", "RECEIPT"],
      ["AP", "440.70", "PAYMENT"],
    ]) {
      const item = (
        await get(
          `/api/v1/settlements/items?entity_id=${entity}&kind=${kind}&party_id=${party.id}`,
        )
      ).items[0];
      const cash = await send(finance, "/api/v1/settlements/cash-documents", {
        client_operation_id: randomUUID(),
        entity_id: entity,
        party_id: party.id,
        account_id: account.id,
        direction,
        amount,
        reference: "MIXED-" + kind + suffix,
        occurred_on: new Intl.DateTimeFormat("en-CA", {
          timeZone: "Asia/Shanghai",
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
        }).format(new Date()),
        note: "核对虚拟收付款并结清两种商品往来",
        confirmed: true,
      });
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
    await check("财务结清往来", [15, 5], 200, 0, 0);
    expect((await get(`/api/v1/work-queue?entity_id=${entity}`)).items).toEqual(
      [],
    );
    await owner.page.goto("/");
    await nav(owner.page, "库存台账");
    await expect(
      owner.page.getByRole("row").filter({ hasText: suffix }),
    ).toHaveCount(6);
    await expect(owner.page.locator(".el-message")).toHaveCount(0);
    await owner.page.screenshot({
      path: "../../artifacts/mixed-packaging-ledger.png",
      fullPage: true,
      animations: "disabled",
    });
    expect(errors).toEqual([]);
    await writeFile(
      resolve("../../artifacts/mixed-packaging-reconciliation.json"),
      JSON.stringify(
        {
          recorded_at: new Date().toISOString(),
          run,
          ...opened,
          tenant_name: manifest.tenant_name,
          goods,
          packs,
          replacements: {
            drug_box: replacement,
            device_bag: deviceReplacement,
          },
          party_id: party.id,
          account_id: account.id,
          documents: {
            purchase: po,
            receipt,
            inspection,
            incoming,
            wholesale: so,
            shipment,
            outgoing,
            retail: pos,
          },
          stages,
          actors: Object.fromEntries(
            Object.entries(team).map(([name, a]) => [
              name,
              { principal_id: a.session.principal_id, role: roles[name] },
            ]),
          ),
          checks: [
            "purchase/sales removal preserves surviving package, quantity and price",
            "intentional SKU change resets only that row",
            "retail removal preserves package",
            "original purchase and paid retail retain retired package snapshots",
            "seven real roles, two product categories, mixed trade units",
            "per-SKU stock, cost, AR/AP and cash reconciled",
          ],
        },
        null,
        2,
      ) + "\n",
    );
    await execute(["scripts/verify_mixed_packaging.py"]);
  } finally {
    for (const actor of Object.values(team)) await actor.context.close();
  }
});
