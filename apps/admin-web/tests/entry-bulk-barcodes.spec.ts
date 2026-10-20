import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";
import {
  entryCsv,
  entryErrorCsv,
  parseEntryFile,
  prepareEntryFileRow,
  validateEntryFileRow,
} from "../src/entryFiles";
import type { EntryProduct, EntryPackages } from "../src/documentEntry";
import {
  wholesale,
  stockedCustomer,
  command,
  read,
  nav,
  chooseParty,
} from "./business-helpers";

const packId = "00000000-0000-0000-0000-000000000002";
const product: EntryProduct = {
  id: "00000000-0000-0000-0000-000000000001",
  code: "00123",
  name: "虚构商品",
  base_unit: "个",
  qty_scale: 0,
  specification: "测试",
  manufacturer_name: "测试厂家",
  eligible: true,
};
const packages: EntryPackages = {
  base_unit: "个",
  items: [{ id: packId, unit_name: "箱", numerator: "3", denominator: "1" }],
};
const boxed: EntryProduct = {
  ...product,
  barcode_match: {
    barcode: "00123-AbC",
    packaging_id: packId,
    unit_name: "箱",
    numerator: "3",
    denominator: "1",
    available: true,
  },
};
const base: EntryProduct = {
  ...product,
  barcode_match: {
    barcode: "00001",
    packaging_id: null,
    unit_name: "个",
    numerator: "1",
    denominator: "1",
    available: true,
  },
};
const csv = (lines: string[][]) =>
  entryCsv(["商品条码", "数量", "含税单价", "交易单位", "税率（小数）"], lines);

test("bulk barcode templates preserve identifiers and old code columns never change meaning", () => {
  const old = parseEntryFile("SKU编码,数量,含税单价\n00123,2,6.123456")[0]!;
  validateEntryFileRow(old, product);
  expect(old.matchBy).toBe("code");
  expect(old.line.packaging_id).toBe("");
  expect(old.baseQuantity).toBe("2 个");
  const row = parseEntryFile(
    csv([[boxed.barcode_match!.barcode, "2", "6.123456", "", "0.06"]]),
  )[0]!;
  validateEntryFileRow(row, boxed, packages);
  expect(row.errors).toEqual([]);
  expect(row.line).toMatchObject({
    packaging_id: packId,
    qty: "2",
    unit_price: "6.123456",
    tax_rate: "0.06",
  });
  expect(row.conversion).toBe("1 箱 ＝ 3 个");
  expect(row.baseQuantity).toBe("6 个");
  expect(row.unit).toBe(""); // Inferred units never become input constraints on a retry.
  const restored = parseEntryFile(entryErrorCsv([old, row]));
  expect(restored.map((r) => [r.matchBy, r.code, r.unit, r.packageId])).toEqual(
    [
      ["code", "00123", "", ""],
      ["barcode", "00123-AbC", "", ""],
    ],
  );
  const max = "00" + "a".repeat(126);
  expect(parseEntryFile(csv([[max, "1", "2", "", ""]]))[0]!.code).toBe(max);
  expect(
    parseEntryFile(csv([[max + "a", "1", "2", "", ""]]))[0]!.errors.join(),
  ).toContain("128");
  const ambiguous = parseEntryFile(
    "SKU编码,商品条码,数量,含税单价\n00123,00001,1,2",
  )[0]!;
  expect(ambiguous.errors.join()).toContain("只能填写");
});

test("barcode import rejects fallback guesses, inactive packs, conflicting units and precision loss", () => {
  for (const [candidate, unit, qty, expected] of [
    [product, "", "1", "未唯一匹配"],
    [
      {
        ...boxed,
        barcode_match: { ...boxed.barcode_match!, barcode: "00123-abc" },
      },
      "",
      "1",
      "未唯一匹配",
    ],
    [
      {
        ...boxed,
        eligible: false,
        barcode_match: { ...boxed.barcode_match!, available: false },
      },
      "",
      "1",
      "已不可用",
    ],
    [boxed, "个", "1", "不一致"],
    [boxed, "", "0.5", "基本数量"],
    [{ ...boxed, eligible: false }, "", "1", "当前不可选"],
  ] as const) {
    const row = parseEntryFile(
      csv([["00123-AbC", qty, "9.123456", unit, "0.13"]]),
    )[0]!;
    validateEntryFileRow(row, candidate, packages);
    expect(row.errors.join()).toContain(expected);
    expect(row.line.qty).toBe(qty);
    expect(row.line.unit_price).toBe("9.123456");
  }
  const missing = parseEntryFile(csv([["00123-AbC", "1", "2", "", ""]]))[0]!;
  validateEntryFileRow(missing, boxed, { base_unit: "个", items: [] });
  expect(missing.errors.join()).toContain("包装已变更或不可用");
  const contradictory = parseEntryFile(
    entryCsv(
      ["商品条码", "数量", "含税单价", "包装标识"],
      [["00001", "1", "2", packId]],
    ),
  )[0]!;
  validateEntryFileRow(contradictory, base, packages);
  expect(contradictory.errors.join()).toContain("不一致");
});

