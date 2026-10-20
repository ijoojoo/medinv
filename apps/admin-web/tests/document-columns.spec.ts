import { test, expect } from "@playwright/test";
import { mkdir, readFile } from "node:fs/promises";
import {
  wholesale,
  read,
  command,
  nav,
  stockedCustomer,
  chooseEntrySku,
} from "./business-helpers";
const dialog = (page: any) =>
  page.getByRole("dialog", { name: "配置单据明细列", exact: true });
const payload = (reply: any, columns = reply.columns) => ({
  expected_version: reply.version,
  schema_version: 1,
  columns: columns.map(({ required, editable, ...c }: any) => c),
});

test("unified purchase columns preserve multi-line drafts, cancel edits, export, copy and persist separate history settings", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(180000);
  const owner = await wholesale(context, "demo.owner"),
    reviewer = await browser.newContext();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const entity = owner.entities[0].id;
  const editorPath = `/api/v1/document-columns/orders/editor?entity_id=${entity}`;
  const viewPath = `/api/v1/document-columns/orders/view?entity_id=${entity}`;
  const originalEditor = await read(context, editorPath),
    originalView = await read(context, viewPath);
  await command(
    context,
    owner,
    editorPath,
    payload(
      originalEditor,
      originalEditor.fields.map((f: any) => f.default),
    ),
  );
  await command(
    context,
    owner,
    viewPath,
    payload(
      originalView,
      originalView.fields.map((f: any) => f.default),
    ),
  );
  try {
    const checker = await wholesale(reviewer, "demo.quality");
    const f = await stockedCustomer(context, owner, reviewer, checker);
    await page.goto("/");
    await nav(page, "采购入库");
    await page
      .getByRole("button", { name: "新建采购订单", exact: true })
      .click();
    const editor = page.getByRole("region", {
        name: "新建采购单",
        exact: true,
      }),
      rows = editor.locator(".entry-row");
    await chooseEntrySku(rows.first(), f.product.sku_id);
    await rows.first().getByLabel("采购数量", { exact: true }).fill("12");
    await rows
      .first()
      .getByLabel("含税单价（元）", { exact: true })
      .fill("1.234567");
    await rows
      .first()
      .getByLabel("含税单价（元）", { exact: true })
      .press("Enter");
    await expect(rows).toHaveCount(2);
    await chooseEntrySku(rows.nth(1), f.product.sku_id);
    await rows.nth(1).getByLabel("采购数量", { exact: true }).fill("6");
    await rows
      .nth(1)
      .getByLabel("含税单价（元）", { exact: true })
      .fill("2.50");
    const keys = await rows.evaluateAll((items) =>
      items.map((el) => el.getAttribute("data-row-key")),
    );
    const trigger = editor.getByRole("button", { name: "列设置", exact: true });
    await trigger.click();
    await dialog(page)
      .getByLabel("含税单价 显示名称", { exact: true })
      .fill("不应保存");
    await dialog(page)
      .getByRole("button", { name: "恢复默认", exact: true })
      .click();
    await page.keyboard.press("Escape");
    await expect(dialog(page)).not.toBeVisible();
    await expect(trigger).toBeFocused();
    await expect(
      editor.getByRole("columnheader", { name: "不应保存（含税）" }),
    ).toHaveCount(0);
    await trigger.click();
    await dialog(page)
      .getByRole("checkbox", { name: "规格型号", exact: true })
      .check();
    await dialog(page)
      .getByRole("checkbox", { name: "持有人／注册人／备案人", exact: true })
      .check();
    await dialog(page)
      .getByRole("checkbox", { name: "交易单位", exact: true })
      .uncheck();
    await dialog(page)
      .getByLabel("含税单价 显示名称", { exact: true })
      .fill("采购价");
    await dialog(page)
      .getByLabel("含税单价 小数位", { exact: true })
      .selectOption("2");
    await dialog(page).getByLabel("规格型号 宽度", { exact: true }).fill("210");
    await dialog(page)
      .getByLabel("税率 格式", { exact: true })
      .selectOption("percent");
    await dialog(page)
      .getByRole("button", { name: "规格型号 前移", exact: true })
      .click();
    await mkdir("../../outputs/document-columns", { recursive: true });
    await page.screenshot({
      path: "../../outputs/document-columns/配置明细列.png",
      fullPage: true,
    });
    await dialog(page)
      .getByRole("button", { name: "保存配置", exact: true })
      .click();
    await expect(dialog(page)).not.toBeVisible();
    await expect(
      editor.getByRole("columnheader", { name: "采购价（含税）", exact: true }),
    ).toBeVisible();
    await expect(
      rows.first().getByLabel("含税单价（元）", { exact: true }),
    ).toHaveValue("1.23");
    await expect(rows.first().locator('[data-col="tax"] input')).toHaveValue(
      "13%",
    );
    expect(
      await rows.evaluateAll((items) =>
        items.map((el) => el.getAttribute("data-row-key")),
      ),
    ).toEqual(keys);
    await editor.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect(editor.locator(".editor-actions")).toContainText("草稿已保存");
    const url = page.url(),
      draft = await read(
        context,
        "/api/v1/document-drafts/" + url.split("/").pop(),
      );
    expect(draft.content.lines).toHaveLength(2);
    expect(draft.content.lines[0].unit_price).toBe("1.234567");
    await page.screenshot({
      path: "../../outputs/document-columns/新建采购单.png",
      fullPage: true,
    });
    const fresh = await browser.newContext();
    try {
      await wholesale(fresh, "demo.owner");
      const freshPage = await fresh.newPage();
      await freshPage.goto(url);
      await expect(
        freshPage.getByRole("columnheader", {
          name: "采购价（含税）",
          exact: true,
        }),
      ).toBeVisible();
      await expect(freshPage.locator(".entry-row")).toHaveCount(2);
    } finally {
      await fresh.close();
    }
    await editor
      .getByRole("button", { name: "返回采购记录", exact: true })
      .click();
    const detail = await read(
      context,
      `/api/v1/purchase-orders/${f.purchase.id}`,
    );
    const list = page.locator(".purchase-worklist:visible");
    await list
      .getByRole("searchbox", { name: "查找单据" })
      .fill(detail.order.number);
    await list.getByRole("button", { name: "查询单据" }).click();
    await list
      .getByRole("button", { name: detail.order.number, exact: true })
      .click();
    const grid = page.getByRole("region", {
      name: "采购订单明细",
      exact: true,
    });
    await expect(
      grid.getByRole("columnheader", { name: "采购价（含税）", exact: true }),
    ).toHaveCount(0);
    await grid
      .getByRole("button", { name: "配置单据明细列", exact: true })
      .click();
    await expect(
      dialog(page).getByRole("checkbox", { name: /回车/ }),
    ).toHaveCount(0);
    await dialog(page)
      .getByRole("button", { name: "从录入配置复制", exact: true })
      .click();
    await expect(dialog(page).getByRole("status")).toContainText(
      "必填列自动保留",
    );
    await dialog(page)
      .getByLabel("含税单价 显示名称", { exact: true })
      .fill("历史进价");
    await dialog(page)
      .getByRole("button", { name: "保存配置", exact: true })
      .click();
    await expect(
      grid.getByRole("columnheader", { name: "历史进价（含税）", exact: true }),
    ).toBeVisible();
    expect(
      (await read(context, editorPath)).columns.find(
        (c: any) => c.key === "price",
      ).label,
    ).toBe("采购价");
    const download = page.waitForEvent("download");
    await grid
      .getByRole("button", { name: "导出当前视图", exact: true })
      .click();
    const file = await download,
      csv = await readFile((await file.path())!, "utf8");
    expect(csv).toContain("历史进价（含税）");
    expect(csv).toContain("规格型号");
    expect(csv).not.toContain("交易单位");
    await page.screenshot({
      path: "../../outputs/document-columns/采购记录明细.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 960, height: 768 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
    await page.screenshot({
      path: "../../outputs/document-columns/采购记录-窄屏.png",
      fullPage: true,
    });
    expect(errors).toEqual([]);
  } finally {
    for (const [path, original] of [
      [editorPath, originalEditor],
      [viewPath, originalView],
    ] as const) {
      const latest = await read(context, path);
      await command(context, owner, path, payload(latest, original.columns));
    }
    await reviewer.close();
  }
});

test("column saves retry the same body and key after response loss, then reject a stale second window", async ({
  page,
  context,
}) => {
  test.setTimeout(90000);
  const owner = await wholesale(context, "demo.owner"),
    entity = owner.entities[0].id;
  const path = `/api/v1/document-columns/orders/editor?entity_id=${entity}`;
  const original = await read(context, path);
  try {
    await page.goto("/");
    await nav(page, "采购入库");
    await page
      .getByRole("button", { name: "新建采购订单", exact: true })
      .click();
    const trigger = page.getByRole("button", { name: "列设置", exact: true });
    await trigger.click();
    await dialog(page)
      .getByLabel("数量 显示名称", { exact: true })
      .fill("待确认数量");
    let count = 0;
    const requests: { body: string | null; key: string | undefined }[] = [];
    await page.route(
      "**/api/v1/document-columns/orders/editor?*",
      async (route) => {
        if (route.request().method() !== "POST") {
          await route.continue();
          return;
        }
        requests.push({
          body: route.request().postData(),
          key: route.request().headers()["idempotency-key"],
        });
        const response = await route.fetch();
        if (count++ === 0)
          await route.fulfill({
            status: 502,
            contentType: "application/json",
            body: JSON.stringify({
              error: { message: "模拟已保存但响应中断" },
            }),
          });
        else await route.fulfill({ response });
      },
    );
    await dialog(page)
      .getByRole("button", { name: "保存配置", exact: true })
      .click();
    await expect(dialog(page)).toContainText("模拟已保存但响应中断");
    await dialog(page)
      .getByRole("button", { name: "取消", exact: true })
      .click();
    await trigger.click();
    await dialog(page)
      .getByRole("button", { name: "继续确认保存", exact: true })
      .click();
    await expect(dialog(page)).not.toBeVisible();
    expect(requests).toHaveLength(2);
    expect(requests[0]).toEqual(requests[1]);
    await page.unrouteAll();
    await trigger.click();
    const latest = await read(context, path);
    await command(
      context,
      owner,
      path,
      payload(
        latest,
        latest.columns.map((c: any) =>
          c.key === "qty" ? { ...c, label: "另一个窗口" } : c,
        ),
      ),
    );
    await dialog(page)
      .getByLabel("数量 显示名称", { exact: true })
      .fill("过期配置");
    await dialog(page)
      .getByRole("button", { name: "保存配置", exact: true })
      .click();
    await expect(dialog(page)).toContainText("其他窗口");
    expect(
      (await read(context, path)).columns.find((c: any) => c.key === "qty")
        .label,
    ).toBe("另一个窗口");
  } finally {
    const latest = await read(context, path);
    await command(context, owner, path, payload(latest, original.columns));
  }
});
