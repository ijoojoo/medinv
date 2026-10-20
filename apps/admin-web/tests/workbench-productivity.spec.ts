import { test, expect, type Page, type Locator } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { wholesale, command, read } from "./business-helpers";

const home = (page: Page) =>
  page.getByRole("region", { name: "企业工作台方案", exact: true });
async function operations(page: Page) {
  await page.getByRole("button", { name: "企业工作台", exact: true }).click();
  await home(page)
    .getByRole("combobox", { name: "工作台方案", exact: true })
    .selectOption("operations");
}
const shortcuts = (page: Page) =>
  home(page).getByRole("region", { name: "常用操作", exact: true });
const browser = (page: Page) =>
  page.getByRole("dialog", { name: "我的编辑草稿", exact: true });

async function drawerFits(drawer: Locator, width: number) {
  await expect
    .poll(async () => {
      const box = await drawer.boundingBox();
      return (
        !!box &&
        box.x >= -1 &&
        box.width <= width + 1 &&
        Math.abs(box.x + box.width - width) <= 1
      );
    })
    .toBe(true);
  await expect(drawer.getByRole("heading").first()).toBeInViewport();
  await expect(
    drawer.getByRole("button", { name: "关闭此对话框", exact: true }),
  ).toBeInViewport();
}

test("personal shortcuts can be selected, dragged, cancelled and remembered across reload", async ({
  page,
  context,
}) => {
  await wholesale(context, "demo.owner");
  await page.goto("/");
  await operations(page);
  await shortcuts(page).getByRole("button", { name: "调整入口" }).click();
  const settings = page.getByRole("dialog", { name: "调整常用操作" });
  await settings
    .getByRole("checkbox", { name: "采购开单", exact: true })
    .uncheck();
  await settings
    .getByRole("checkbox", { name: "库存台账", exact: true })
    .check();
  await settings
    .getByRole("button", { name: "上移库存台账", exact: true })
    .click();
  const ordered = settings.locator(".shortcut-order li");
  await expect(ordered.nth(4)).toContainText("库存台账");
  await settings.locator('[data-shortcut="ledger"]').dragTo(ordered.first());
  await expect(ordered.first()).toContainText("库存台账");
  await settings.getByRole("button", { name: "保存入口" }).click();
  await expect(
    shortcuts(page).locator(".wb-shortcuts button").first(),
  ).toContainText("库存台账");
  await expect(
    shortcuts(page).getByRole("button", { name: /采购开单/ }),
  ).toHaveCount(0);
  await page.reload();
  await expect(home(page)).toHaveAttribute("data-home-view", "operations");
  await expect(
    shortcuts(page).locator(".wb-shortcuts button").first(),
  ).toContainText("库存台账");
  await shortcuts(page).getByRole("button", { name: "调整入口" }).click();
  await settings.getByRole("button", { name: "恢复默认入口" }).click();
  await expect(ordered.first()).toContainText("采购开单");
  await settings.getByRole("button", { name: "取消调整" }).click();
  await expect(
    shortcuts(page).locator(".wb-shortcuts button").first(),
  ).toContainText("库存台账");
  await shortcuts(page)
    .getByRole("button", { name: /库存台账/ })
    .click();
  await expect(
    page.getByRole("heading", { name: "库存台账", exact: true }),
  ).toBeVisible();
  await operations(page);
  await shortcuts(page).getByRole("button", { name: "调整入口" }).click();
  mkdirSync("../../outputs/workbench", { recursive: true });
  for (const width of [1440, 375]) {
    await page.setViewportSize({ width, height: 960 });
    await drawerFits(settings, width);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
    await page.screenshot({
      path: `../../outputs/workbench/shortcuts-${width}.png`,
      fullPage: true,
      animations: "disabled",
    });
  }
});

test("shortcut options and draft access follow current permissions", async ({
  page,
  context,
}) => {
  const user = await wholesale(context, "demo.owner");
  await page.route("**/auth/session", async (route) => {
    const response = await route.fetch(),
      json = await response.json();
    json.data.permissions = ["tenant.read", "inventory.read", "purchase.read"];
    await route.fulfill({ response, json });
  });
  await page.addInitScript(
    ({ tenant, actor, entity }) => {
      localStorage.setItem(
        `medinv.home.shortcuts.v1:${tenant}:${actor}:${entity}`,
        JSON.stringify(["purchase-new", "finance", "inventory"]),
      );
    },
    {
      tenant: user.tenant_id,
      actor: user.principal_id,
      entity: user.entities[0].id,
    },
  );
  const draftRequests: string[] = [];
  page.on("request", (r) => {
    if (r.url().includes("/api/v1/document-drafts"))
      draftRequests.push(r.url());
  });
  await page.goto("/");
  await expect(shortcuts(page).locator(".wb-shortcuts button")).toHaveCount(1);
  await expect(shortcuts(page).locator(".wb-shortcuts button")).toContainText(
    "库存查询",
  );
  await expect(
    home(page).getByRole("region", { name: "继续草稿" }),
  ).toHaveCount(0);
  await shortcuts(page).getByRole("button", { name: "调整入口" }).click();
  const settings = page.getByRole("dialog", { name: "调整常用操作" });
  await expect(
    settings.getByRole("checkbox", { name: /开单|结算|销售/ }),
  ).toHaveCount(0);
  await expect(
    settings.getByRole("checkbox", { name: "采购记录", exact: true }),
  ).toBeVisible();
  expect(draftRequests).toEqual([]);
});

