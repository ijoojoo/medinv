import { test, expect, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { wholesale, nav, read, command, chooseParty } from "./business-helpers";
import { entryCsv } from "../src/entryFiles";

// Exact sums for quantity and money; do not hide rounding problems with Number.
function units(raw: string): bigint {
  expect(raw).toMatch(/^-?\d+(\.\d{1,8})?$/);
  const [whole, fraction = ""] = raw.replace(/^-/, "").split(".");
  return (
    BigInt(whole! + fraction.padEnd(8, "0")) * (raw.startsWith("-") ? -1n : 1n)
  );
}
const sum = (items: any[], field: string) =>
  items.reduce((n, row) => n + units(row[field] || "0"), 0n);
// Basic-unit purchase lines have nullable stored amounts. Verify their exact
// calculated gross here, and the actual payable after posting below.
const orderGross = (items: any[]) =>
  items.reduce(
    (n, row) =>
      n +
      (row.gross_amount == null
        ? ((units(row.trade_qty || row.qty) * units(row.unit_price) +
            50000000000000n) /
            100000000000000n) *
          1000000n
        : units(row.gross_amount)),
    0n,
  );
async function submitDialog(
  page: Page,
  title: string,
  button: string,
  fields: Record<string, string> = {},
) {
  const dialog = page.getByRole("dialog", { name: title, exact: true });
  for (const [label, value] of Object.entries(fields))
    await dialog.getByLabel(label, { exact: true }).fill(value);
  await dialog.getByRole("button", { name: button, exact: true }).click();
  await expect(dialog).not.toBeVisible();
}
async function openRecord(page: Page, purchase: boolean, number: string) {
  await page.goto("/");
  await nav(page, purchase ? "采购入库" : "批发销售");
  await page
    .getByRole("row")
    .filter({ hasText: number })
    .getByRole("button", {
      name: purchase ? "查看采购单" : "查看销售单",
      exact: true,
    })
    .click();
}

test("master product and package barcodes complete purchasing, receiving, wholesale and exact inventory reconciliation", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(180000);
  const owner = await wholesale(context, "demo.owner"),
    reviewer = await browser.newContext();
  const quality = await reviewer.newPage(),
    faults: string[] = [];
  for (const p of [page, quality])
    p.on("pageerror", (e) => faults.push(e.message));
  const run = randomUUID().slice(0, 8),
    year = new Date().getFullYear();
  const name = `主线联合验收器械-${run}`,
    barcode = `00-${run}-BASE`,
    boxBarcode = `00-${run}-BOX`;
  const checkpoints: any[] = [];
  try {
    const checker = await wholesale(reviewer, "demo.quality"),
      entity = owner.entities[0].id;
    const schema = await read(context, "/api/v1/catalog/master/schema");
    const category = schema.payload.nodes.find(
      (n: any) => n.node_code === "device",
    ).node_id;
    await page.goto("/");
    await nav(page, "商品档案");
    await page
      .getByRole("button", { name: "新建商品 / 项目", exact: true })
      .click();
    const choice = page.getByRole("dialog", {
      name: "明确选择建档品类",
      exact: true,
    });
    await choice.getByLabel("新建商品品类").selectOption(category);
    await choice.getByRole("button", { name: "进入建档", exact: true }).click();
    await page.locator('[data-field="name"] input').fill(name);
    await page
      .locator('[data-field="specification"] input')
      .fill("虚拟验收规格，1箱6盒");
    await page.locator('[data-field="base_unit"] input').fill("盒");
    await page
      .locator('[data-field="regulatory_subtype"] select')
      .selectOption("MEDICAL_DEVICE");
    await page.locator('[data-field="device_class"] select').selectOption("I");
    await page
      .locator('[data-field="regulatory_subject"] input')
      .fill("联合验收虚拟备案人");
    await page
      .locator('[data-field="manufacturer"] input')
      .fill("联合验收虚拟生产企业");
    await page
      .locator('[data-field="product_certificates"] input')
      .fill(`TEST-FILING-${run}`);
    await page.getByLabel("条码（基本单位）", { exact: true }).fill(barcode);
    await page
      .getByRole("navigation", { name: "商品资料分组" })
      .getByRole("button", { name: "价格信息", exact: true })
      .click();
    await page.getByLabel("参考采购价", { exact: true }).fill("11.30");
    await page.getByLabel("参考批发价", { exact: true }).fill("13.56");
    const savingMaster = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/v1/catalog/master/products") &&
        r.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "保存资料草稿", exact: true })
      .click();
    const savedMaster = await savingMaster;
    expect(savedMaster.status(), await savedMaster.text()).toBe(200);
    const product = (await savedMaster.json()).data;
    let master = await read(
      context,
      `/api/v1/catalog/master/products/${product.id}`,
    );
    const sku = master.skus[0].id;
    expect(master.values.barcodes[0].external_code).toBe(barcode);

    // Complete and independently verify the evidence created from direct input.
    // These are virtual test records; configuration and qualification rules are unchanged.
    const certPath = `/api/v1/catalog/products/${product.id}/certificates`;
    const certificate = (await read(context, certPath)).items[0];
    const revised = await command(
      context,
      owner,
      `${certPath}/${certificate.id}/revise`,
      {
        expected_version: certificate.version,
        certificate_type: "DEVICE_FILING",
        number: `TEST-FILING-${run}`,
        holder_name: "联合验收虚拟备案人",
        issuer: "软件测试机关",
        validity_mode: "FIXED",
        valid_from: `${year}-01-01`,
        valid_until: `${year + 2}-12-31`,
        evidence_ref: "DEMO-ONLY",
        reason: "补齐虚构证件依据",
      },
    );
    const verified = await command(
      reviewer,
      checker,
      `${certPath}/${certificate.id}/review`,
      {
        expected_version: revised.version,
        decision: "VERIFY",
        evidence_ref: "DEMO-ONLY",
        reason: "独立核对虚拟证件",
      },
    );
    const record = (
      await read(context, `/api/v1/catalog/products/${product.id}`)
    ).record;
    const linked = await command(
      context,
      owner,
      "/api/v1/catalog/master/products",
      {
        product_id: product.id,
        category_node_id: category,
        form_revision: schema.revision,
        expected_version: Number(record.version),
        values: {
          product_certificates: [
            {
              certificate_id: certificate.id,
              certificate_version: Number(verified.version),
              business_use: true,
            },
          ],
        },
        reason: "选定已核验经营证件",
      },
    );
    await command(
      reviewer,
      checker,
      `/api/v1/catalog/products/${product.id}/review`,
      {
        expected_version: String(linked.version),
        decision: "APPROVE",
        evidence_ref: "DEMO-ONLY",
        reason: "独立核对器械建档资料",
      },
    );
    const packPath = `/api/v1/catalog/skus/${sku}/packaging`;
    const pack = await command(context, owner, packPath, {
      expected_sku_version: (await read(context, packPath)).sku_version,
      unit_name: "箱",
      numerator: "6",
      denominator: "1",
      reason: "联合验收一箱六盒",
    });
    await command(reviewer, checker, `${packPath}/${pack.id}/review`, {
      expected_version: pack.version,
      decision: "APPROVE",
      evidence_ref: "DEMO-ONLY",
      reason: "独立核对包装",
    });
    await command(context, owner, `/api/v1/catalog/skus/${sku}/codes`, {
      scheme: "BARCODE",
      external_code: boxBarcode,
      packaging_id: pack.id,
      evidence_ref: "DEMO-ONLY",
      reason: "虚构包装条码",
    });
    const party = await command(context, owner, "/api/v1/catalog/parties", {
      entity_id: entity,
      code: `FLOW-${run}`,
      name: `联合验收往来单位-${run}`,
      credit_code: `DEMO-${run}`,
      kind: "BOTH",
      license_no: "DEMO-ONLY",
      valid_from: `${year}-01-01`,
      valid_until: `${year + 2}-12-31`,
      categories: ["DEVICE"],
    });
    await command(
      reviewer,
      checker,
      `/api/v1/catalog/parties/${party.id}/review`,
      {
        expected_version: "1",
        decision: "APPROVE",
        evidence_ref: "DEMO-ONLY",
        reason: "核对虚构往来单位",
      },
    );
    await command(
      context,
      owner,
      `/api/v1/customer-credit/${party.id}?entity_id=${entity}`,
      {
        expected_version: "0",
        credit_limit: "1000",
        valid_until: `${year + 1}-12-31`,
        reason: "虚拟联合验收额度",
      },
    );
    const options = await read(
      context,
      `/api/v1/purchasing/options?entity_id=${entity}`,
    );
    const warehouse = options.warehouses[0],
      location = options.locations.find(
        (l: any) => l.warehouse_id === warehouse.id,
      );
    const stock = async () =>
      (
        await read(
          context,
          `/api/v1/inventory/availability?entity_id=${entity}&warehouse_id=${warehouse.id}&sku_id=${sku}`,
        )
      ).items;
    async function checkStock(stage: string, onHand: string, reserved: string) {
      const rows = await stock();
      expect(sum(rows, "on_hand"), stage).toBe(units(onHand));
      expect(sum(rows, "reserved"), stage).toBe(units(reserved));
      checkpoints.push({ stage, on_hand: onHand, reserved, base_unit: "盒" });
    }
    await checkStock("建档完成", "0", "0");

    await nav(page, "采购入库");
    await page
      .getByRole("button", { name: "新建采购订单", exact: true })
      .click();
    let editor = page.getByRole("region", { name: "新建采购单", exact: true });
    await chooseParty(
      editor.getByRole("combobox", { name: "供应商", exact: true }),
      party.id,
    );
    await editor
      .getByRole("combobox", { name: "收货仓库", exact: true })
      .selectOption(warehouse.id);
    await editor.getByRole("button", { name: "导入文件", exact: true }).click();
    let dialog = page.getByRole("dialog", {
      name: "导入商品明细",
      exact: true,
    });
    await dialog.getByLabel("商品明细CSV文件", { exact: true }).setInputFiles({
      name: "采购条码.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(
        entryCsv(
          ["商品条码", "数量", "含税单价", "税率（小数）"],
          [
            [boxBarcode, "2", "67.80", "0.13"],
            [barcode, "3", "11.30", "0.13"],
          ],
        ),
        "utf8",
      ),
    });
    await expect(dialog).toContainText("0 行需修正");
    await expect(dialog).toContainText("折合 12 盒");
    await dialog
      .getByRole("button", { name: "确认追加 2 行", exact: true })
      .click();
    const purchaseSaving = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/v1/purchase-orders") &&
        r.request().method() === "POST",
    );
    await editor
      .getByRole("button", { name: "保存采购单", exact: true })
      .click();
    const purchaseResponse = await purchaseSaving;
    expect(purchaseResponse.status(), await purchaseResponse.text()).toBe(200);
    const purchaseId = (await purchaseResponse.json()).data.id,
      purchasePath = `/api/v1/purchase-orders/${purchaseId}`;
    let purchase = await read(context, purchasePath);
    expect(purchase.lines.map((r: any) => r.qty)).toEqual([
      "12.000000",
      "3.000000",
    ]);
    expect(orderGross(purchase.lines)).toBe(units("169.50"));
    await checkStock("采购已保存未入库", "0", "0");
    await expect(
      page.getByRole("button", { name: "审核采购单", exact: true }),
    ).toBeDisabled();
    await openRecord(quality, true, purchase.order.number);
    await quality
      .getByRole("button", { name: "审核采购单", exact: true })
      .click();
    await submitDialog(quality, "审核采购单", "确认审核", {
      操作说明: "核对2箱和3盒，合计15盒",
    });
    await page.getByRole("button", { name: "刷新单据", exact: true }).click();
    await page.getByRole("button", { name: "登记到货", exact: true }).click();
    dialog = page.getByRole("dialog", { name: "登记到货", exact: true });
    await dialog
      .getByLabel("随货单号", { exact: true })
      .fill(`DELIVERY-${run}`);
    await dialog
      .getByLabel("运输及随货核查", { exact: true })
      .fill("按基本单位逐行清点");
    for (const [i, line] of purchase.lines.entries()) {
      if (i)
        await dialog
          .getByRole("button", { name: "添加到货行", exact: true })
          .click();
      const row = dialog.locator(".line-editor").nth(i);
      await row
        .getByRole("combobox", { name: /^采购商品/ })
        .selectOption(line.id);
      await expect(row.locator(".operation-unit")).toHaveText("盒");
      await expect(row.locator(".receipt-unit-note")).toContainText(
        i ? "本行采购 3 盒" : "本行采购 2 箱，折合 12 盒（1 箱 ＝ 6 盒）",
      );
      await row
        .getByRole("combobox", { name: /^货位/ })
        .selectOption(location.id);
      await row.getByLabel("到货数量", { exact: true }).fill(i ? "3" : "12");
      await row
        .getByLabel("生产批号", { exact: true })
        .fill(`FLOW-${run}-${i ? "B" : "A"}`);
      await row
        .getByRole("textbox", { name: "生产日期", exact: true })
        .fill(`${year}0101`);
      await row
        .getByRole("textbox", { name: "有效期至", exact: true })
        .fill(`${year + 1}${i ? "1231" : "1130"}`);
    }
    await page.screenshot({
      path: "../../outputs/master-stock-flow/到货单位核对.png",
      animations: "disabled",
    });
    await dialog
      .getByRole("button", { name: "保存到货记录", exact: true })
      .click();
    await expect(dialog).not.toBeVisible();
    await checkStock("登记到货未验收", "0", "0");
    await expect(
      page.getByRole("button", { name: "登记验收", exact: true }),
    ).toBeDisabled();
    await quality
      .getByRole("button", { name: "刷新单据", exact: true })
      .click();
    await quality
      .getByRole("button", { name: "登记验收", exact: true })
      .click();
    dialog = quality.getByRole("dialog", { name: "登记验收", exact: true });
    for (const [lot, qty] of [
      [`FLOW-${run}-A`, "12"],
      [`FLOW-${run}-B`, "3"],
    ]) {
      const row = dialog.locator(".line-editor").filter({ hasText: lot });
      await expect(row).toContainText(`待验 ${qty} 盒`);
      await expect(row.locator(".operation-unit")).toHaveText(["盒", "盒"]);
      await row.getByLabel("本次合格数量", { exact: true }).fill(qty!);
    }
    await dialog
      .getByLabel("验收依据与结论", { exact: true })
      .fill("核对两个批号、基本数量与有效期，全部合格");
    await quality.screenshot({
      path: "../../outputs/master-stock-flow/验收单位核对.png",
      animations: "disabled",
    });
    await dialog
      .getByRole("button", { name: "保存验收记录", exact: true })
      .click();
    await expect(dialog).not.toBeVisible();
    await checkStock("验收合格待入库", "0", "0");
    await quality
      .getByRole("button", { name: "合格入库", exact: true })
      .click();
    await submitDialog(quality, "确认合格入库", "确认入库");
    await checkStock("采购合格入库", "15", "0");
    purchase = await read(context, purchasePath);
    const incomingPosting = purchase.receipts[0].postings[0].posting_id;
    expect(
      purchase.lines.every((r: any) => units(r.remaining_qty) === 0n),
    ).toBe(true);
    await quality.screenshot({
      path: "../../outputs/master-stock-flow/采购入库.png",
      fullPage: true,
      animations: "disabled",
    });

    await nav(page, "批发销售");
    await page.getByRole("button", { name: "新建销售单", exact: true }).click();
    editor = page.getByRole("region", { name: "新建销售单", exact: true });
    await chooseParty(
      editor.getByRole("combobox", { name: "客户", exact: true }),
      party.id,
    );
    await editor
      .getByRole("combobox", { name: "发货仓库", exact: true })
      .selectOption(warehouse.id);
    await editor
      .getByLabel("收货地址", { exact: true })
      .fill("虚拟联合验收收货点");
    await editor.getByRole("button", { name: "粘贴多行", exact: true }).click();
    dialog = page.getByRole("dialog", { name: "粘贴多行商品", exact: true });
    await dialog
      .getByRole("radio", { name: "商品条码（自动匹配包装）", exact: true })
      .check();
    await dialog
      .getByLabel("多行商品数据", { exact: true })
      .fill(`${barcode}\t2\t13.56\t0.13\n${boxBarcode}\t1\t81.36\t0.13`);
    await dialog
      .getByRole("button", { name: "校验并预览", exact: true })
      .click();
    await expect(dialog).toContainText("折合 6 盒");
    await dialog
      .getByRole("button", { name: "确认填入 2 行", exact: true })
      .click();
    const saleSaving = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/v1/sales-orders") &&
        r.request().method() === "POST",
    );
    await editor
      .getByRole("button", { name: "保存销售单", exact: true })
      .click();
    const saleResponse = await saleSaving;
    expect(saleResponse.status(), await saleResponse.text()).toBe(200);
    const saleId = (await saleResponse.json()).data.id,
      salePath = `/api/v1/sales-orders/${saleId}`;
    let sale = await read(context, salePath);
    expect(sale.lines.map((r: any) => r.qty)).toEqual(["2.000000", "6.000000"]);
    expect(orderGross(sale.lines)).toBe(units("108.48"));
    await openRecord(quality, false, sale.order.number);
    await quality
      .getByRole("button", { name: "审核销售单", exact: true })
      .click();
    await submitDialog(quality, "审核销售单", "确认审核", {
      操作说明: "按2盒加1箱发货，核对批发价",
    });
    await page
      .getByRole("button", { name: "刷新销售详情", exact: true })
      .click();
    await page
      .getByRole("button", { name: "分配发货批次", exact: true })
      .click();
    dialog = page.getByRole("dialog");
    await expect(dialog.locator(".operation-unit")).toHaveText(["盒", "盒"]);
    for (const [i, qty] of ["2", "6"].entries())
      await dialog.getByLabel("本次发货数量", { exact: true }).nth(i).fill(qty);
    await dialog.getByRole("button", { name: "确认分配", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await checkStock("发货批次已分配", "15", "8");
    sale = await read(context, salePath);
    const shipment = sale.shipments[0];
    expect(shipment.lines.every((l: any) => l.lot_no === `FLOW-${run}-A`)).toBe(
      true,
    );
    await page.getByRole("button", { name: "登记拣货", exact: true }).click();
    dialog = page.getByRole("dialog");
    await expect(dialog.locator(".operation-unit")).toHaveText(["盒", "盒"]);
    for (const [i, line] of shipment.lines.entries()) {
      await dialog
        .getByLabel("实物批号", { exact: true })
        .nth(i)
        .fill(line.lot_no);
      await dialog
        .getByLabel("实物数量", { exact: true })
        .nth(i)
        .fill(line.qty);
    }
    await dialog.getByLabel("核查说明", { exact: true }).fill("按盒清点8盒");
    await dialog.getByRole("button", { name: "保存拣货", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await expect(
      page.getByRole("button", { name: "出库复核", exact: true }),
    ).toBeDisabled();
    await quality
      .getByRole("button", { name: "刷新销售详情", exact: true })
      .click();
    await quality
      .getByRole("button", { name: "出库复核", exact: true })
      .click();
    dialog = quality.getByRole("dialog");
    for (const [i, line] of shipment.lines.entries()) {
      await dialog
        .getByLabel("实物批号", { exact: true })
        .nth(i)
        .fill(line.lot_no);
      await dialog
        .getByLabel("实物数量", { exact: true })
        .nth(i)
        .fill(line.qty);
    }
    await dialog
      .getByLabel("核查说明", { exact: true })
      .fill("独立复核近效期批次8盒");
    await dialog
      .getByLabel("物流 / 交接凭据", { exact: true })
      .fill(`SHIP-${run}`);
    await dialog.getByRole("button", { name: "确认复核", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await quality
      .getByRole("button", { name: "销售出库", exact: true })
      .click();
    dialog = quality.getByRole("dialog");
    await dialog.getByRole("button", { name: "确认出库", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await checkStock("销售出库完成", "7", "0");
    sale = await read(context, salePath);
    expect(sale.shipments[0].state).toBe("POSTED");
    expect(sale.lines.every((r: any) => units(r.remaining_qty) === 0n)).toBe(
      true,
    );
    const outgoingPosting = sale.shipments[0].posting_id;
    const balances = await stock();
    const lotId = (suffix: string) =>
      purchase.receipts[0].lines.find(
        (r: any) => r.lot_no === `FLOW-${run}-${suffix}`,
      ).lot_id;
    expect(
      sum(
        balances.filter((r: any) => r.lot_id === lotId("A")),
        "on_hand",
      ),
    ).toBe(units("4"));
    expect(
      sum(
        balances.filter((r: any) => r.lot_id === lotId("B")),
        "on_hand",
      ),
    ).toBe(units("3"));
    const ledger = (
      await read(
        context,
        `/api/v1/inventory/ledger?entity_id=${entity}&search=${encodeURIComponent(name)}`,
      )
    ).items;
    expect(ledger).toHaveLength(4);
    expect(new Set(ledger.map((r: any) => r.posting_id))).toEqual(
      new Set([incomingPosting, outgoingPosting]),
    );
    expect(sum(ledger, "qty_delta")).toBe(units("7"));
    expect(sum(ledger, "amount_delta")).toBe(units("70"));
    for (const [kind, amount, posting] of [
      ["AP", "169.50", incomingPosting],
      ["AR", "108.48", outgoingPosting],
    ]) {
      const items = (
        await read(
          context,
          `/api/v1/settlements/items?entity_id=${entity}&kind=${kind}&party_id=${party.id}`,
        )
      ).items;
      expect(items).toHaveLength(1);
      expect(items[0].posting_id).toBe(posting);
      expect(sum(items, "outstanding_amount")).toBe(units(amount));
    }
    await nav(quality, "库存台账");
    const search = quality.getByRole("form", { name: "单据查询" });
    await search
      .locator(
        'input[type="search"], input[placeholder="商品、编码、批号、仓库或过账编号"]',
      )
      .fill(name);
    await search.getByRole("button", { name: "查询单据", exact: true }).click();
    await expect(
      quality.getByRole("row").filter({ hasText: name }),
    ).toHaveCount(4);
    await quality.screenshot({
      path: "../../outputs/master-stock-flow/库存流水核对.png",
      fullPage: true,
      animations: "disabled",
    });
    expect(faults).toEqual([]);
    await mkdir("../../docs/development", { recursive: true });
    await writeFile(
      "../../docs/development/主数据条码进销存联合验收记录.json",
      JSON.stringify(
        {
          completed_at: new Date().toISOString(),
          run,
          scenario: "虚拟器械主数据至采购入库、条码销售出库",
          entity_id: entity,
          warehouse_id: warehouse.id,
          product_id: product.id,
          sku_id: sku,
          product_name: name,
          packaging_id: pack.id,
          package: "1箱=6盒",
          party_id: party.id,
          purchase: {
            id: purchaseId,
            number: purchase.order.number,
            posting_id: incomingPosting,
            quantity: "15",
            gross: "169.50",
          },
          sale: {
            id: saleId,
            number: sale.order.number,
            posting_id: outgoingPosting,
            quantity: "8",
            gross: "108.48",
          },
          checkpoints,
          reconciliation: {
            on_hand: "7",
            reserved: "0",
            inventory_cost: "70.00",
            payable: "169.50",
            receivable: "108.48",
            ledger_rows: ledger.length,
          },
          scope:
            "建档、开单及收发货通过页面；资质、包装、往来单位准备与结果核对使用正常业务接口。未变更零售界面或创建企业、仓库。",
        },
        null,
        2,
      ) + "\n",
    );
  } finally {
    await reviewer.close();
  }
});
