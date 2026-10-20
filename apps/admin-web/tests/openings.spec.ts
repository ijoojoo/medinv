import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import {
  wholesale,
  stockedCustomer,
  command,
  read,
  nav,
  origin,
} from "./business-helpers";
import { headers } from "./helpers";

for (const entry of ["manual", "csv"] as const) {
  test(`independently reviewed ${entry} warehouse opening becomes saleable stock with cost and no payable`, async ({
    page,
    context,
    browser,
  }) => {
    test.setTimeout(150000);
    const owner = await wholesale(context, "demo.owner");
    const reviewer = await browser.newContext(),
      viewer = await browser.newContext();
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    try {
      const checker = await wholesale(reviewer, "demo.quality");
      const f = await stockedCustomer(context, owner, reviewer, checker);
      const w = await command(context, owner, "/api/v1/warehouses", {
        client_operation_id: randomUUID(),
        entity_id: f.entity,
        code: "OP-" + f.suffix,
        name: "期初验收仓-" + f.suffix,
        location_code: "OP-A1",
      });
      await page.goto("/");
      await nav(page, "期初库存");
      await page
        .getByRole("button", { name: "新建期初库存", exact: true })
        .click();
      await page
        .getByRole("combobox", { name: "启用仓库", exact: true })
        .selectOption(w.id);
      await page
        .getByLabel("期初清单凭据", { exact: true })
        .fill("OPEN-" + f.suffix);
      if (entry === "manual") {
        await page
          .getByRole("combobox", { name: "期初商品", exact: true })
          .selectOption(f.product.sku_id);
        await page
          .getByLabel("生产批号", { exact: true })
          .fill("EARLY-" + f.suffix);
        await page.getByLabel("基本单位数量", { exact: true }).fill("4");
        await page
          .getByLabel("生产日期", { exact: true })
          .fill(`${f.year}-01-01`);
        await page
          .getByLabel("有效期至", { exact: true })
          .fill(`${f.year + 1}-06-30`);
        await page.getByLabel("账面成本总额（元）", { exact: true }).fill("40");
        await page.getByRole("button", { name: "添加期初行" }).click();
        await page
          .getByRole("combobox", { name: "期初商品", exact: true })
          .nth(1)
          .selectOption(f.product.sku_id);
        await page
          .getByLabel("生产批号", { exact: true })
          .nth(1)
          .fill("LATE-" + f.suffix);
        await page.getByLabel("基本单位数量", { exact: true }).nth(1).fill("6");
        await page
          .getByLabel("生产日期", { exact: true })
          .nth(1)
          .fill(`${f.year}-01-01`);
        await page
          .getByLabel("有效期至", { exact: true })
          .nth(1)
          .fill(`${f.year + 1}-12-31`);
        await page
          .getByLabel("账面成本总额（元）", { exact: true })
          .nth(1)
          .fill("90");
      } else {
        const downloadEvent = page.waitForEvent("download");
        await page
          .getByRole("button", { name: "下载期初模板", exact: true })
          .click();
        const downloaded = await downloadEvent;
        expect(downloaded.suggestedFilename()).toBe("期初库存导入模板.csv");
        const templatePath = await downloaded.path();
        expect(readFileSync(templatePath!, "utf8")).toBe(
          "\uFEFFSKU编码,库位编码,批号,生产日期,有效期至,期初数量,期初成本金额\r\n",
        );
        const csv =
          "SKU编码,库位编码,批号,生产日期,有效期至,期初数量,期初成本金额\r\n" +
          `SS-${f.suffix},OP-A1,EARLY-${f.suffix},${f.year}-01-01,${f.year + 1}-06-30,4,40\r\n` +
          `SS-${f.suffix},OP-A1,LATE-${f.suffix},${f.year}-01-01,${f.year + 1}-12-31,6,90\r\n`;
        const input = page.getByLabel("期初CSV文件", { exact: true });
        await page.getByLabel("生产批号", { exact: true }).fill("保留未保存行");
        for (const [name, buffer, message] of [
          ["旧表格.xlsx", Buffer.from("not-a-csv"), "请选择 CSV 文件"],
          [
            "旧编码.csv",
            Buffer.from([0xff, 0xfe, 0x00]),
            "文件不是 UTF-8 编码",
          ],
          ["过大文件.csv", Buffer.alloc(512 * 1024 + 1, " "), "文件超过512 KB"],
        ] as const) {
          await input.setInputFiles({
            name,
            mimeType: "application/octet-stream",
            buffer,
          });
          await expect(
            page.getByRole("alert").filter({ hasText: message }),
          ).toBeVisible();
          await expect(
            page.getByLabel("生产批号", { exact: true }),
          ).toHaveValue("保留未保存行");
          await page
            .getByRole("button", { name: "关闭导入预览", exact: true })
            .click();
          await expect(
            page.getByRole("button", { name: "保存期初清单", exact: true }),
          ).toBeEnabled();
        }
        await input.setInputFiles({
          name: "错误清单.csv",
          mimeType: "text/csv",
          buffer: Buffer.from(csv.replace(`SS-${f.suffix}`, "UNKNOWN-SKU")),
        });
        await expect(
          page.getByText("共2行，其中1行需更正；尚未填入清单。", {
            exact: true,
          }),
        ).toBeVisible();
        await expect(
          page.getByRole("button", {
            name: "核对后填入清单（替换现有行）",
            exact: true,
          }),
        ).toBeDisabled();
        await expect(page.getByLabel("生产批号", { exact: true })).toHaveValue(
          "保留未保存行",
        );
        await expect(
          page.getByRole("button", { name: "保存期初清单", exact: true }),
        ).toBeDisabled();
        await input.setInputFiles({
          name: "期初完整清单.csv",
          mimeType: "text/csv",
          buffer: Buffer.from("\uFEFF" + csv),
        });
        await expect(
          page.getByText("校验通过：2行，期初成本合计 130.00 元。", {
            exact: true,
          }),
        ).toBeVisible();
        const before = await read(
          context,
          `/api/v1/inventory/availability?entity_id=${f.entity}&warehouse_id=${w.id}&sku_id=${f.product.sku_id}`,
        );
        expect(before.items).toHaveLength(0);
        await page.screenshot({
          path: "../../artifacts/opening-import-preview.png",
          fullPage: true,
        });
        await page
          .getByRole("button", {
            name: "核对后填入清单（替换现有行）",
            exact: true,
          })
          .click();
        await expect(page.getByLabel("生产批号", { exact: true })).toHaveCount(
          2,
        );
        await expect(
          page.getByLabel("生产批号", { exact: true }).first(),
        ).toHaveValue("EARLY-" + f.suffix);
        await expect(
          page.getByLabel("期初清单凭据", { exact: true }),
        ).toHaveValue("OPEN-" + f.suffix);
        await expect(
          page.getByRole("button", { name: "保存期初清单", exact: true }),
        ).toBeEnabled();
      }
      await page.getByLabel("开账说明").fill("软件验收：原有实物及账面成本");
      await page.getByRole("button", { name: "保存期初清单" }).click();
      await expect(
        page.getByRole("heading", { name: "期初库存详情" }),
      ).toBeVisible();
      const number = await page.locator(".document-number").innerText();
      await expect(
        page.getByRole("button", { name: "审核期初单", exact: true }),
      ).toBeDisabled();
      const quality = await reviewer.newPage();
      quality.on("pageerror", (e) => errors.push(e.message));
      await quality.goto("/");
      await nav(quality, "期初库存");
      await quality
        .getByRole("row")
        .filter({ hasText: number })
        .getByRole("button", { name: "查看期初单" })
        .click();
      await quality
        .getByRole("button", { name: "审核期初单", exact: true })
        .click();
      let dialog = quality.getByRole("dialog");
      await dialog.getByLabel("核验凭据").fill("核对两批次期初实盘");
      await dialog.getByLabel("操作说明").fill("数量、批号效期与账面成本一致");
      await dialog.getByRole("button", { name: "提交审核" }).click();
      await expect(dialog).not.toBeVisible();
      await quality
        .getByRole("button", { name: "启用期初库存", exact: true })
        .click();
      dialog = quality.getByRole("dialog");
      await expect(dialog).toContainText("成本合计 130.00 元");
      await dialog.getByLabel("操作说明").fill("确认期初清单完整，启用业务");
      await dialog.getByRole("button", { name: "确认启用" }).click();
      await expect(dialog).not.toBeVisible();
      await expect(
        quality.getByRole("alert").filter({ hasText: "期初库存已启用" }),
      ).toBeVisible();
      await expect(quality.locator(".el-message")).toHaveCount(0);
      await quality.screenshot({
        path: "../../artifacts/opening-enabled.png",
        fullPage: true,
        animations: "disabled",
      });
      const docs = await read(
        context,
        `/api/v1/inventory-openings?entity_id=${f.entity}`,
      );
      const oid = docs.items.find((d: any) => d.number === number).id;
      const detail = await read(context, `/api/v1/inventory-openings/${oid}`);
      const availability = await read(
        context,
        `/api/v1/inventory/availability?entity_id=${f.entity}&warehouse_id=${w.id}&sku_id=${f.product.sku_id}`,
      );
      expect(
        availability.items.reduce(
          (sum: number, item: any) => sum + Number(item.available),
          0,
        ),
      ).toBe(10);
      const ledger = await read(
        context,
        `/api/v1/inventory/ledger?entity_id=${f.entity}`,
      );
      const entries = ledger.items.filter(
        (l: any) => l.posting_id === detail.document.posting_id,
      );
      expect(entries).toHaveLength(2);
      expect(
        entries.every(
          (l: any) =>
            l.event_type === "OPENING_BALANCE" && l.financial_kind === null,
        ),
      ).toBe(true);

      // The ordinary retail command consumes the earlier opening lot and its actual cost.
      const account = await command(
        context,
        owner,
        "/api/v1/settlements/accounts",
        {
          entity_id: f.entity,
          code: "OPC-" + f.suffix,
          name: "期初试销现金",
          method: "CASH",
        },
      );
      const sale = await command(context, owner, "/api/v1/retail/sales", {
        client_operation_id: randomUUID(),
        entity_id: f.entity,
        warehouse_id: w.id,
        lines: [
          {
            sku_id: f.product.sku_id,
            qty: "3",
            unit_price: "20",
            tax_rate: "0",
          },
        ],
      });
      await command(context, owner, `/api/v1/retail/sales/${sale.id}/cash`, {
        client_operation_id: randomUUID(),
        expected_version: "1",
        account_id: account.id,
        tendered: "60",
        confirmed: true,
        reason: "试销现金实收",
      });
      const retail = await read(context, `/api/v1/retail/sales/${sale.id}`);
      expect(
        retail.allocations.every((l: any) => l.lot_no === "EARLY-" + f.suffix),
      ).toBe(true);
      const result = await command(
        context,
        owner,
        `/api/v1/retail/sales/${sale.id}/complete`,
        {
          expected_version: retail.sale.version,
          reason: "核对实物交付",
          lines: retail.allocations.map((l: any) => ({
            allocation_id: l.id,
            qty: l.allocated_qty,
            lot_no: l.lot_no,
          })),
        },
      );
      expect(result.cost_amount).toBe("30.00000000");
      await nav(page, "库存查询");
      await page.getByRole("combobox", { name: "库存仓库" }).press("Enter");
      await page
        .getByRole("option", { name: "期初验收仓-" + f.suffix, exact: true })
        .click();
      await page.getByRole("combobox", { name: "库存商品" }).click();
      await page
        .getByRole("option", {
          name: `${f.name} · SS-${f.suffix}`,
          exact: true,
        })
        .click();
      await page.getByRole("button", { name: "查询库存", exact: true }).click();
      await expect
        .poll(async () =>
          (await page.locator(".available").allTextContents())
            .map(Number)
            .sort((a, b) => a - b),
        )
        .toEqual([1, 6]);
      await expect(page.locator(".el-message")).toHaveCount(0);
      await page.screenshot({
        path: "../../artifacts/opening-after-sale.png",
        fullPage: true,
        animations: "disabled",
      });

      const observer = await wholesale(viewer, "demo.viewer");
      const vp = await viewer.newPage();
      await vp.goto("/");
      await nav(vp, "期初库存");
      await expect(
        vp.getByRole("button", { name: "新建期初库存", exact: true }),
      ).toHaveCount(0);
      const forbidden = await viewer.request.post(
        origin + `/api/v1/inventory-openings/${oid}/post`,
        {
          headers: { ...headers(observer), "Idempotency-Key": randomUUID() },
          data: { expected_version: detail.document.version, reason: "越权" },
        },
      );
      expect(forbidden.status()).toBe(403);
      expect(errors).toEqual([]);
    } finally {
      await reviewer.close();
      await viewer.close();
    }
  });
}
