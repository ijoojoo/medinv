import { test, expect, type Page, type Locator } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import {
  wholesale,
  stockedCustomer,
  read,
  command,
  nav,
} from "./business-helpers";
import {
  unallocatedReceipt,
  receiptAmountIssue,
  inspectionAmountIssue,
  inspectionDictionary,
} from "../src/purchaseEntry";

test("receiving allocation uses remaining base-unit quantities without rounding or double counting", () => {
  const sources = [
    {
      id: "a",
      remaining_qty: "90071992547409.123456",
      qty_scale: 6,
      base_unit: "克",
    },
  ];
  const lines = [{ order_line_id: "a", qty: "90071992547409.000001" }];
  expect(unallocatedReceipt("a", sources, lines)).toBe("0.123455");
  expect(unallocatedReceipt("a", sources, lines, 0)).toBe(
    "90071992547409.123456",
  );
  expect(
    receiptAmountIssue(sources, [
      ...lines,
      { order_line_id: "a", qty: "0.123455" },
    ]),
  ).toBeNull();
  expect(
    receiptAmountIssue(sources, [
      ...lines,
      { order_line_id: "a", qty: "0.123456" },
    ])?.index,
  ).toBe(1);
  expect(
    unallocatedReceipt("a", sources, [{ order_line_id: "a", qty: "x" }]),
  ).toBe("");
  expect(
    receiptAmountIssue(sources, [{ order_line_id: "a", qty: "0" }])?.message,
  ).toContain("至少");
  expect(
    receiptAmountIssue(
      [{ ...sources[0]!, qty_scale: 0 }],
      [{ order_line_id: "a", qty: "0.5" }],
    )?.message,
  ).toContain("整数");
});

test("inspection validates pending balance and derives the conclusion from submitted quantities", () => {
  const row = {
    pending: "5",
    qualified_qty: "5",
    rejected_qty: "0",
    qty_scale: 0,
    base_unit: "盒",
  };
  expect(inspectionAmountIssue([row])).toBeNull();
  expect(inspectionDictionary([row])).toBe("inspection_qualified");
  expect(
    inspectionAmountIssue([{ ...row, rejected_qty: "1" }])?.message,
  ).toContain("超过");
  expect(
    inspectionDictionary([{ ...row, qualified_qty: "4", rejected_qty: "1" }]),
  ).toBe("inspection_mixed");
  expect(
    inspectionDictionary([{ ...row, qualified_qty: "0", rejected_qty: "5" }]),
  ).toBe("inspection_rejected");
  expect(
    inspectionAmountIssue([{ ...row, qualified_qty: "0" }])?.message,
  ).toContain("至少");
  expect(
    inspectionAmountIssue([{ ...row, qualified_qty: "0.1" }])?.message,
  ).toContain("整数");
});

const panel = (page: Page) => page.locator(".purchase-worklist:visible");
async function findOrder(page: Page, number: string, stage = "采购订单") {
  await nav(page, "采购入库");
  await panel(page).getByRole("tab", { name: stage, exact: true }).click();
  await panel(page).getByRole("searchbox", { name: "查找单据" }).fill(number);
  await panel(page).getByRole("button", { name: "查询单据" }).click();
  await expect(
    panel(page).getByRole("button", { name: "查询单据" }),
  ).toBeEnabled();
  return panel(page).getByRole("row").filter({ hasText: number });
}
async function selected(input: Locator) {
  await expect(input).toBeFocused();
  expect(
    await input.evaluate((el: HTMLInputElement) => [
      el.selectionStart,
      el.selectionEnd,
      el.value.length,
    ]),
  ).toEqual([
    0,
    (await input.inputValue()).length,
    (await input.inputValue()).length,
  ]);
}