test("combined draft browser searches both channels and resumes failed local edits without duplicates", async ({
  page,
  context,
}) => {
  test.setTimeout(120000);
  const user = await wholesale(context, "demo.owner"),
    entity = user.entities[0].id;
  const options = await read(
    context,
    `/api/v1/purchasing/options?entity_id=${entity}&include_skus=false`,
  );
  const title = "接续验收-" + randomUUID().slice(0, 8),
    ids: string[] = [];
  const faults: string[] = [];
  page.on("pageerror", (e) => faults.push(e.message));
  try {
    for (const channel of ["purchase", "sale"]) {
      const id = randomUUID();
      ids.push(id);
      await command(context, user, `/api/v1/document-drafts/${id}`, {
        expected_version: "0",
        channel,
        entity_id: entity,
        title: title + (channel === "purchase" ? "-采购" : "-销售"),
        content: {
          supplier_id: channel === "purchase" ? options.suppliers[0].id : "",
          warehouse_id: options.warehouses[0].id,
          lines: [
            {
              client_key: randomUUID(),
              qty: "12",
              search_text: "尚未选定的商品",
            },
          ],
        },
      });
    }
    await page.goto("/");
    await operations(page);
    await home(page)
      .getByRole("button", { name: "全部草稿 ↗", exact: true })
      .click();
    await browser(page)
      .getByRole("searchbox", { name: "查找草稿" })
      .fill(title);
    await browser(page).getByRole("button", { name: "查询草稿" }).click();
    await expect(browser(page)).toContainText("已存草稿共 2 份");
    await expect(browser(page).locator("tbody tr")).toHaveCount(2);
    await expect(browser(page)).toContainText(options.suppliers[0].name);
    await expect(browser(page)).toContainText(options.warehouses[0].name);
    await browser(page).getByLabel("草稿类型").selectOption("sale");
    await expect(browser(page).locator("tbody tr")).toHaveCount(1);
    await expect(browser(page).locator("tbody tr")).toContainText(
      title + "-销售",
    );
    await browser(page).getByLabel("草稿类型").selectOption("all");
    await expect(browser(page).locator("tbody tr")).toHaveCount(2);
    mkdirSync("../../outputs/workbench", { recursive: true });
    for (const width of [1440, 375]) {
      await page.setViewportSize({ width, height: 960 });
      await drawerFits(browser(page), width);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
      ).toBe(true);
      await page.screenshot({
        path: `../../outputs/workbench/drafts-${width}.png`,
        fullPage: true,
        animations: "disabled",
      });
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await browser(page)
      .locator("tbody tr")
      .filter({ hasText: title + "-采购" })
      .getByRole("button", { name: "继续录入 ↗" })
      .click();
    await expect(page).toHaveURL((url) => url.pathname.endsWith(ids[0]!));
    const editor = page.locator(".document-editor:visible");
    await expect(editor.getByLabel("采购数量", { exact: true })).toHaveValue(
      "12",
    );
    let fail = true;
    await page.route(`**/api/v1/document-drafts/${ids[0]}`, (route) => {
      if (fail && route.request().method() === "POST")
        return route.fulfill({
          status: 503,
          json: {
            error: { code: "TEST_OFFLINE", message: "测试草稿暂不能保存" },
          },
        });
      return route.continue();
    });
    await editor.getByLabel("草稿备注").fill(title + "-未存修改");
    await editor.getByLabel("采购数量", { exact: true }).fill("27");
    await editor.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect(editor.getByRole("alert")).toContainText("测试草稿暂不能保存");
    const tabs = page.locator(".editor-tab:not(.module-tab):not(.record-tab)");
    await expect(tabs).toHaveCount(1);
    await operations(page);
    const recent = home(page).getByRole("region", {
      name: "继续草稿",
      exact: true,
    });
    await expect(recent.locator(".wb-draft").first()).toContainText(
      title + "-未存修改",
    );
    await expect(recent.locator(".wb-draft").first()).toContainText("保存失败");
    await recent.getByRole("button", { name: "全部草稿 ↗" }).click();
    await expect(browser(page).locator(".draft-pending")).toContainText(
      "当前窗口待保存 · 1份",
    );
    await browser(page).locator(".draft-pending button").click();
    await expect(tabs).toHaveCount(1);
    await expect(editor.getByLabel("采购数量", { exact: true })).toHaveValue(
      "27",
    );
    await expect(editor.getByLabel("草稿备注")).toHaveValue(
      title + "-未存修改",
    );
    fail = false;
    await editor.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect(editor.locator(".editor-actions")).toContainText("草稿已保存");
    await editor
      .getByRole("button", { name: "返回采购记录", exact: true })
      .click();
    await page.getByRole("button", { name: "我的草稿", exact: true }).click();
    await expect(browser(page).getByLabel("草稿类型")).toHaveValue("purchase");
    await browser(page)
      .getByRole("searchbox", { name: "查找草稿" })
      .fill(title);
    await browser(page).getByRole("button", { name: "查询草稿" }).click();
    await expect(browser(page).locator("tbody tr")).toHaveCount(1);
    await expect(browser(page).locator("tbody tr")).toContainText(
      title + "-未存修改",
    );
    const saved = await read(context, `/api/v1/document-drafts/${ids[0]}`);
    expect(saved.content.lines[0].qty).toBe("27");
    expect(faults).toEqual([]);
  } finally {
    await page.close();
    for (const id of ids) {
      const saved = await read(context, `/api/v1/document-drafts/${id}`);
      if (saved.state === "EDITING")
        await command(context, user, `/api/v1/document-drafts/${id}/discard`, {
          expected_version: saved.version,
        });
    }
  }
});
