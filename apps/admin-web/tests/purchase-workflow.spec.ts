import { test, expect, type Page } from "@playwright/test";
import {
  wholesale,
  read,
  command,
  stockedCustomer,
  nav,
  origin,
} from "./business-helpers";
import { headers } from "./helpers";
import { mkdir } from "node:fs/promises";
const current = (page: Page) => page.locator(".purchase-worklist:visible");
async function menu(page: Page, name: string) {
  await page
    .getByRole("navigation", { name: "主要功能", exact: true })
    .getByRole("button", { name: "采购", exact: true })
    .click();
  await page
    .getByRole("navigation", { name: "采购功能", exact: true })
    .getByRole("button", { name, exact: true })
    .click();
}
async function filter(page: Page, search: string) {
  const panel = current(page);
  await panel.getByRole("searchbox", { name: "查找单据" }).fill(search);
  await panel.getByRole("button", { name: "查询单据" }).click();
  await expect(panel.getByRole("button", { name: "查询单据" })).toBeEnabled();
}

test("compact purchasing tabs preserve separate filters, pagination, scroll and keyboard navigation", async ({
  page,
  context,
}) => {
  test.setTimeout(90000);
  await wholesale(context, "demo.owner");
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await nav(page, "采购入库");
  const panel = current(page);
  await expect(panel.getByRole("button", { name: "查询单据" })).toBeEnabled();
  await expect(page.locator(".purchase-workspace .flow-strip")).toHaveCount(0);
  const tabbar = panel.getByRole("tablist", { name: "采购业务环节" });
  expect(await tabbar.getByRole("tab").count()).toBe(4);
  const tabsRect = await tabbar.boundingBox(),
    actionRect = await panel
      .getByRole("button", { name: "新建采购订单" })
      .boundingBox();
  expect(Math.abs(tabsRect!.y - actionRect!.y)).toBeLessThan(15);
  // Use the original date control and real server queries, including compact input.
  await panel
    .getByRole("textbox", { name: "订单日期从", exact: true })
    .fill("20260101");
  await panel
    .getByRole("textbox", { name: "订单日期至", exact: true })
    .fill("20291231");
  const response = page.waitForResponse(
    (r) =>
      r.url().includes("/purchasing/workflow?") &&
      r.request().method() === "GET",
  );
  await panel.getByRole("button", { name: "查询单据" }).click();
  expect(new URL((await response).url()).searchParams.get("date_from")).toBe(
    "2026-01-01",
  );
  await expect(panel.getByRole("button", { name: "查询单据" })).toBeEnabled();
  await panel.getByLabel("日期排序", { exact: true }).selectOption("oldest");
  await expect(panel.getByRole("button", { name: "查询单据" })).toBeEnabled();
  const morePages = await panel
    .getByRole("button", { name: "下一页" })
    .isEnabled();
  if (morePages) {
    await panel.getByRole("button", { name: "下一页" }).click();
    await expect(panel.locator("footer")).toContainText("第 2 页");
  }
  await panel.getByRole("button", { name: "更多条件", exact: true }).click();
  await panel
    .getByRole("searchbox", { name: "查找单据" })
    .fill("尚未执行的查询");
  const scroll = panel
    .locator(".el-table__body-wrapper .el-scrollbar__wrap")
    .first();
  await scroll.evaluate((el) => {
    el.scrollTop = 260;
    el.dispatchEvent(new Event("scroll"));
  });
  const position = await scroll.evaluate((el) => el.scrollTop);
  await panel.getByRole("tab", { name: "采购入库", exact: true }).click();
  await expect(
    panel.getByRole("button", { name: "确认入库", exact: true }).first(),
  ).toBeDisabled();
  await expect(panel.getByRole("searchbox", { name: "查找单据" })).toHaveValue(
    "",
  );
  await panel.getByRole("tab", { name: "采购订单", exact: true }).click();
  await expect(panel.getByRole("searchbox", { name: "查找单据" })).toHaveValue(
    "尚未执行的查询",
  );
  await expect(
    panel.getByRole("textbox", { name: "订单日期从", exact: true }),
  ).toHaveValue("2026-01-01");
  await expect(panel.getByLabel("日期排序", { exact: true })).toHaveValue(
    "oldest",
  );
  await expect(
    panel.getByRole("button", { name: "收起条件", exact: true }),
  ).toBeVisible();
  if (morePages) await expect(panel.locator("footer")).toContainText("第 2 页");
  await expect.poll(() => scroll.evaluate((el) => el.scrollTop)).toBe(position);
  let requests = 0;
  page.on("request", (r) => {
    if (r.url().includes("/purchasing/workflow?")) requests++;
  });
  await panel
    .getByRole("textbox", { name: "订单日期从", exact: true })
    .fill("20260230");
  const before = requests;
  await panel.getByRole("button", { name: "查询单据" }).click();
  await expect(
    panel.getByRole("textbox", { name: "订单日期从", exact: true }),
  ).toHaveAttribute("aria-invalid", "true");
  expect(requests).toBe(before);
  await panel.getByRole("button", { name: "清除筛选" }).click();
  await expect(panel.getByRole("button", { name: "查询单据" })).toBeEnabled();
  await panel
    .getByRole("tab", { name: "采购订单", exact: true })
    .press("ArrowRight");
  await expect(
    panel.getByRole("tab", { name: "采购收货", exact: true }),
  ).toBeFocused();
  await menu(page, "采购验收");
  await expect(
    panel.getByRole("tab", { name: "采购验收", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  const worktabs = page.getByRole("navigation", { name: "工作标签" });
  await expect(
    worktabs.getByRole("button", { name: "采购入库", exact: true }),
  ).toHaveCount(1);
  await expect(
    worktabs.getByRole("button", { name: "采购验收", exact: true }),
  ).toHaveCount(0);
  await menu(page, "采购订单");
  await panel.getByRole("button", { name: "收起条件", exact: true }).click();
  await mkdir("../../outputs/purchase-workflow", { recursive: true });
  await page.screenshot({
    path: "../../outputs/purchase-workflow/采购订单-桌面.png",
    fullPage: true,
  });
  const trigger = page
    .getByRole("navigation", { name: "主要功能" })
    .getByRole("button", { name: "采购", exact: true });
  await trigger.hover();
  const popup = page.getByRole("navigation", { name: "采购功能", exact: true });
  await expect(popup.getByRole("region")).toHaveCount(2);
  await expect(
    popup.getByRole("button", { name: "收货记录", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "../../outputs/purchase-workflow/采购双栏菜单.png",
    fullPage: true,
  });
  await page.mouse.move(1000, 700);
  await expect(popup).not.toBeVisible();
  await trigger.focus();
  await trigger.press("ArrowDown");
  await expect(
    popup.getByRole("button", { name: "采购订单", exact: true }),
  ).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(trigger).toBeFocused();
  await page.setViewportSize({ width: 960, height: 768 });
  await expect
    .poll(
      async () =>
        (await panel.locator("footer").boundingBox())!.y +
        (await panel.locator("footer").boundingBox())!.height,
    )
    .toBeLessThanOrEqual(768);
  await page.screenshot({
    path: "../../outputs/purchase-workflow/采购订单-窄屏.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    panel.getByRole("tab", { name: "采购订单", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBe(true);
  await panel.getByRole("button", { name: "下一页" }).scrollIntoViewIfNeeded();
  await page.screenshot({
    path: "../../outputs/purchase-workflow/采购订单-小屏.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("real worklist actions select their source, preserve partial quantities and expose history without duplicate posting", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(150000);
  const owner = await wholesale(context, "demo.owner"),
    reviewer = await browser.newContext(),
    viewer = await browser.newContext();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    const f = await stockedCustomer(context, owner, reviewer, checker);
    const order = await command(context, owner, "/api/v1/purchase-orders", {
      entity_id: f.entity,
      supplier_id: f.party.id,
      warehouse_id: f.warehouse,
      lines: [
        {
          sku_id: f.product.sku_id,
          qty: "100",
          unit_price: "13",
          tax_rate: "0.13",
        },
      ],
    });
    await command(
      reviewer,
      checker,
      `/api/v1/purchase-orders/${order.id}/approve`,
      { expected_version: "1", reason: "四环节工作区软件验收" },
    );
    const detail = await read(context, `/api/v1/purchase-orders/${order.id}`);
    await page.goto("/");
    await menu(page, "采购收货");
    await filter(page, detail.order.number);
    await expect(
      current(page)
        .getByRole("button", { name: "登记收货", exact: true })
        .first(),
    ).toBeDisabled();
    await current(page).getByRole("radio").check();
    await current(page)
      .getByRole("button", { name: "登记收货", exact: true })
      .first()
      .click();
    let dialog = page.getByRole("dialog", { name: "登记到货", exact: true });
    await expect(dialog).toBeVisible();
    await dialog.getByLabel("随货单号").fill("FLOW-" + f.suffix);
    await dialog
      .getByLabel("运输及随货核查")
      .fill("四环节软件测试，实收90，剩余10待收");
    await dialog.getByLabel("到货数量", { exact: true }).fill("90");
    await dialog.getByLabel("生产批号").fill("FLOW-" + f.suffix);
    await dialog.getByLabel("生产日期", { exact: true }).fill("20260230");
    await dialog.getByLabel("生产日期", { exact: true }).press("Enter");
    await expect(dialog.getByLabel("生产日期", { exact: true })).toBeFocused();
    await expect(
      dialog.getByLabel("生产日期", { exact: true }),
    ).toHaveAttribute("aria-invalid", "true");
    await dialog.getByLabel("生产日期", { exact: true }).fill(`${f.year}0101`);
    await dialog.getByLabel("生产日期", { exact: true }).press("Enter");
    await expect(dialog.getByLabel("有效期至", { exact: true })).toBeFocused();
    await dialog
      .getByLabel("有效期至", { exact: true })
      .fill(`${f.year + 1}1231`);
    await mkdir("../../outputs/document-columns", { recursive: true });
    await page.screenshot({
      path: "../../outputs/document-columns/到货录入.png",
      animations: "disabled",
      fullPage: true,
    });
    await dialog.getByRole("button", { name: "保存到货记录" }).click();
    await expect(dialog).not.toBeVisible();
    await page
      .getByRole("button", { name: "← 返回采购列表", exact: true })
      .click();
    await expect(
      current(page).getByRole("row").filter({ hasText: detail.order.number }),
    ).toContainText("待收 10");
    await current(page)
      .getByRole("tab", { name: "采购验收", exact: true })
      .click();
    await filter(page, detail.order.number);
    await expect(
      current(page).getByRole("row").filter({ hasText: detail.order.number }),
    ).toContainText("请由另一位成员验收");
    const quality = await reviewer.newPage();
    quality.on("pageerror", (e) => errors.push(e.message));
    await quality.goto("/");
    await menu(quality, "采购验收");
    await filter(quality, detail.order.number);
    await current(quality)
      .getByRole("row")
      .filter({ hasText: detail.order.number })
      .getByRole("button", { name: "开始验收", exact: true })
      .click();
    dialog = quality.getByRole("dialog", { name: "登记验收", exact: true });
    await expect(dialog).toContainText("90");
    await dialog.getByLabel("合格数量").fill("70");
    await dialog.getByLabel("拒收数量").fill("15");
    await dialog
      .getByLabel("验收依据与结论")
      .fill("合格70，不合格15，剩余5继续待验");
    await quality.screenshot({
      path: "../../outputs/document-columns/验收录入.png",
      animations: "disabled",
      fullPage: true,
    });
    await dialog.getByRole("button", { name: "保存验收记录" }).click();
    await expect(dialog).not.toBeVisible();
    await quality
      .getByRole("button", { name: "← 返回采购列表", exact: true })
      .click();
    await expect(
      current(quality)
        .getByRole("row")
        .filter({ hasText: detail.order.number }),
    ).toContainText("待验 5");
    await current(quality)
      .getByRole("tab", { name: "采购入库", exact: true })
      .click();
    await filter(quality, detail.order.number);
    const row = current(quality)
      .getByRole("row")
      .filter({ hasText: detail.order.number });
    await expect(row).toContainText("可入库 70");
    await row.getByRole("button", { name: "确认入库", exact: true }).click();
    dialog = quality.getByRole("dialog", { name: "确认合格入库", exact: true });
    await expect(dialog.locator('[data-grid-cell="qualified_qty"]')).toHaveText(
      "70.000000",
    );
    await expect(dialog.locator('[data-grid-cell="base_unit"]')).toHaveText(
      "个",
    );
    await quality.screenshot({
      path: "../../outputs/document-columns/入库确认.png",
      animations: "disabled",
      fullPage: true,
    });
    await dialog
      .getByRole("button", { name: "确认入库", exact: true })
      .dblclick();
    await expect(dialog).not.toBeVisible();
    await quality
      .getByRole("button", { name: "← 返回采购列表", exact: true })
      .click();
    await expect(
      current(quality)
        .getByRole("row")
        .filter({ hasText: detail.order.number }),
    ).toHaveCount(0);
    await current(quality)
      .getByRole("button", { name: "入库记录", exact: true })
      .click();
    await filter(quality, detail.order.number);
    await expect(
      current(quality)
        .getByRole("row")
        .filter({ hasText: detail.order.number }),
    ).toContainText("本次入库 70");
    const final = await read(context, `/api/v1/purchase-orders/${order.id}`),
      receipt = final.receipts[0];
    expect(Number(receipt.lines[0].pending_qty)).toBe(5);
    expect(Number(receipt.lines[0].posted_qty)).toBe(70);
    expect(receipt.postings).toHaveLength(1);
    await current(quality)
      .getByRole("row")
      .filter({ hasText: detail.order.number })
      .getByRole("button", { name: "查看关联记录" })
      .click();
    await expect(
      quality.locator(`[data-purchase-segment="${receipt.postings[0].id}"]`),
    ).toBeFocused();
    await quality
      .getByRole("button", { name: "← 返回采购列表", exact: true })
      .click();
    await mkdir("../../outputs/purchase-workflow", { recursive: true });
    await quality.screenshot({
      path: "../../outputs/purchase-workflow/入库记录-联查.png",
      fullPage: true,
    });
    const reader = await wholesale(viewer, "demo.viewer");
    const pending = await read(
      viewer,
      `/api/v1/purchasing/workflow?entity_id=${f.entity}&stage=receiving&search=${detail.order.number}`,
    );
    expect(pending.items).toHaveLength(1);
    expect(pending.items[0].action).toBe("");
    const forbidden = await viewer.request.post(
      origin + `/api/v1/receipts/${receipt.id}/inspections`,
      {
        headers: { ...headers(reader), "Idempotency-Key": crypto.randomUUID() },
        data: {
          expected_version: receipt.version,
          note: "权限检查，不应写入",
          lines: [
            {
              receipt_line_id: receipt.lines[0].id,
              qualified_qty: "5",
              rejected_qty: "0",
            },
          ],
        },
      },
    );
    expect(forbidden.status()).toBe(403);
    const viewPage = await viewer.newPage();
    await viewPage.goto("/");
    await menu(viewPage, "采购收货");
    await filter(viewPage, detail.order.number);
    await expect(
      current(viewPage).getByRole("button", { name: "登记收货", exact: true }),
    ).toHaveCount(0);
    expect(errors).toEqual([]);
  } finally {
    await reviewer.close();
    await viewer.close();
  }
});
