import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";
import {
  wholesale,
  stockedCustomer,
  command,
  read,
  nav,
  chooseParty,
} from "./business-helpers";

test("purchase, wholesale and retail lists search all documents and retain applied filters", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(150000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  let retailId = "";
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    const f = await stockedCustomer(context, owner, reviewer, checker);
    const sales = await command(context, owner, "/api/v1/sales-orders", {
      entity_id: f.entity,
      customer_id: f.party.id,
      warehouse_id: f.warehouse,
      delivery_address: "查询测试地址",
      minimum_shelf_days: 0,
      credit_days: 30,
      lines: [
        {
          sku_id: f.product.sku_id,
          qty: "1",
          unit_price: "20",
          tax_rate: "0.13",
        },
      ],
    });
    const retail = await command(context, owner, "/api/v1/retail/sales", {
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
    retailId = retail.id;
    await page.goto("/");
    for (const item of [
      {
        page: "采购入库",
        path: "purchase-orders",
        id: f.purchase.id,
        field: "order",
        state: "RECEIVED",
        open: "查看采购单",
        back: "返回采购列表",
      },
      {
        page: "批发销售",
        path: "sales-orders",
        id: sales.id,
        field: "order",
        state: "DRAFT",
        open: "查看销售单",
        back: "返回销售列表",
      },
      {
        page: "零售收银",
        path: "retail/sales",
        id: retail.id,
        field: "sale",
        state: "RESERVED",
        open: "零售详情",
        back: "返回零售列表",
      },
    ]) {
      const listPath =
        item.path === "purchase-orders" ? "purchasing/workflow" : item.path;
      const dateLabel =
        item.path === "purchase-orders" ? "订单日期" : "创建日期";
      const detail = (await read(context, `/api/v1/${item.path}/${item.id}`))[
        item.field
      ];
      const day = new Intl.DateTimeFormat("en-CA", {
        timeZone: "Asia/Shanghai",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
      }).format(new Date(detail.created_at));
      await nav(page, item.page);
      const form = page.getByRole("form", { name: "单据查询" });
      const rows = page
        .locator(".el-table__body-wrapper tr:visible")
        .filter({ hasText: detail.number });
      const submit = async (enter = false) => {
        await expect(
          form.getByRole("button", { name: "查询单据" }),
        ).toBeEnabled();
        await Promise.all([
          page.waitForResponse(
            (r) =>
              new URL(r.url()).pathname === `/api/v1/${listPath}` &&
              r.request().method() === "GET",
          ),
          enter
            ? form.getByLabel("查找单据").press("Enter")
            : form.getByRole("button", { name: "查询单据" }).click(),
        ]);
        await expect(page.locator(".el-loading-mask:visible")).toHaveCount(0);
      };
      await form.getByLabel("查找单据").fill(detail.number.toLowerCase());
      await form.getByLabel("单据状态").selectOption(item.state);
      await form.getByLabel(`${dateLabel}从`, { exact: true }).fill(day);
      await form.getByLabel(`${dateLabel}至`, { exact: true }).fill(day);
      await submit(true);
      await expect(rows).toHaveCount(1);
      await expect(
        page.locator(".el-table__body-wrapper tr:visible"),
      ).toHaveCount(1);
      await rows.getByRole("button", { name: item.open }).click();
      await page.getByRole("button", { name: item.back, exact: false }).click();
      await expect(form.getByLabel("查找单据")).toHaveValue(
        detail.number.toLowerCase(),
      );
      await expect(form.getByLabel("单据状态")).toHaveValue(item.state);
      await expect(
        form.getByLabel(`${dateLabel}至`, { exact: true }),
      ).toHaveValue(day);
      await expect(rows).toHaveCount(1);
      if (item.path !== "retail/sales") {
        await form
          .getByRole("button", { name: "更多条件", exact: true })
          .click();
        await chooseParty(
          form.getByRole("combobox", {
            name: item.path === "purchase-orders" ? "筛选供应商" : "筛选客户",
            exact: true,
          }),
          f.party.id,
        );
        await form
          .getByLabel("仓库", { exact: true })
          .selectOption(f.warehouse);
        await form
          .getByRole("button", { name: "收起条件（2）", exact: true })
          .click();
        const filtered = page.waitForRequest(
          (r) =>
            new URL(r.url()).pathname === `/api/v1/${listPath}` &&
            new URL(r.url()).searchParams.get("party_id") === f.party.id &&
            new URL(r.url()).searchParams.get("warehouse_id") === f.warehouse,
        );
        await submit();
        await filtered;
        await expect(rows).toHaveCount(1);
        await form
          .getByRole("combobox", { name: "快捷日期范围", exact: true })
          .selectOption("today");
        await submit();
        await expect(rows).toHaveCount(1);
        await form
          .getByRole("combobox", { name: "快捷日期范围", exact: true })
          .selectOption("yesterday");
        await submit();
        await expect(rows).toHaveCount(0);
        await form
          .getByRole("combobox", { name: "快捷日期范围", exact: true })
          .selectOption("all");
        await submit();
        await expect(rows).toHaveCount(1);
        await form.getByLabel("查找单据").fill(f.customer);
        await submit();
        await expect(rows).toHaveCount(1);
      }
      await form.getByLabel("单据状态").selectOption("CANCELLED");
      await submit();
      await expect(rows).toHaveCount(0);
      await form.getByRole("button", { name: "清除筛选" }).click();
      await expect(form.getByLabel("查找单据")).toHaveValue("");
      await expect(
        form.getByLabel(`${dateLabel}从`, { exact: true }),
      ).toHaveValue("");
      await expect(form.getByLabel("单据状态")).toHaveValue("");
      await expect(rows).toHaveCount(1);
      // Refresh must use the last applied criteria, not partially typed edits.
      await form.getByLabel("查找单据").fill(detail.number);
      await submit();
      await form.getByLabel("查找单据").fill("尚未提交的输入");
      await Promise.all([
        page.waitForResponse(
          (r) =>
            new URL(r.url()).pathname === `/api/v1/${listPath}` &&
            r.request().method() === "GET",
        ),
        page
          .getByRole("button", { name: /^刷新(订单|销售单|零售列表)$/ })
          .click(),
      ]);
      await expect(page.locator(".el-loading-mask:visible")).toHaveCount(0);
      await expect(rows).toHaveCount(1);
      if (item.path === "sales-orders") {
        await form.getByLabel("查找单据").fill(detail.number);
        await page.route("**/api/v1/sales-orders?**", (route) =>
          route.fulfill({
            status: 503,
            contentType: "application/json",
            body: JSON.stringify({
              error: { code: "UNAVAILABLE", message: "查询暂不可用" },
            }),
          }),
        );
        await submit();
        await expect(rows).toHaveCount(1);
        await expect(
          page.getByText("查询失败，请重试；表格保留上次成功加载的结果。"),
        ).toBeVisible();
        await expect(form.getByLabel("查找单据")).toHaveValue(detail.number);
        await page.unroute("**/api/v1/sales-orders?**");
        await submit();
        await expect(rows).toHaveCount(1);
        await expect(page.locator(".el-message")).toHaveCount(0, {
          timeout: 10000,
        });
        await page.screenshot({
          path: "../../artifacts/document-search-sales.png",
          fullPage: true,
        });
      }
    }
    expect(errors).toEqual([]);
  } finally {
    if (retailId) {
      const doc = await read(context, `/api/v1/retail/sales/${retailId}`);
      if (doc.sale.state === "RESERVED")
        await command(
          context,
          owner,
          `/api/v1/retail/sales/${retailId}/cancel`,
          {
            expected_version: doc.sale.version,
            reason: "查询测试结束，释放预占",
          },
        );
    }
    await reviewer.close();
  }
});