test("all order rows default remaining quantities, keyboard entry flows across rows, zero arrivals stay pending and inspections default qualified", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(180000);
  const owner = await wholesale(context, "demo.owner"),
    reviewer = await browser.newContext();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    const f = await stockedCustomer(context, owner, reviewer, checker);
    const quantities = Array.from({ length: 12 }, (_, i) => String(i + 10));
    const order = await command(context, owner, "/api/v1/purchase-orders", {
      entity_id: f.entity,
      supplier_id: f.party.id,
      warehouse_id: f.warehouse,
      lines: quantities.map((qty) => ({
        sku_id: f.product.sku_id,
        qty,
        unit_price: "2",
        tax_rate: "0.13",
      })),
    });
    const doc = await read(context, `/api/v1/purchase-orders/${order.id}`);
    const quality = await reviewer.newPage();
    quality.on("pageerror", (e) => errors.push(e.message));
    await quality.goto("/");
    let row = await findOrder(quality, doc.order.number);
    await row.getByRole("button", { name: "查看采购单", exact: true }).click();
    await quality
      .getByRole("button", { name: "审核采购单", exact: true })
      .click();
    const approval = quality.getByRole("dialog", {
      name: "审核采购单",
      exact: true,
    });
    await expect(approval.getByLabel("审核意见", { exact: true })).toHaveValue(
      "同意",
    );
    await approval.getByRole("button", { name: "确认审核" }).click();
    await expect(approval).not.toBeVisible();
    await page.setViewportSize({ width: 960, height: 768 });
    await page.goto("/");
    row = await findOrder(page, doc.order.number, "采购收货");
    await row.getByRole("button", { name: "登记收货", exact: true }).click();
    const receipt = page.getByRole("dialog", { name: "登记到货", exact: true });
    const inputs = receipt.getByLabel("到货数量", { exact: true });
    await expect(inputs).toHaveCount(12);
    for (let i = 0; i < 12; i++) {
      await expect(inputs.nth(i)).toHaveValue(quantities[i]!);
      await expect(
        receipt.getByLabel("采购商品", { exact: true }).nth(i),
      ).toHaveValue(doc.lines[i].id);
    }
    await receipt.getByLabel("随货单号").fill("EFF-" + f.suffix);
    await receipt.getByLabel("随货单号").press("Enter");
    await expect(receipt.getByLabel("运输及随货核查")).toBeFocused();
    await receipt.getByLabel("运输及随货核查").fill("多行键盘收货验收");
    await receipt.getByLabel("运输及随货核查").press("Enter");
    await selected(inputs.first());
    await page.keyboard.insertText("5");
    for (let i = 0; i < 12; i++) {
      await expect(inputs.nth(i)).toBeFocused();
      if (i === 1) {
        await page.keyboard.insertText("0");
        await inputs.nth(i).press("Tab");
        await selected(inputs.nth(i + 1));
        continue;
      }
      await inputs.nth(i).press(i % 2 ? "Tab" : "Enter");
      const lot = receipt.getByLabel("生产批号", { exact: true }).nth(i);
      await expect(lot).toBeFocused();
      await lot.fill("EFF-" + f.suffix + "-" + i);
      await lot.press("Enter");
      const mfg = receipt.getByLabel("生产日期", { exact: true }).nth(i);
      await expect(mfg).toBeFocused();
      if (i === 0) {
        await mfg.fill("20260230");
        await mfg.press("Tab");
        await expect(mfg).toBeFocused();
        await expect(mfg).toHaveAttribute("aria-invalid", "true");
      }
      await mfg.fill(`${f.year}0101`);
      await mfg.press("Enter");
      const expiry = receipt.getByLabel("有效期至", { exact: true }).nth(i);
      await expect(expiry).toBeFocused();
      await expiry.fill(`${f.year + 1}1231`);
      if (i === 11) {
        const box = await expiry.boundingBox(),
          viewport = await receipt
            .locator(".document-grid-scroll")
            .boundingBox();
        expect(box!.y).toBeGreaterThanOrEqual(viewport!.y + 34);
        expect(box!.y + box!.height).toBeLessThanOrEqual(
          viewport!.y + viewport!.height,
        );
        const saveBounds = await receipt
          .getByRole("button", { name: "保存到货记录" })
          .boundingBox();
        expect(saveBounds!.y + saveBounds!.height).toBeLessThanOrEqual(768);
        const addBounds = await receipt
          .getByRole("button", { name: "添加到货行" })
          .boundingBox();
        expect(Math.abs(addBounds!.y - saveBounds!.y)).toBeLessThan(3);
        expect(addBounds!.y + addBounds!.height).toBeLessThanOrEqual(768);
        await mkdir("../../outputs/purchase-efficiency", { recursive: true });
        await page.screenshot({
          path: "../../outputs/purchase-efficiency/多行收货.png",
          animations: "disabled",
        });
      }
      await expiry.press(i % 2 ? "Enter" : "Tab");
      if (i < 11) await selected(inputs.nth(i + 1));
    }
    // Ctrl+Enter from a valid date commits the form after validation.
    await receipt.getByLabel("有效期至", { exact: true }).last().focus();
    await receipt
      .getByLabel("有效期至", { exact: true })
      .last()
      .press("Control+Enter");
    await expect(receipt).not.toBeVisible();
    let updated = await read(context, `/api/v1/purchase-orders/${order.id}`);
    expect(updated.receipts[0].lines.map((l: any) => l.order_line_id)).toEqual(
      doc.lines.filter((_: any, i: number) => i !== 1).map((l: any) => l.id),
    );
    await page.getByRole("button", { name: "登记到货", exact: true }).click();
    await expect(inputs).toHaveCount(2);
    await expect(inputs.nth(0)).toHaveValue("5");
    await expect(inputs.nth(1)).toHaveValue("11");
    await receipt.getByRole("button", { name: "关闭此对话框" }).click();
    await quality
      .getByRole("button", { name: "← 返回采购列表", exact: true })
      .click();
    row = await findOrder(quality, doc.order.number, "采购验收");
    await row.getByRole("button", { name: "开始验收", exact: true }).click();
    const inspect = quality.getByRole("dialog", {
      name: "登记验收",
      exact: true,
    });
    const qualified = inspect.getByLabel("本次合格数量", { exact: true }),
      rejected = inspect.getByLabel("本次拒收数量", { exact: true });
    await expect(qualified).toHaveCount(11);
    const expectedQty = ["5", ...quantities.slice(2)];
    for (let i = 0; i < 11; i++) {
      await expect(qualified.nth(i)).toHaveValue(expectedQty[i]!);
      await expect(rejected.nth(i)).toHaveValue("0");
    }
    await expect(inspect.getByLabel("验收结论", { exact: true })).toHaveValue(
      "合格",
    );
    await qualified.first().click();
    await selected(qualified.first());
    await quality.keyboard.insertText("4");
    await qualified.first().press("Enter");
    await selected(qualified.nth(1));
    await qualified.nth(1).press("Shift+Tab");
    await selected(qualified.first());
    await rejected.first().fill("1");
    await expect(inspect.getByLabel("验收结论", { exact: true })).toHaveValue(
      "部分合格",
    );
    await expect(
      inspect.getByLabel("验收依据与结论", { exact: true }),
    ).toHaveAttribute("required", "");
    // Reduce another row, leaving a real pending balance for a second inspection.
    await qualified.nth(1).fill("10");
    await inspect
      .getByLabel("验收依据与结论", { exact: true })
      .fill("一件外包装破损；第二行余下两件待核验");
    await quality.screenshot({
      path: "../../outputs/purchase-efficiency/多行验收.png",
      animations: "disabled",
    });
    await inspect.getByRole("button", { name: "保存验收记录" }).click();
    await expect(inspect).not.toBeVisible();
    updated = await read(context, `/api/v1/purchase-orders/${order.id}`);
    const rec = updated.receipts[0];
    expect(rec.inspections[0].note).toContain("部分合格；");
    expect(Number(rec.lines[1].pending_qty)).toBe(2);
    await quality
      .getByRole("button", { name: "登记验收", exact: true })
      .click();
    await expect(qualified).toHaveCount(1);
    await expect(qualified.first()).toHaveValue("2");
    await expect(inspect.getByLabel("验收结论", { exact: true })).toHaveValue(
      "合格",
    );
    await inspect.getByRole("button", { name: "保存验收记录" }).click();
    await expect(inspect).not.toBeVisible();
    expect(errors).toEqual([]);
  } finally {
    await reviewer.close();
  }
});
