import { test, expect, type BrowserContext, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { readFile, writeFile, mkdir, chmod } from "node:fs/promises";
import { resolve } from "node:path";
import { login, headers, view } from "./helpers";
import {
  command,
  read,
  stockedCustomer,
  origin,
  nav,
} from "./business-helpers";

const phase = process.env.MED_RECOVERY_PHASE;
const target = process.env.MED_RECOVERY_TARGET;
const proofPath = resolve("../../artifacts/recovery-scenario.json");
const cookiePath = resolve("../../.cache/recovery-scenario/session.json");
const roles: Record<string, string> = {
  owner: "OWNER",
  purchase: "PURCHASER",
  sales: "SALES",
  warehouse: "WAREHOUSE",
  quality: "QUALITY",
  cashier: "CASHIER",
  finance: "FINANCE",
};
type Actor = { context: BrowserContext; page: Page; session: any };
const execute = (args: string[]) =>
  promisify(execFile)(
    resolve(
      process.platform === "win32"
        ? "../../backend/.venv/Scripts/python.exe"
        : "../../backend/.venv/bin/python",
    ),
    args,
    {
      cwd: resolve("../.."),
      env: { ...process.env, PYTHONUTF8: "1" },
      timeout: 30000,
    },
  );
const sum = (items: any[], field: string) =>
  items.reduce((n: bigint, r: any) => {
    const value = r[field] || "0",
      negative = value.startsWith("-");
    const [whole, fraction = ""] = value.replace(/^-/, "").split(".");
    return n + (negative ? -1n : 1n) * BigInt(whole + fraction.padEnd(8, "0"));
  }, 0n);

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

test("backup drill prepares, resumes or checks original unfinished documents", async ({
  browser,
}) => {
  test.skip(
    !["prepare", "resume", "source"].includes(phase || ""),
    "Explicit maintenance drill only; never switches environments in normal test runs",
  );
  test.setTimeout(180000);
  if (phase === "resume") {
    expect(target).toMatch(/^medinv-restore-[a-f0-9]{12}$/);
    await execute([
      "scripts/recovery.py",
      "assert-target",
      "--target",
      target!,
    ]);
  } else {
    // Source-only stages must not accidentally write to a restored gateway.
    await execute([
      "-c",
      "import sys;sys.path.insert(0,'scripts');import recovery;assert not recovery.JOURNAL.exists();s=recovery.source();assert s['web']['State']['Running']",
    ]);
  }
  let proof: any;
  if (phase === "prepare") {
    const run = randomUUID(),
      suffix = run.slice(0, 8);
    const ids = JSON.parse(
      (
        await execute([
          "-c",
          "import json;from uuid import uuid5,NAMESPACE_URL;from scripts.dev_accounts import ACCOUNTS;print(json.dumps({n:str(uuid5(NAMESPACE_URL,'medinv-dev/'+n)) for n,_ in ACCOUNTS}))",
        ])
      ).stdout,
    );
    const manifest = {
      request_id: run,
      tenant_name: "恢复验收企业-" + suffix,
      entity_name: "恢复验收主体",
      credit_code: "DEMO-" + suffix,
      warehouse_code: "MAIN",
      warehouse_name: "恢复验收仓",
      location_code: "A1",
      reason: "备份与未完成原单续办验收",
      members: Object.entries(roles).map(([name, role]) => ({
        principal_id: ids["demo." + name],
        role_codes: [role],
      })),
    };
    const manifestPath = resolve("../../artifacts/recovery-enterprise.json");
    await writeFile(manifestPath, JSON.stringify(manifest, null, 2));
    const opened = JSON.parse(
      (await execute(["scripts/provision_enterprise.py", manifestPath])).stdout,
    );
    proof = { run, tenant_name: manifest.tenant_name, opened };
  } else proof = JSON.parse(await readFile(proofPath, "utf8"));

  if (phase === "resume") {
    const saved = await browser.newContext({ storageState: cookiePath });
    try {
      const response = await saved.request.get(origin + "/auth/session");
      expect(
        response.status(),
        "Restored server session and original browser cookie",
      ).toBe(200);
      const session = (await response.json()).data;
      expect(session.tenant_id).toBe(proof.opened.tenant_id);
      const refreshed = await saved.request.post(origin + "/auth/refresh", {
        headers: headers(session),
      });
      expect(
        refreshed.status(),
        "Restored encrypted refresh token is usable with original keys and identity DB",
      ).toBe(200);
    } finally {
      await saved.close();
    }
  }
  const team: Record<string, Actor> = {},
    errors: string[] = [];
  try {
    for (const [role, code] of Object.entries(roles)) {
      const context = await browser.newContext(),
        page = await context.newPage();
      page.on("pageerror", (e) => errors.push(e.message));
      team[role] = { context, page, session: null };
      const session = await login(context, "demo." + role);
      const switched = await context.request.post(origin + "/auth/tenant", {
        headers: headers(session),
        data: { tenant_id: proof.opened.tenant_id },
      });
      expect(switched.status()).toBe(200);
      team[role]!.session = (await switched.json()).data;
      expect(team[role]!.session.role_codes).toEqual([code]);
      if (phase !== "prepare")
        expect(team[role]!.session.permissions.slice().sort()).toEqual(
          proof.permissions[role],
        );
    }
    const owner = team.owner!,
      quality = team.quality!,
      warehouse = team.warehouse!,
      cashier = team.cashier!,
      sales = team.sales!,
      finance = team.finance!,
      purchase = team.purchase!;
    const send = (a: Actor, path: string, body: unknown) =>
      command(a.context, a.session, path, body);
    const get = (path: string) => read(owner.context, path);
    if (phase === "prepare") {
      const f = await stockedCustomer(
        owner.context,
        owner.session,
        quality.context,
        quality.session,
        {
          warehouse: proof.opened.warehouse_id,
          location: proof.opened.location_id,
        },
      );
      proof.f = f;
      proof.permissions = Object.fromEntries(
        Object.entries(team).map(([role, a]) => [
          role,
          a.session.permissions.slice().sort(),
        ]),
      );
      proof.account = await send(owner, "/api/v1/settlements/accounts", {
        entity_id: f.entity,
        code: "RECOVERY-CASH",
        name: "恢复验收现金",
        method: "CASH",
      });
      await send(
        owner,
        `/api/v1/customer-credit/${f.party.id}?entity_id=${f.entity}`,
        {
          expected_version: "0",
          credit_limit: "1000",
          valid_until: `${f.year + 1}-12-31`,
          reason: "恢复演练额度",
        },
      );
      proof.purchase = await send(purchase, "/api/v1/purchase-orders", {
        entity_id: f.entity,
        supplier_id: f.party.id,
        warehouse_id: f.warehouse,
        lines: [
          {
            sku_id: f.product.sku_id,
            qty: "5",
            unit_price: "13",
            tax_rate: "0.13",
          },
        ],
      });
      await send(
        quality,
        `/api/v1/purchase-orders/${proof.purchase.id}/approve`,
        { expected_version: "1", reason: "独立审核" },
      );
      let detail = await get(`/api/v1/purchase-orders/${proof.purchase.id}`);
      const receipt = await send(
        warehouse,
        `/api/v1/purchase-orders/${proof.purchase.id}/receipts`,
        {
          expected_version: detail.order.version,
          shipment_ref: "RESTORE-DELIVERY",
          transport_note: "核对随货实物",
          lines: [
            {
              order_line_id: detail.lines[0].id,
              location_id: proof.opened.location_id,
              qty: "5",
              lot_no: f.lot,
              mfg_date: `${f.year}-01-01`,
              expiry_date: `${f.year + 1}-12-31`,
            },
          ],
        },
      );
      detail = await get(`/api/v1/purchase-orders/${proof.purchase.id}`);
      proof.inspection = await send(
        quality,
        `/api/v1/receipts/${receipt.id}/inspections`,
        {
          expected_version: "1",
          note: "验收合格后留待恢复入库",
          lines: [
            {
              receipt_line_id: detail.receipts[0].lines[0].id,
              qualified_qty: "5",
              rejected_qty: "0",
            },
          ],
        },
      );
      proof.sale = await send(sales, "/api/v1/sales-orders", {
        entity_id: f.entity,
        customer_id: f.party.id,
        warehouse_id: f.warehouse,
        delivery_address: "虚拟恢复验收交付点",
        lines: [
          {
            sku_id: f.product.sku_id,
            qty: "4",
            unit_price: "20",
            tax_rate: "0.13",
          },
        ],
      });
      await send(quality, `/api/v1/sales-orders/${proof.sale.id}/approve`, {
        expected_version: "1",
        reason: "独立审核授信及价格",
      });
      detail = await get(`/api/v1/sales-orders/${proof.sale.id}`);
      proof.shipment = await send(
        sales,
        `/api/v1/sales-orders/${proof.sale.id}/shipments`,
        {
          expected_version: detail.order.version,
          client_operation_id: randomUUID(),
          lines: [{ order_line_id: detail.lines[0].id, qty: "4" }],
        },
      );
      detail = await get(`/api/v1/sales-orders/${proof.sale.id}`);
      await send(warehouse, `/api/v1/shipments/${proof.shipment.id}/pick`, {
        expected_version: "1",
        note: "已拣货留待恢复后独立复核",
        lines: detail.shipments[0].lines.map((r: any) => ({
          shipment_line_id: r.id,
          qty: r.qty,
          lot_no: r.lot_no,
        })),
      });
      proof.retail = await send(cashier, "/api/v1/retail/sales", {
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
      proof.cashRequest = {
        client_operation_id: randomUUID(),
        expected_version: "1",
        account_id: proof.account.id,
        tendered: "40",
        confirmed: true,
        reason: "实收40元后留待恢复交付",
      };
      proof.cashKey = randomUUID();
      const cash = await cashier.context.request.post(
        origin + `/api/v1/retail/sales/${proof.retail.id}/cash`,
        {
          headers: {
            ...headers(cashier.session),
            "Idempotency-Key": proof.cashKey,
          },
          data: proof.cashRequest,
        },
      );
      expect(cash.status()).toBe(200);
      proof.cashResult = (await cash.json()).data;
    }
    const f = proof.f;
    const baseline = {
      purchase: await get(`/api/v1/purchase-orders/${proof.purchase.id}`),
      wholesale: await get(`/api/v1/sales-orders/${proof.sale.id}`),
      retail: await get(`/api/v1/retail/sales/${proof.retail.id}`),
    };
    if (phase === "prepare") {
      proof.baseline = baseline;
      await mkdir(resolve("../../.cache/recovery-scenario"), {
        recursive: true,
      });
      await owner.context.storageState({ path: cookiePath });
      await chmod(cookiePath, 0o600);
      await writeFile(proofPath, JSON.stringify(proof, null, 2) + "\n");
    } else expect(baseline).toEqual(proof.baseline);
    const availability = () =>
      get(
        `/api/v1/inventory/availability?entity_id=${f.entity}&warehouse_id=${f.warehouse}&sku_id=${f.product.sku_id}`,
      );
    const stock = (await availability()).items;
    expect(sum(stock, "on_hand")).toBe(1000000000n);
    expect(sum(stock, "reserved")).toBe(600000000n);
    expect(baseline.retail.payments).toHaveLength(1);
    if (phase !== "resume") {
      await writeFile(
        resolve(`../../artifacts/recovery-${phase}-check.json`),
        JSON.stringify(
          {
            tenant_id: proof.opened.tenant_id,
            unchanged_documents: true,
            stock: "10",
            reserved: "6",
            retail_payments: 1,
            role_permissions_match: true,
          },
          null,
          2,
        ),
      );
      return;
    }

    const outsider = await browser.newContext();
    try {
      const v = await login(outsider, "demo.viewer");
      const forbidden = await outsider.request.post(origin + "/auth/tenant", {
        headers: headers(v),
        data: { tenant_id: proof.opened.tenant_id },
      });
      expect(forbidden.status()).toBe(403);
    } finally {
      await outsider.close();
    }
    const cashAgain = await cashier.context.request.post(
      origin + `/api/v1/retail/sales/${proof.retail.id}/cash`,
      {
        headers: {
          ...headers(cashier.session),
          "Idempotency-Key": proof.cashKey,
        },
        data: proof.cashRequest,
      },
    );
    expect(cashAgain.status()).toBe(200);
    expect((await cashAgain.json()).data).toEqual(proof.cashResult);

    await original(quality, proof.purchase.number, "purchase_post");
    const postingPath = `/api/v1/receipt-postings/${proof.inspection.receipt_posting_id}/post`;
    const posted = quality.page.waitForResponse(
      (r) => r.url().endsWith(postingPath) && r.request().method() === "POST",
    );
    await quality.page
      .getByRole("button", { name: "合格入库", exact: true })
      .click();
    await quality.page
      .getByRole("dialog")
      .getByRole("button", { name: "确认入库", exact: true })
      .click();
    const result = await posted;
    expect(result.status()).toBe(200);
    const repeat = await quality.context.request.post(origin + postingPath, {
      headers: {
        ...headers(quality.session),
        "Idempotency-Key": result.request().headers()["idempotency-key"]!,
      },
      data: result.request().postDataJSON(),
    });
    expect(repeat.status()).toBe(200);
    expect((await repeat.json()).data).toEqual((await result.json()).data);

    await original(quality, proof.shipment.number, "sales_verify");
    await quality.page
      .getByRole("button", { name: "出库复核", exact: true })
      .click();
    let dialog = quality.page.getByRole("dialog");
    await dialog.getByLabel("实物批号").fill(f.lot);
    await dialog.getByLabel("实物数量").fill("4");
    await dialog.getByLabel("物流 / 交接凭据").fill("RESTORE-HANDOVER");
    await dialog.getByLabel("核查说明").fill("恢复后按原单独立核查");
    await dialog.getByRole("button", { name: "确认复核", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await quality.page
      .getByRole("button", { name: "销售出库", exact: true })
      .click();
    await quality.page
      .getByRole("dialog")
      .getByRole("button", { name: "确认出库", exact: true })
      .click();
    await expect(quality.page.getByRole("dialog")).not.toBeVisible();

    await original(cashier, proof.retail.number, "retail_complete");
    await expect(
      cashier.page.getByRole("button", { name: "登记现金收款", exact: true }),
    ).toHaveCount(0);
    await cashier.page
      .getByRole("button", { name: "核对并完成出库", exact: true })
      .click();
    dialog = cashier.page.getByRole("dialog");
    await dialog.getByLabel("交付数量 1", { exact: true }).fill("2");
    await dialog.getByLabel("交付批号 1", { exact: true }).fill(f.lot);
    await dialog.getByLabel("操作说明").fill("恢复后交付原已收款零售单");
    await dialog.getByRole("button", { name: "确认保存", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    for (const [kind, amount, direction] of [
      ["AR", "80", "RECEIPT"],
      ["AP", "195", "PAYMENT"],
    ]) {
      const items = (
        await get(
          `/api/v1/settlements/items?entity_id=${f.entity}&kind=${kind}&party_id=${f.party.id}`,
        )
      ).items;
      const cash = await send(finance, "/api/v1/settlements/cash-documents", {
        client_operation_id: randomUUID(),
        entity_id: f.entity,
        party_id: f.party.id,
        account_id: proof.account.id,
        direction,
        amount,
        reference: "RESTORE-" + kind,
        occurred_on: new Intl.DateTimeFormat("en-CA", {
          timeZone: "Asia/Shanghai",
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
        }).format(new Date()),
        note: "恢复环境核对虚拟往来凭据",
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
      expect(
        sum(
          (
            await get(
              `/api/v1/settlements/items?entity_id=${f.entity}&kind=${kind}&party_id=${f.party.id}`,
            )
          ).items,
          "outstanding_amount",
        ),
      ).toBe(0n);
    }
    const final = (await availability()).items;
    expect(sum(final, "on_hand")).toBe(900000000n);
    expect(sum(final, "reserved")).toBe(0n);
    const ledger = (await get(`/api/v1/inventory/ledger?entity_id=${f.entity}`))
      .items;
    expect(ledger).toHaveLength(4);
    expect(sum(ledger, "amount_delta")).toBe(10353600000n);
    expect(
      (await get(`/api/v1/retail/sales/${proof.retail.id}`)).payments,
    ).toHaveLength(1);
    expect(
      (await get(`/api/v1/work-queue?entity_id=${f.entity}`)).items,
    ).toEqual([]);
    await owner.page.goto("/");
    await nav(owner.page, "库存台账");
    await expect(
      owner.page.getByRole("row").filter({ hasText: f.lot }),
    ).toHaveCount(4);
    await owner.page.screenshot({
      path: "../../artifacts/recovery-ledger.png",
      fullPage: true,
    });
    expect(errors).toEqual([]);
    await writeFile(
      resolve("../../artifacts/recovery-resume-check.json"),
      JSON.stringify(
        {
          target,
          tenant_id: proof.opened.tenant_id,
          session_refresh: true,
          role_permissions_match: true,
          outsider_rejected: true,
          existing_payment_replay: true,
          purchase_post_replay: true,
          original_three_documents_completed: true,
          stock: "9",
          cost: "103.536",
          ar: "0",
          ap: "0",
          stock_ledger_entries: 4,
          retail_payments: 1,
        },
        null,
        2,
      ),
    );
  } finally {
    for (const a of Object.values(team)) await a.context.close();
  }
});