test("editing and rechecking discards stale package matches, but preserves values and source errors", () => {
  const row = parseEntryFile(
    csv([["00123-AbC", "0.5", "9.123456", "", "0.06"]]),
  )[0]!;
  validateEntryFileRow(row, boxed, packages);
  expect(row.errors).not.toHaveLength(0);
  row.line.qty = "2";
  prepareEntryFileRow(row);
  validateEntryFileRow(row, boxed, packages);
  expect(row.errors).toEqual([]);
  expect(row.line.packaging_id).toBe(packId);
  row.code = "00001";
  prepareEntryFileRow(row);
  expect(row.line.sku_id).toBe("");
  expect(row.line.product).toBeUndefined();
  validateEntryFileRow(row, base);
  expect(row.errors).toEqual([]);
  expect(row.line.packaging_id).toBe("");
  expect(row.baseQuantity).toBe("2 个");
  expect(row.line.unit_price).toBe("9.123456");
  const malformed = parseEntryFile("商品条码,数量,含税单价\n00001,2")[0]!;
  malformed.line.unit_price = "1";
  prepareEntryFileRow(malformed);
  validateEntryFileRow(malformed, base);
  expect(malformed.errors).toContain("列数与表头不一致");
});

for (const purchase of [true, false]) {
  test(`${purchase ? "purchase" : "sales"} bulk barcode preview, inline repair and committed package units`, async ({
    page,
    context,
    browser,
  }) => {
    test.setTimeout(150000);
    const owner = await wholesale(context, "demo.owner"),
      reviewer = await browser.newContext();
    const faults: string[] = [];
    page.on("pageerror", (e) => faults.push(e.message));
    try {
      const checker = await wholesale(reviewer, "demo.quality"),
        f = await stockedCustomer(context, owner, reviewer, checker);
      const channel = purchase ? "purchase" : "sale",
        path = `/api/v1/catalog/skus/${f.product.sku_id}`;
      const product = (
        await read(
          context,
          `/api/v1/catalog/entry-products?entity_id=${f.entity}&channel=${channel}&sku_id=${f.product.sku_id}`,
        )
      ).items[0];
      const pack = await command(context, owner, `${path}/packaging`, {
        expected_sku_version: (await read(context, `${path}/packaging`))
          .sku_version,
        unit_name: "箱",
        numerator: "3",
        denominator: "1",
        reason: "多行条码导入验收",
      });
      await command(reviewer, checker, `${path}/packaging/${pack.id}/review`, {
        expected_version: pack.version,
        decision: "APPROVE",
        evidence_ref: "DEMO-ONLY",
        reason: "独立核对包装",
      });
      // A barcode equal to a real SKU code must never change old CSV / paste semantics.
      const boxBarcode = product.code,
        baseBarcode = `00-${randomUUID()}-AbC`;
      for (const [external_code, packaging_id] of [
        [boxBarcode, pack.id],
        [baseBarcode, null],
      ])
        await command(context, owner, `${path}/codes`, {
          scheme: "BARCODE",
          external_code,
          packaging_id,
          evidence_ref: "DEMO-ONLY",
          reason: "批量条码验收",
        });
      await page.goto("/");
      await nav(page, purchase ? "采购入库" : "批发销售");
      const title = purchase ? "新建采购单" : "新建销售单";
      await page.getByRole("button", { name: title === "新建采购单" ? "新建采购订单" : title, exact: true }).click();
      const editor = page.getByRole("region", { name: title, exact: true }),
        lines = editor.locator(".entry-row");
      await editor
        .getByRole("button", { name: "粘贴多行", exact: true })
        .click();
      const paste = page.getByRole("dialog", {
        name: "粘贴多行商品",
        exact: true,
      });
      await paste
        .getByLabel("多行商品数据", { exact: true })
        .fill(`${boxBarcode}\t2\t6.123456`);
      await paste
        .getByRole("button", { name: "校验并预览", exact: true })
        .click();
      await expect(paste).toContainText(`折合 2 ${product.base_unit}`);
      await paste
        .getByRole("radio", { name: "商品条码（自动匹配包装）", exact: true })
        .check();
      await expect(
        paste.getByLabel("粘贴导入预览", { exact: true }),
      ).toHaveCount(0);
      await paste
        .getByLabel("多行商品数据", { exact: true })
        .fill(
          `${boxBarcode}\t0.5\t6.123456\t0.06\n${baseBarcode}\t3\t1.123456\t0.13`,
        );
      await paste
        .getByRole("button", { name: "校验并预览", exact: true })
        .click();
      await expect(paste).toContainText("基本数量");
      await expect(
        paste.getByRole("button", { name: "确认填入 2 行", exact: true }),
      ).toBeDisabled();
      await paste.getByLabel("第1行数量", { exact: true }).fill("2");
      await expect(paste).toContainText("已修改，待重新校验");
      await expect(
        paste.getByRole("button", { name: "确认填入 2 行", exact: true }),
      ).toBeDisabled();
      await paste
        .getByRole("button", { name: "重新校验", exact: true })
        .click();
      await expect(paste).toContainText(`1 箱 ＝ 3 ${product.base_unit}`);
      await expect(paste).toContainText(`折合 6 ${product.base_unit}`);
      await expect(
        paste.getByLabel("第1行含税单价", { exact: true }),
      ).toHaveValue("6.123456");
      await page.setViewportSize({ width: 1280, height: 720 });
      await expect
        .poll(async () => {
          const rect = await paste
            .getByRole("button", { name: "确认填入 2 行", exact: true })
            .boundingBox();
          return !!rect && rect.y >= 0 && rect.y + rect.height < 720;
        })
        .toBe(true);
      await page.screenshot({
        path: `../../outputs/entry-bulk/${channel}-paste.png`,
        animations: "disabled",
      });
      await paste
        .getByRole("button", { name: "确认填入 2 行", exact: true })
        .click();
      await expect(lines).toHaveCount(2);
      await expect(
        lines
          .first()
          .getByRole("combobox", { name: "交易单位 1", exact: true }),
      ).toHaveValue(pack.id);

      await editor
        .getByRole("button", { name: "导入文件", exact: true })
        .click();
      const file = page.getByRole("dialog", {
        name: "导入商品明细",
        exact: true,
      });
      await file.getByLabel("商品明细CSV文件", { exact: true }).setInputFiles({
        name: "条码.csv",
        mimeType: "text/csv",
        buffer: Buffer.from(
          csv([
            ["UNKNOWN-BARCODE", "1", "8.123456", "", "0.06"],
            [baseBarcode, "2", "1.223456", "箱", "0.13"],
          ]),
          "utf8",
        ),
      });
      await expect(file).toContainText("2 行需修正");
      await expect(file).toContainText("与条码对应包装不一致");
      const downloading = page.waitForEvent("download");
      await file
        .getByRole("button", { name: "下载待修正清单", exact: true })
        .click();
      const stream = await (await downloading).createReadStream();
      const parts: Buffer[] = [];
      for await (const chunk of stream!) parts.push(Buffer.from(chunk));
      const errors = parseEntryFile(Buffer.concat(parts).toString("utf8"));
      expect(errors.map((r) => r.matchBy)).toEqual(["barcode", "barcode"]);
      expect(errors[0]!.code).toBe("UNKNOWN-BARCODE");
      await file
        .getByLabel("第2行编码或条码", { exact: true })
        .fill(boxBarcode);
      await file.getByLabel("第3行交易单位", { exact: true }).fill("");
      await expect(
        file.getByRole("button", { name: "确认追加 2 行", exact: true }),
      ).toBeDisabled();
      await file.getByRole("button", { name: "重新校验", exact: true }).click();
      await expect(file).toContainText("0 行需修正");
      await expect(file).toContainText(`折合 3 ${product.base_unit}`);
      await expect(file).toContainText(`折合 2 ${product.base_unit}`);
      await expect
        .poll(async () => {
          const rect = await file
            .getByRole("button", { name: "确认追加 2 行", exact: true })
            .boundingBox();
          return !!rect && rect.y >= 0 && rect.y + rect.height < 720;
        })
        .toBe(true);
      await page.screenshot({
        path: `../../outputs/entry-bulk/${channel}-file.png`,
        animations: "disabled",
      });
      await file
        .getByRole("button", { name: "确认追加 2 行", exact: true })
        .click();
      await expect(lines).toHaveCount(4);
      await expect(
        lines.nth(2).getByRole("combobox", { name: "交易单位 3", exact: true }),
      ).toHaveValue(pack.id);
      await chooseParty(
        editor.getByRole("combobox", {
          name: purchase ? "供应商" : "客户",
          exact: true,
        }),
        f.party.id,
      );
      if (!purchase)
        await editor
          .getByLabel("收货地址", { exact: true })
          .fill("批量导入虚构验收地址");
      const endpoint = purchase
        ? "/api/v1/purchase-orders"
        : "/api/v1/sales-orders";
      const saving = page.waitForResponse(
        (r) => r.url().endsWith(endpoint) && r.request().method() === "POST",
      );
      await editor
        .getByRole("button", {
          name: purchase ? "保存采购单" : "保存销售单",
          exact: true,
        })
        .click();
      const response = await saving;
      expect(response.status(), await response.text()).toBe(200);
      const sent = response.request().postDataJSON();
      expect(
        sent.lines.map((r: any) => [r.packaging_id || "", r.qty, r.unit_price]),
      ).toEqual([
        [pack.id, "2", "6.123456"],
        ["", "3", "1.123456"],
        [pack.id, "1", "8.123456"],
        ["", "2", "1.223456"],
      ]);
      const document = await read(
        context,
        `${endpoint}/${(await response.json()).data.id}`,
      );
      expect(document.lines.map((r: any) => r.qty)).toEqual([
        "6.000000",
        "3.000000",
        "3.000000",
        "2.000000",
      ]);
      expect(faults).toEqual([]);
    } finally {
      await reviewer.close();
    }
  });
}

