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
  await dialog.getByRole("button", { name: "保存", exact: true }).click();
  await expect(dialog).not.toBeVisible();
}
const scaled = (value: string) => {
  const negative = value.startsWith("-");
  const [whole, fraction = ""] = value.replace(/^-/, "").split(".");
  return (negative ? -1n : 1n) * BigInt(whole! + fraction.padEnd(8, "0"));
};

test("warehouse and quality close transfers, losses, quarantine and counts from original work items", async ({
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
    warehouse: "WAREHOUSE",
    cashier: "CASHIER",
    viewer: "VIEWER",
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
    tenant_name: "库存协作企业-" + suffix,
    entity_name: "库存协作主体",
    credit_code: "DEMO-" + suffix,
    warehouse_code: "IJ",
    warehouse_name: "库存协作仓A",
    location_code: "A1",
    reason: "调拨、差异处理和盘点岗位交接验证",
    members: Object.entries(roles).map(([name, role]) => ({
      principal_id: ids["demo." + name],
      role_codes: [role],
    })),
  };
  const manifestPath = resolve("../../artifacts/inventory-jobs-manifest.json");
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
      warehouse = team.warehouse!,
      cashier = team.cashier!,
      viewer = team.viewer!;
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

    const dest = await send(owner, "/api/v1/warehouses", {
      client_operation_id: randomUUID(),
      entity_id: f.entity,
      code: "IJ-B",
      name: "库存协作仓B",
      location_code: "B1",
    });
    const checkpoints: any[] = [],
      documents: any[] = [];
    const availability = (warehouseId: string) =>
      get(
        owner,
        `/api/v1/inventory/availability?entity_id=${f.entity}&warehouse_id=${warehouseId}&sku_id=${f.product.sku_id}`,
      );
    async function check(label: string, qty: string, cost: string) {
      const ledger = await get(
        owner,
        `/api/v1/inventory/ledger?entity_id=${f.entity}`,
      );
      expect(ledger.has_more).toBe(false);
      expect(
        ledger.items.reduce((v: bigint, r: any) => v + scaled(r.qty_delta), 0n),
      ).toBe(scaled(qty));
      expect(
        ledger.items.reduce(
          (v: bigint, r: any) => v + scaled(r.amount_delta || "0"),
          0n,
        ),
      ).toBe(scaled(cost));
      checkpoints.push({ label, qty, cost });
    }
    async function back(a: Actor, search: string, remains = false) {
      const dialog = a.page.getByRole("dialog");
      if (await dialog.isVisible())
        await dialog
          .getByRole("button", { name: "关闭详情", exact: true })
          .click();
      await a.page
        .getByRole("button", { name: "返回业务待办", exact: true })
        .click();
      await expect(a.page.getByLabel("查找待办")).toHaveValue(search);
      const queue = a.page.getByRole("region", { name: "进销存业务待办" });
      await expect(
        queue.getByRole("button", { name: "查询待办", exact: true }),
      ).toBeEnabled();
      await expect(
        queue.getByRole("row").filter({ hasText: search }),
      ).toHaveCount(remains ? 1 : 0);
    }
    async function transfer(qty: string) {
      const candidates = await get(
        warehouse,
        `/api/v1/transfers/candidates?entity_id=${f.entity}&warehouse_id=${f.warehouse}&search=${f.lot}`,
      );
      const doc = await send(warehouse, "/api/v1/transfers", {
        client_operation_id: randomUUID(),
        entity_id: f.entity,
        source_warehouse_id: f.warehouse,
        destination_warehouse_id: dest.id,
        destination_location_id: dest.location_id,
        reason: "仓储按实际需要调拨",
        lines: [{ balance_id: candidates.items[0].id, qty }],
      });
      documents.push({ type: "transfer", ...doc });
      await original(warehouse, doc.number, "transfer_review");
      await expect(
        warehouse.page.getByRole("button", {
          name: "独立审核调拨",
          exact: true,
        }),
      ).toHaveCount(0);
      await original(quality, doc.number, "transfer_review");
      await quality.page
        .getByRole("button", { name: "独立审核调拨", exact: true })
        .click();
      await confirm(quality.page, "质管独立审核调拨路线与数量");
      await original(warehouse, doc.number, "transfer_dispatch");
      await warehouse.page
        .getByRole("button", { name: "确认调拨发出", exact: true })
        .click();
      await confirm(warehouse.page, "仓储已核对并发出实物");
      return doc;
    }
    async function receive(doc: any, qty: string, status = "QUALIFIED") {
      await original(warehouse, doc.number, "transfer_receive");
      await warehouse.page
        .getByRole("button", { name: "登记本次收货", exact: true })
        .click();
      const dialog = warehouse.page.getByRole("dialog");
      await dialog.getByLabel("本次收货数量 1", { exact: true }).fill(qty);
      await dialog.getByLabel("实物批号 1", { exact: true }).fill(f.lot);
      await dialog
        .getByLabel("收货质量 1", { exact: true })
        .selectOption(status);
      await confirm(warehouse.page, "仅登记本次已收到的实物");
    }
    async function claim(doc: any) {
      await warehouse.page
        .getByRole("button", { name: "申请在途损失", exact: true })
        .click();
      const dialog = warehouse.page.getByRole("dialog");
      await dialog.getByLabel("损失数量", { exact: true }).fill("1");
      await dialog.getByLabel("确认批号", { exact: true }).fill(f.lot);
      await dialog
        .getByLabel("损失原因", { exact: true })
        .fill("待独立核查的运输短少");
      await dialog
        .getByLabel("核查依据", { exact: true })
        .fill("运输交接记录 IJ-" + suffix);
      await dialog
        .getByRole("button", { name: "提交损失申请", exact: true })
        .click();
      await expect(dialog.getByTestId("loss-state")).toHaveText("待审核");
      const record = (
        await get(warehouse, `/api/v1/transfers/${doc.id}/losses`)
      ).items[0];
      documents.push({ type: "loss", id: record.id, number: record.number });
      await dialog
        .getByRole("button", { name: "关闭详情", exact: true })
        .click();
      return record;
    }
    async function lossAction(a: Actor, button: string, reason: string) {
      const dialog = a.page.getByRole("dialog");
      await dialog.getByRole("button", { name: button, exact: true }).click();
      await dialog.getByLabel("操作说明", { exact: true }).fill(reason);
      await dialog
        .getByRole("button", { name: "保存损失处理", exact: true })
        .click();
    }
    await check("采购入库", "10", "115.04");
    const first = await transfer("8");
    await check("调拨发出仍为自有库存", "10", "115.04");
    await receive(first, "4");
    await receive(first, "3", "QUARANTINE");
    const lost = await claim(first);
    await original(viewer, lost.number, "transfer_loss_review");
    await expect(viewer.page.getByRole("dialog")).toContainText(lost.number);
    await expect(
      viewer.page.getByRole("button", { name: "独立审核损失", exact: true }),
    ).toHaveCount(0);
    await deny(
      viewer,
      `/api/v1/transfers/${first.id}/losses/${lost.id}/review`,
      {
        expected_version: "1",
        decision: "APPROVE",
        reason: "只读账号不得审核",
      },
    );
    await original(quality, lost.number, "transfer_loss_review");
    await lossAction(quality, "独立审核损失", "已复核承运记录及实物差异");
    await expect(quality.page.getByTestId("loss-state")).toHaveText("待核销");
    await back(quality, lost.number);
    await original(quality, lost.number, "transfer_loss_post");
    await expect(
      quality.page.getByRole("dialog").getByTestId("loss-state"),
    ).toHaveText("待核销");
    await quality.page.screenshot({
      path: "../../artifacts/inventory-jobs-loss.png",
      fullPage: true,
      animations: "disabled",
    });
    await lossAction(quality, "确认损失核销", "确认短少1件，核销在途和成本");
    await expect(quality.page.getByTestId("loss-state")).toHaveText("已核销");
    await back(quality, lost.number);
    const firstDetail = await get(owner, `/api/v1/transfers/${first.id}`);
    expect(firstDetail.document.state).toBe("CLOSED_WITH_LOSS");
    expect(firstDetail.document.created_by).toBe(
      warehouse.session.principal_id,
    );
    expect(firstDetail.document.reviewed_by).toBe(quality.session.principal_id);
    expect(scaled(firstDetail.lines[0].remaining_qty)).toBe(0n);
    await check("实收7件、核销短少1件", "9", "103.536");
    const b = await availability(dest.id);
    expect(
      scaled(
        b.items.find((r: any) =>
          r.ineligibility_codes.includes("QUALITY_NOT_QUALIFIED"),
        ).available,
      ),
    ).toBe(0n);
    for (const [kind, qty, total, cost] of [
      ["RELEASE", "2", "9", "103.536"],
      ["WRITE_OFF", "1", "8", "92.032"],
    ]) {
      await warehouse.page.goto("/");
      await nav(warehouse.page, "质量处置");
      await warehouse.page
        .getByRole("button", { name: "新建质量处置", exact: true })
        .click();
      await warehouse.page.getByLabel("检索批次").fill(f.lot);
      await warehouse.page
        .getByRole("button", { name: "查询待处置库存", exact: true })
        .click();
      await warehouse.page
        .getByRole("row")
        .filter({ hasText: f.lot })
        .getByRole("button", { name: "选择", exact: true })
        .click();
      await warehouse.page
        .getByLabel("处置方式", { exact: true })
        .selectOption(kind!);
      await warehouse.page.getByLabel("处置数量", { exact: true }).fill(qty!);
      await warehouse.page.getByLabel("实物批号", { exact: true }).fill(f.lot);
      await warehouse.page
        .getByLabel("申请原因", { exact: true })
        .fill(kind === "RELEASE" ? "复验合格申请放行" : "确认破损申请报损");
      await warehouse.page
        .getByLabel("复验 / 处置依据", { exact: true })
        .fill("复验及处置记录 IJ-" + suffix);
      await warehouse.page
        .getByRole("button", { name: "提交处置申请", exact: true })
        .click();
      await expect(warehouse.page.locator(".el-tag")).toHaveText("待审核");
      const doc = (
        await get(owner, `/api/v1/quality-dispositions?entity_id=${f.entity}`)
      ).items[0];
      documents.push({ type: kind, id: doc.id, number: doc.number });
      await original(viewer, doc.number, "quality_review");
      await expect(
        viewer.page.getByRole("button", { name: "独立审核处置", exact: true }),
      ).toHaveCount(0);
      await deny(viewer, `/api/v1/quality-dispositions/${doc.id}/review`, {
        expected_version: "1",
        decision: "APPROVE",
        reason: "只读账号不得审核",
      });
      await original(quality, doc.number, "quality_review");
      await quality.page
        .getByRole("button", { name: "独立审核处置", exact: true })
        .click();
      await confirm(quality.page, "独立核查实物质量及依据");
      await back(quality, doc.number);
      await original(quality, doc.number, "quality_post");
      await quality.page
        .getByRole("button", { name: "确认处置过账", exact: true })
        .click();
      await confirm(quality.page, "确认处置完成并记账");
      await expect(quality.page.locator(".el-tag")).toHaveText("已过账");
      await back(quality, doc.number);
      await check(
        kind === "RELEASE" ? "放行只改变质量状态" : "报损扣减实物和成本",
        total!,
        cost!,
      );
    }
    // Delayed arrival closes the transfer; its unresolved loss application must remain visible.
    const second = await transfer("1"),
      late = await claim(second);
    await original(quality, late.number, "transfer_loss_review");
    await lossAction(
      quality,
      "独立审核损失",
      "按当时核查记录审核，最终仍须核对在途",
    );
    await receive(second, "1");
    expect(
      (await get(owner, `/api/v1/transfers/${second.id}`)).document.state,
    ).toBe("RECEIVED");
    await original(warehouse, late.number, "transfer_loss_post");
    await expect(warehouse.page.getByRole("dialog")).toContainText(
      "调拨已结清，本申请不能继续核销",
    );
    await expect(
      warehouse.page.getByRole("button", { name: "确认损失核销", exact: true }),
    ).toHaveCount(0);
    await lossAction(
      warehouse,
      "取消损失申请",
      "迟到货物已收齐，取消遗留短少申请",
    );
    await expect(warehouse.page.getByTestId("loss-state")).toHaveText("已取消");
    await back(warehouse, late.number);
    await check("迟到货物与遗留损失结清", "8", "92.032");
    const balances = await get(
      warehouse,
      `/api/v1/stock-counts/candidates?entity_id=${f.entity}&warehouse_id=${dest.id}&search=${f.lot}`,
    );
    const qualified = balances.items.find(
      (r: any) => r.quality_status === "QUALIFIED",
    );
    const count = await send(warehouse, "/api/v1/stock-counts", {
      client_operation_id: randomUUID(),
      entity_id: f.entity,
      warehouse_id: dest.id,
      balance_ids: [qualified.id],
      reason: "调拨差异处理后实盘核对",
    });
    documents.push({ type: "count", ...count });
    const freezeCheck = async () => {
      const a = await availability(f.warehouse);
      expect(a.items[0].ineligibility_codes).toContain("COUNT_FROZEN");
      const response = await cashier.context.request.post(
        origin + "/api/v1/retail/sales",
        {
          headers: {
            ...headers(cashier.session),
            "Idempotency-Key": randomUUID(),
          },
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
      expect(response.status()).toBe(409);
      expect((await response.json()).error.code).toBe("COUNT_FROZEN");
    };
    await freezeCheck();
    await original(warehouse, count.number, "count_enter");
    await warehouse.page
      .getByRole("button", { name: "录入实盘并提交", exact: true })
      .click();
    let dialog = warehouse.page.getByRole("dialog");
    await dialog.getByLabel("实盘数量 1", { exact: true }).fill("6");
    await dialog.getByLabel("实物批号 1", { exact: true }).fill(f.lot);
    await dialog
      .getByLabel("实盘依据 1", { exact: true })
      .fill("两次清点一致，实物比账面短少1件");
    await confirm(warehouse.page, "仓储确认实盘数量");
    await original(quality, count.number, "count_review");
    await quality.page
      .getByRole("button", { name: "独立审核盘点", exact: true })
      .click();
    await confirm(quality.page, "独立核对实盘和差异依据");
    await freezeCheck();
    await original(quality, count.number, "count_post");
    await quality.page
      .getByRole("button", { name: "确认盘点过账", exact: true })
      .click();
    await confirm(quality.page, "核对盘亏1件及成本，正式结单");
    await expect(quality.page.locator(".el-tag")).toHaveText("已过账");
    await back(quality, count.number);
    await check("盘亏1件结单后解除冻结", "7", "80.528");
    const a = await availability(f.warehouse),
      finalB = await availability(dest.id);
    expect(scaled(a.items[0].available)).toBe(scaled("1"));
    expect(
      finalB.items.reduce((v: bigint, r: any) => v + scaled(r.available), 0n),
    ).toBe(scaled("6"));
    const sale = await send(cashier, "/api/v1/retail/sales", {
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
    await send(cashier, `/api/v1/retail/sales/${sale.id}/cancel`, {
      expected_version: "1",
      reason: "验证结单恢复销售，取消未收款验证单",
    });
    const countDoc = (await get(owner, `/api/v1/stock-counts/${count.id}`))
      .document;
    expect(countDoc.created_by).toBe(warehouse.session.principal_id);
    expect(countDoc.reviewed_by).toBe(quality.session.principal_id);
    const pending = await get(
      owner,
      `/api/v1/work-queue?entity_id=${f.entity}`,
    );
    expect(pending.items).toEqual([]);
    expect(pending.kinds).toHaveLength(32);
    const ap = await get(
      owner,
      `/api/v1/settlements/items?entity_id=${f.entity}&kind=AP&party_id=${f.party.id}`,
    );
    expect(ap.items).toHaveLength(1);
    expect(scaled(ap.items[0].outstanding_amount)).toBe(scaled("130"));
    expect(
      (
        await get(
          owner,
          `/api/v1/settlements/items?entity_id=${f.entity}&kind=AR&party_id=${f.party.id}`,
        )
      ).items,
    ).toEqual([]);
    await owner.page.goto("/");
    await nav(owner.page, "库存台账");
    await expect(
      owner.page.getByRole("row").filter({ hasText: "盘亏出库" }),
    ).toHaveCount(1);
    await owner.page.screenshot({
      path: "../../artifacts/inventory-jobs-ledger.png",
      fullPage: true,
      animations: "disabled",
    });
    expect(errors).toEqual([]);
    await writeFile(
      resolve("../../artifacts/inventory-jobs-reconciliation.json"),
      JSON.stringify(
        {
          recorded_at: new Date().toISOString(),
          tenant_name: manifest.tenant_name,
          tenant_id: opened.tenant_id,
          entity_id: f.entity,
          documents,
          checkpoints,
          final_qty: "7",
          final_cost: "80.528",
          warehouse_a: "1",
          warehouse_b: "6",
          final_ar: "0",
          final_ap: "130",
          pending_work: 0,
          actors: Object.fromEntries(
            Object.entries(team).map(([name, a]) => [
              name,
              { principal_id: a.session.principal_id, role: roles[name] },
            ]),
          ),
          checks: [
            "original warehouse/quality work item handoff",
            "partial qualified and quarantine receipts",
            "loss after late full receipt remains visible",
            "independent release and write-off",
            "count freeze crosses warehouses until posting",
            "cashier can sell after posting",
            "viewer cannot approve",
            "no settlement changes",
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
