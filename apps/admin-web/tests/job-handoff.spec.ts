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
async function original(actor: Actor, number: string, kind: string) {
  await actor.page.goto("/");
  const queue = actor.page.getByRole("region", { name: "进销存业务待办" });
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
const scaled = (value: string) => {
  const negative = value.startsWith("-");
  const [whole, fraction = ""] = value.replace(/^-/, "").split(".");
  return (
    (negative ? -1n : 1n) *
    (BigInt(whole!) * 100000000n + BigInt(fraction.padEnd(8, "0")))
  );
};

test("separate purchasing, warehouse, quality, sales, cashier and finance actors hand off original documents", async ({
  browser,
}) => {
  test.setTimeout(240000);
  const team: Record<string, Actor> = {};
  const errors: string[] = [];
  const run = randomUUID(),
    suffix = run.slice(0, 8),
    year = new Date().getFullYear();
  const roles: Record<string, string> = {
    owner: "OWNER",
    quality: "QUALITY",
    purchase: "PURCHASER",
    warehouse: "WAREHOUSE",
    sales: "SALES",
    cashier: "CASHIER",
    finance: "FINANCE",
  };
  const python = resolve(
    process.platform === "win32"
      ? "../../backend/.venv/Scripts/python.exe"
      : "../../backend/.venv/bin/python",
  );
  const execute = async (args: string[]) =>
    promisify(execFile)(python, args, {
      cwd: resolve("../.."),
      env: { ...process.env, PYTHONUTF8: "1" },
      timeout: 30000,
    });
  // These are fixed local development principal IDs; never read or emit credentials.
  const ids = JSON.parse(
    (
      await execute([
        "-c",
        "import json; from uuid import uuid5,NAMESPACE_URL; from scripts.dev_accounts import ACCOUNTS; print(json.dumps({name:str(uuid5(NAMESPACE_URL,'medinv-dev/'+name)) for name,_ in ACCOUNTS}))",
      ])
    ).stdout,
  );
  const manifest = {
    request_id: run,
    tenant_name: "试用岗位企业-" + suffix,
    entity_name: "岗位协作主体-" + suffix,
    credit_code: "DEMO-" + suffix,
    warehouse_code: "TEAM",
    warehouse_name: "协作仓库",
    location_code: "A1",
    reason: "独立岗位真实登录及业务交接验收",
    members: Object.entries(roles).map(([name, role]) => ({
      principal_id: ids["demo." + name],
      role_codes: [role],
    })),
  };
  const manifestPath = resolve("../../artifacts/job-handoff-manifest.json");
  await writeFile(
    manifestPath,
    JSON.stringify(manifest, null, 2) + "\n",
    "utf8",
  );
  const opened = JSON.parse(
    (await execute(["scripts/provision_enterprise.py", manifestPath])).stdout,
  );
  const entity = opened.entity_id;
  try {
    for (const name of Object.keys(roles)) {
      const context = await browser.newContext();
      const page = await context.newPage();
      page.on("pageerror", (e) => errors.push(e.message));
      team[name] = { context, page, session: null };
      let current = await login(context, "demo." + name);
      if (current.tenant_id !== opened.tenant_id) {
        const response = await context.request.post(origin + "/auth/tenant", {
          headers: headers(current),
          data: { tenant_id: opened.tenant_id },
        });
        expect(response.status()).toBe(200);
        current = (await response.json()).data;
      }
      expect(current.role_codes).toEqual([roles[name]]);
      team[name]!.session = current;
    }
    const owner = team.owner!,
      quality = team.quality!,
      purchase = team.purchase!,
      warehouse = team.warehouse!,
      sales = team.sales!,
      cashier = team.cashier!,
      finance = team.finance!;
    const send = (actor: Actor, path: string, body: unknown) =>
      command(actor.context, actor.session, path, body);
    const get = (actor: Actor, path: string) => read(actor.context, path);
    const reject = async (actor: Actor, path: string, body: unknown) => {
      const response = await actor.context.request.post(origin + path, {
        headers: { ...headers(actor.session), "Idempotency-Key": randomUUID() },
        data: body,
      });
      expect(response.status(), path + ": " + (await response.text())).toBe(
        403,
      );
    };
    const members = await get(owner, "/control/members");
    for (const [code, name] of Object.entries({
      PURCHASER: "采购员",
      WAREHOUSE: "仓储员",
      SALES: "销售员",
      CASHIER: "收银员",
    }))
      expect(members.roles).toContainEqual({ code, name });
    const product = await send(purchase, "/api/v1/catalog/products", {
      code: "TEAM-P",
      name: "协作器械-" + suffix,
      category: "DEVICE",
      manufacturer_name: "软件测试厂家",
      specification: "单件",
      approval_number: "DEMO-ONLY",
      valid_from: `${year}-01-01`,
      valid_until: `${year + 2}-12-31`,
      sku_code: "TEAM-SKU",
      base_unit: "个",
      qty_scale: 0,
    });
    const party = await send(purchase, "/api/v1/catalog/parties", {
      entity_id: entity,
      code: "TEAM-PARTY",
      name: "协作往来单位",
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
    ]) {
      const body = {
        expected_version: "1",
        decision: "APPROVE",
        evidence_ref: "DEMO",
        reason: "核对虚拟资质",
      };
      await reject(purchase, `/api/v1/catalog/${kind}/${id}/review`, body);
      await send(quality, `/api/v1/catalog/${kind}/${id}/review`, body);
    }
    const account = await send(owner, "/api/v1/settlements/accounts", {
      entity_id: entity,
      code: "CASH",
      name: "协作现金账户",
      method: "CASH",
    });
    await send(
      owner,
      `/api/v1/customer-credit/${party.id}?entity_id=${entity}`,
      {
        expected_version: "0",
        credit_limit: "1000",
        valid_until: `${year + 1}-12-31`,
        reason: "批准试用客户额度",
      },
    );
    await purchase.page.goto("/");
    await expect(purchase.page.locator(".role-value")).toHaveText("采购员");
    await nav(purchase.page, "采购入库");
    await purchase.page
      .getByRole("button", { name: "新建采购订单", exact: true })
      .click();
    let dialog = purchase.page.getByRole("dialog");
    await chooseParty(
      dialog.getByRole("combobox", { name: "供应商", exact: true }),
      party.id,
    );
    await chooseEntrySku(dialog, product.sku_id);
    await dialog.getByLabel("采购数量").fill("10");
    await dialog.getByLabel("含税单价（元）").fill("13");
    await dialog.getByRole("button", { name: "保存采购单" }).click();
    await expect(dialog).not.toBeVisible();
    const purchaseNo = await purchase.page
      .locator(".document-number")
      .first()
      .innerText();
    const po = (
      await get(purchase, `/api/v1/purchase-orders?entity_id=${entity}`)
    ).items[0];
    await expect(
      purchase.page.getByRole("button", { name: "审核采购单", exact: true }),
    ).toHaveCount(0);
    await reject(warehouse, `/api/v1/purchase-orders/${po.id}/approve`, {
      expected_version: "1",
      reason: "仓储不得审核采购",
    });
    await original(quality, purchaseNo, "purchase_approve");
    await quality.page
      .getByRole("button", { name: "审核采购单", exact: true })
      .click();
    dialog = quality.page.getByRole("dialog");
    await dialog.getByLabel("操作说明").fill("质管独立审核");
    await dialog.getByRole("button", { name: "确认审核", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await original(warehouse, purchaseNo, "purchase_receive");
    await expect(
      warehouse.page.getByRole("button", { name: "修改采购单", exact: true }),
    ).toHaveCount(0);
    await warehouse.page
      .getByRole("button", { name: "登记到货", exact: true })
      .click();
    dialog = warehouse.page.getByRole("dialog");
    const lot = "TEAM-" + suffix;
    await dialog.getByLabel("随货单号").fill(lot);
    await dialog.getByLabel("运输及随货核查").fill("仓储核对到货实物");
    await dialog.getByLabel("到货数量").fill("10");
    await dialog.getByLabel("生产批号").fill(lot);
    await dialog.getByLabel("生产日期").fill(`${year}-01-01`);
    await dialog.getByLabel("有效期至").fill(`${year + 1}-12-31`);
    await dialog.getByRole("button", { name: "保存到货记录" }).click();
    await expect(dialog).not.toBeVisible();
    await expect(
      warehouse.page.getByRole("button", { name: "登记验收", exact: true }),
    ).toHaveCount(0);
    await original(quality, purchaseNo, "purchase_inspect");
    await quality.page
      .getByRole("button", { name: "登记验收", exact: true })
      .click();
    dialog = quality.page.getByRole("dialog");
    await dialog.getByLabel("本次合格数量").fill("10");
    await dialog.getByLabel("本次拒收数量").fill("0");
    await dialog.getByLabel("验收依据与结论").fill("独立验收10件合格");
    await dialog.getByRole("button", { name: "保存验收记录" }).click();
    await expect(dialog).not.toBeVisible();
    await original(quality, purchaseNo, "purchase_post");
    await quality.page
      .getByRole("button", { name: "合格入库", exact: true })
      .click();
    dialog = quality.page.getByRole("dialog");
    await dialog.getByRole("button", { name: "确认入库", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    const checkpoints: any[] = [];
    async function check(
      label: string,
      qty: string,
      cost: string,
      ar: string,
      ap: string,
    ) {
      const stock = (
        await get(
          owner,
          `/api/v1/inventory/availability?entity_id=${entity}&warehouse_id=${opened.warehouse_id}&sku_id=${product.sku_id}`,
        )
      ).items;
      expect(
        stock.reduce((n: bigint, r: any) => n + scaled(r.on_hand), 0n),
      ).toBe(scaled(qty));
      const ledger = (
        await get(owner, `/api/v1/inventory/ledger?entity_id=${entity}`)
      ).items;
      expect(
        ledger.reduce(
          (n: bigint, r: any) => n + scaled(r.amount_delta || "0"),
          0n,
        ),
      ).toBe(scaled(cost));
      for (const [kind, value] of [
        ["AR", ar],
        ["AP", ap],
      ]) {
        const items = (
          await get(
            finance,
            `/api/v1/settlements/items?entity_id=${entity}&kind=${kind}&party_id=${party.id}`,
          )
        ).items;
        expect(
          items.reduce(
            (n: bigint, r: any) => n + scaled(r.outstanding_amount),
            0n,
          ),
        ).toBe(scaled(value!));
      }
      checkpoints.push({ label, qty, cost, ar, ap });
    }
    await check("采购三岗位交接完成", "10", "115.04", "0", "130");
    const saleBody = {
      entity_id: entity,
      customer_id: party.id,
      warehouse_id: opened.warehouse_id,
      delivery_address: "软件验收点",
      lines: [
        {
          sku_id: product.sku_id,
          qty: "4",
          unit_price: "20",
          tax_rate: "0.13",
        },
      ],
    };
    await reject(warehouse, "/api/v1/sales-orders", saleBody);
    const so = await send(sales, "/api/v1/sales-orders", saleBody);
    await original(quality, so.number, "sales_approve");
    await quality.page
      .getByRole("button", { name: "审核销售单", exact: true })
      .click();
    dialog = quality.page.getByRole("dialog");
    await dialog.getByLabel("操作说明").fill("销售资质及授信独立核对");
    await dialog.getByRole("button", { name: "确认审核", exact: true }).click();
    await expect(dialog).not.toBeVisible();
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
    detail = await get(sales, `/api/v1/sales-orders/${so.id}`);
    const lines = detail.shipments[0].lines.map((r: any) => ({
      shipment_line_id: r.id,
      qty: r.qty,
      lot_no: r.lot_no,
    }));
    await reject(sales, `/api/v1/shipments/${shipment.id}/pick`, {
      expected_version: "1",
      lines,
      note: "销售不得拣货",
    });
    await original(warehouse, shipment.number, "sales_pick");
    await warehouse.page
      .getByRole("button", { name: "登记拣货", exact: true })
      .click();
    dialog = warehouse.page.getByRole("dialog");
    await dialog.getByLabel("实物批号").fill(lot);
    await dialog.getByLabel("实物数量").fill("4");
    await dialog.getByLabel("核查说明").fill("仓储按批号拣货");
    await dialog.getByRole("button", { name: "保存拣货", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await reject(warehouse, `/api/v1/shipments/${shipment.id}/verify`, {
      expected_version: "2",
      lines,
      note: "仓储不得自行复核",
      shipping_ref: lot,
    });
    await original(quality, shipment.number, "sales_verify");
    await quality.page
      .getByRole("button", { name: "出库复核", exact: true })
      .click();
    dialog = quality.page.getByRole("dialog");
    await dialog.getByLabel("实物批号").fill(lot);
    await dialog.getByLabel("实物数量").fill("4");
    await dialog.getByLabel("核查说明").fill("质管独立核对批号数量");
    await dialog.getByLabel("物流 / 交接凭据").fill("SHIP-" + suffix);
    await dialog.getByRole("button", { name: "确认复核", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await original(quality, shipment.number, "sales_post");
    await quality.page
      .getByRole("button", { name: "销售出库", exact: true })
      .click();
    dialog = quality.page.getByRole("dialog");
    await dialog.getByRole("button", { name: "确认出库", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await check("销售仓储质管交接完成", "6", "69.024", "80", "130");
    await cashier.page.goto("/");
    await expect(cashier.page.locator(".role-value")).toHaveText("收银员");
    for (const name of ["采购入库", "批发销售", "往来结算", "成员与权限"])
      await expect(
        cashier.page.getByRole("button", { name, exact: true }),
      ).toHaveCount(0);
    await nav(cashier.page, "零售收银");
    await cashier.page
      .getByRole("button", { name: "新建零售单", exact: true })
      .click();
    dialog = cashier.page.getByRole("dialog");
    await dialog.getByLabel("搜索商品").fill("协作器械-" + suffix);
    await dialog.getByRole("button", { name: "查找可售商品" }).click();
    await dialog.getByRole("button", { name: "加入", exact: true }).click();
    await dialog.getByLabel("零售数量 1", { exact: true }).fill("2");
    await dialog.getByLabel("零售单价 1", { exact: true }).fill("20");
    await dialog
      .getByRole("button", { name: "建单并预占库存", exact: true })
      .click();
    await expect(dialog).not.toBeVisible();
    await cashier.page
      .getByRole("button", { name: "登记现金收款", exact: true })
      .click();
    dialog = cashier.page.getByRole("dialog");
    await dialog
      .getByLabel("现金账户", { exact: true })
      .selectOption(account.id);
    await dialog.getByLabel("顾客交付现金（元）", { exact: true }).fill("50");
    await dialog.getByLabel("我已核对现金实收与找零").check();
    await dialog.getByLabel("操作说明").fill("收银收到50元、找零10元");
    await dialog.getByRole("button", { name: "确认保存", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await cashier.page
      .getByRole("button", { name: "核对并完成出库", exact: true })
      .click();
    dialog = cashier.page.getByRole("dialog");
    await dialog.getByLabel("交付数量 1", { exact: true }).fill("2");
    await dialog.getByLabel("交付批号 1", { exact: true }).fill(lot);
    await dialog.getByLabel("操作说明").fill("收银核对交付实物");
    await dialog.getByRole("button", { name: "确认保存", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await check("收银交付完成", "4", "46.016", "80", "130");
    const forbiddenRead = await cashier.context.request.get(
      origin + `/api/v1/settlements/items?entity_id=${entity}&kind=AR`,
      { headers: headers(cashier.session) },
    );
    expect(forbiddenRead.status()).toBe(403);
    await reject(cashier, "/api/v1/settlements/accounts", {
      entity_id: entity,
      code: "FORBIDDEN",
      name: "收银不得管理账户",
      method: "CASH",
    });
    await finance.page.goto("/");
    await nav(finance.page, "往来结算");
    for (const [tab, action, amount] of [
      ["应收账款", "登记收款", "80"],
      ["应付账款", "登记付款", "130"],
    ]) {
      await finance.page.getByRole("tab", { name: tab, exact: true }).click();
      const row = finance.page
        .getByRole("row")
        .filter({ hasText: "协作往来单位" });
      await row.getByRole("button", { name: action === "新建采购单" ? "新建采购订单" : action, exact: true }).click();
      dialog = finance.page.getByRole("dialog");
      await dialog
        .getByRole("combobox", { name: "资金账户", exact: true })
        .selectOption(account.id);
      await dialog.getByLabel("实际金额（元）").fill(amount!);
      await dialog.getByLabel("流水 / 凭据编号").fill("FIN-" + action + suffix);
      await dialog.getByLabel("核对说明").fill("财务核对虚拟收付凭据");
      await dialog.getByLabel("我已核对款项实际发生及收付凭据").check();
      await dialog.getByRole("button", { name: "确认登记款项" }).click();
      await expect(dialog).not.toBeVisible();
      await finance.page
        .getByRole("button", { name: "核销单据", exact: true })
        .click();
      dialog = finance.page.getByRole("dialog");
      await dialog.getByLabel("本次核销金额").fill(amount!);
      await dialog
        .getByRole("button", { name: "确认核销", exact: true })
        .click();
      await expect(dialog).not.toBeVisible();
      await finance.page.getByRole("button", { name: "返回往来账" }).click();
    }
    await check("财务收付款核销完成", "4", "46.016", "0", "0");
    expect(
      (await get(owner, `/api/v1/work-queue?entity_id=${entity}`)).items,
    ).toEqual([]);
    // Permission removal invalidates an already authenticated cashier on core and web.
    const member = members.items.find(
      (r: any) => r.principal_id === cashier.session.principal_id,
    );
    const change = await owner.context.request.patch(
      origin + `/control/members/${member.principal_id}`,
      {
        headers: headers(owner.session),
        data: {
          expected_version: member.version,
          role_codes: ["VIEWER"],
          entity_ids: [entity],
          active: true,
        },
      },
    );
    expect(change.status()).toBe(200);
    const stale = await cashier.context.request.get(
      origin + `/api/v1/retail/sales?entity_id=${entity}`,
      { headers: headers(cashier.session) },
    );
    expect(stale.status()).toBe(401);
    const updatedMembers = await get(owner, "/control/members");
    const restore = await owner.context.request.patch(
      origin + `/control/members/${member.principal_id}`,
      {
        headers: headers(owner.session),
        data: {
          expected_version: updatedMembers.items.find(
            (r: any) => r.principal_id === member.principal_id,
          ).version,
          role_codes: ["CASHIER"],
          entity_ids: [entity],
          active: true,
        },
      },
    );
    expect(restore.status()).toBe(200);
    await owner.page.goto("/");
    await nav(owner.page, "成员与权限");
    for (const name of ["采购员", "仓储员", "销售员", "收银员"])
      await expect(
        owner.page.getByRole("cell", { name, exact: true }),
      ).toHaveCount(1);
    await expect(owner.page.locator(".el-message")).toHaveCount(0);
    await owner.page.screenshot({
      path: "../../artifacts/job-handoff-members.png",
      fullPage: true,
    });
    await nav(owner.page, "库存台账");
    await expect(
      owner.page.getByRole("row").filter({ hasText: lot }),
    ).toHaveCount(3);
    await owner.page.screenshot({
      path: "../../artifacts/job-handoff-ledger.png",
      fullPage: true,
    });
    expect(errors).toEqual([]);
    const poFinal = await get(owner, `/api/v1/purchase-orders/${po.id}`);
    const soFinal = await get(owner, `/api/v1/sales-orders/${so.id}`);
    expect(poFinal.order.created_by).toBe(purchase.session.principal_id);
    expect(poFinal.receipts[0].created_by).toBe(warehouse.session.principal_id);
    expect(soFinal.order.created_by).toBe(sales.session.principal_id);
    await writeFile(
      "../../artifacts/job-handoff-reconciliation.json",
      JSON.stringify(
        {
          recorded_at: new Date().toISOString(),
          run,
          tenant_name: manifest.tenant_name,
          ...opened,
          actors: Object.fromEntries(
            Object.entries(team).map(([k, v]) => [
              k,
              { principal_id: v.session.principal_id, role: roles[k] },
            ]),
          ),
          sku_id: product.sku_id,
          purchase_id: po.id,
          sales_id: so.id,
          checkpoints,
          final_qty: "4",
          final_cost: "46.016",
          final_ar: "0",
          final_ap: "0",
          checks: [
            "independent real OIDC identities",
            "purchase and warehouse handoff via work queue",
            "quality independent review and posting",
            "sales cannot pick; warehouse cannot verify",
            "cashier without finance access completes cash sale",
            "finance collects, pays and allocates",
            "removed role revokes existing core session",
          ],
        },
        null,
        2,
      ) + "\n",
      "utf8",
    );
  } finally {
    for (const actor of Object.values(team)) await actor.context.close();
  }
});