test("failed or cancelled inline rechecks cannot apply an old import preview", async ({
  page,
  context,
}) => {
  await wholesale(context, "demo.owner");
  await page.goto("/");
  await nav(page, "采购入库");
  await page.getByRole("button", { name: "新建采购订单", exact: true }).click();
  const editor = page.getByRole("region", { name: "新建采购单", exact: true });
  const grid = editor.locator(".document-entry");
  const entity = await grid.getAttribute("data-entity");
  const p = (
    await read(
      context,
      `/api/v1/catalog/entry-products?entity_id=${entity}&channel=purchase&search=&match_barcode=false`,
    )
  ).items.find((p: EntryProduct) => p.eligible);
  expect(p).toBeTruthy();
  await editor.getByRole("button", { name: "导入文件", exact: true }).click();
  const dialog = page.getByRole("dialog", {
    name: "导入商品明细",
    exact: true,
  });
  const upload = (qty: string) =>
    dialog.getByLabel("商品明细CSV文件", { exact: true }).setInputFiles({
      name: "重试.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(
        entryCsv(["SKU编码", "数量", "含税单价"], [[p.code, qty, "1.123456"]]),
        "utf8",
      ),
    });
  await upload("1");
  await expect(dialog).toContainText("0 行需修正");
  await dialog.getByLabel("第2行数量", { exact: true }).fill("2");
  const lookup = "**/api/v1/catalog/entry-products?**";
  await page.route(lookup, (route) => route.abort("failed"), { times: 1 });
  await dialog.getByRole("button", { name: "重新校验", exact: true }).click();
  await expect(dialog.getByRole("alert")).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "确认追加 1 行", exact: true }),
  ).toBeDisabled();
  await dialog.getByRole("button", { name: "重新校验", exact: true }).click();
  await expect(dialog).toContainText("0 行需修正");
  await expect(dialog.getByLabel("第2行数量", { exact: true })).toHaveValue(
    "2",
  );
  let release: () => void = () => {},
    reached = false,
    finished = false;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(
    lookup,
    async (route) => {
      const response = await route.fetch();
      reached = true;
      await gate;
      await route.fulfill({ response }).catch(() => {});
      finished = true;
    },
    { times: 1 },
  );
  try {
    await dialog.getByLabel("第2行数量", { exact: true }).fill("3");
    await dialog.getByRole("button", { name: "重新校验", exact: true }).click();
    await expect.poll(() => reached).toBe(true);
    await dialog.getByRole("button", { name: "返回", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await editor.getByRole("button", { name: "导入文件", exact: true }).click();
    await upload("4");
    await expect(dialog).toContainText("0 行需修正");
    release();
    await expect.poll(() => finished).toBe(true);
    await expect(dialog.getByLabel("第2行数量", { exact: true })).toHaveValue(
      "4",
    );
    await expect(editor.locator(".entry-row")).toHaveCount(1);
    await page.setViewportSize({ width: 390, height: 844 });
    const button = dialog.getByRole("button", {
      name: "确认追加 1 行",
      exact: true,
    });
    await expect
      .poll(async () => {
        const rect = await button.boundingBox();
        return (
          !!rect &&
          rect.x >= 0 &&
          rect.x + rect.width <= 390 &&
          rect.y > 0 &&
          rect.y + rect.height < 844
        );
      })
      .toBe(true);
    await page.screenshot({
      path: "../../outputs/entry-bulk/file-390.png",
      animations: "disabled",
    });
    await dialog.getByRole("button", { name: "返回", exact: true }).click();
  } finally {
    release();
  }
});
