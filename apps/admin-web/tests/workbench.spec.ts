import { test, expect, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { wholesale, read } from "./business-helpers";

const home = (page: Page) =>
  page.getByRole("region", { name: "企业工作台方案", exact: true });
const picker = (page: Page) => home(page).getByLabel("工作台方案");
async function overview(page: Page) {
  await page.getByRole("button", { name: "企业工作台", exact: true }).click();
}

test("three workbenches use real data, retain periods and preferences, and resume the same draft", async ({
  page,
  context,
}) => {
  test.setTimeout(90000);
  const user = await wholesale(context, "demo.owner");
  const faults: string[] = [];
  page.on("pageerror", (e) => faults.push(e.message));
  let snapshots = 0;
  page.on("request", (r) => {
    if (r.url().includes("/api/v1/workbench/snapshot")) snapshots++;
  });
  await page.goto("/");
  await expect(
    home(page).getByRole("heading", { name: "经营总览", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".wb-chart-reading:visible")).toBeVisible();
  await expect(page.locator(".wb-heading:visible")).toHaveCount(1);
  const initialSnapshots = snapshots;
  const response = page.waitForResponse(
    (r) =>
      r.url().includes("/workbench/period?") &&
      r.url().includes("days=30") &&
      r.status() === 200,
  );
  await home(page).getByRole("button", { name: "近30日", exact: true }).click();
  const period = (await (await response).json()).data;
  await expect(
    home(page)
      .getByRole("region", { name: "期间经营指标" })
      .locator("strong")
      .first(),
  ).toHaveText(
    period.current.sales === null
      ? "—"
      : Number(period.current.sales).toLocaleString("en-US", {
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        }),
  );
  expect(period.series).toHaveLength(30);
  expect(snapshots).toBe(initialSnapshots);
  await home(page).getByRole("button", { name: "查看前一日" }).click();
  await expect(page.locator(".wb-chart-reading:visible")).toContainText(
    period.series.at(-2).day,
  );
  await home(page).getByRole("button", { name: "回款", exact: true }).click();
  await expect(page.getByRole("img", { name: /回款逐日趋势/ })).toBeVisible();
  await picker(page).selectOption("risk");
  await expect(
    home(page).getByRole("heading", { name: "风险总览" }),
  ).toBeVisible();
  await expect(
    home(page).getByRole("button", { name: "查看全部事项" }),
  ).toBeEnabled();
  await home(page).getByRole("button", { name: "查看全部事项" }).click();
  const drawer = page.getByRole("dialog", { name: "全部已接入风险事项" });
  await expect(drawer).toBeVisible();
  await expect(drawer.locator("details[open]")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await picker(page).selectOption("insights");
  await expect(
    home(page).getByRole("button", { name: "近30日", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "界面与操作设置" }).click();
  await page
    .locator(".home-view-choices")
    .getByRole("button", { name: /高效业务台/ })
    .click();
  await overview(page);
  await expect(home(page)).toHaveAttribute("data-home-view", "operations");
  await page.reload();
  await expect(home(page)).toHaveAttribute("data-home-view", "operations");
  await expect(
    home(page).getByRole("region", { name: "进销存业务待办" }),
  ).toBeVisible();
  await home(page).getByRole("button", { name: "新建采购单" }).click();
  const note = "工作台草稿接续-" + Date.now();
  await page.getByLabel("草稿备注").fill(note);
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page.locator(".document-editor:visible")).toContainText(
    "草稿已保存",
  );
  const tabs = await page
    .locator(".editor-tab:not(.module-tab):not(.record-tab)")
    .count();
  await overview(page);
  await home(page)
    .getByRole("button", { name: new RegExp(note) })
    .click();
  await expect(page.getByLabel("草稿备注")).toHaveValue(note);
  expect(
    await page.locator(".editor-tab:not(.module-tab):not(.record-tab)").count(),
  ).toBe(tabs);
  const drafts = await read(
    context,
    `/api/v1/document-drafts?entity_id=${user.entities[0].id}&channel=purchase`,
  );
  expect(drafts.items.filter((d: any) => d.title === note)).toHaveLength(1);
  expect(faults).toEqual([]);
});

test("all schemes fit desktop, tablet and small phones; menu survives pointer gap, resize and Escape", async ({
  page,
  context,
}) => {
  test.setTimeout(120000);
  await wholesale(context, "demo.owner");
  await page.goto("/");
  await expect(page.locator(".wb-chart-reading:visible")).toBeVisible();
  mkdirSync("../../outputs/workbench", { recursive: true });
  for (const width of [1440, 1280, 1024, 768, 375, 320]) {
    await page.setViewportSize({ width, height: width > 1000 ? 1000 : 860 });
    for (const view of ["insights", "risk", "operations"]) {
      await picker(page).selectOption(view);
      await expect(home(page)).toHaveAttribute("data-home-view", view);
      await expect(
        home(page).getByRole("button", {
          name:
            view === "insights"
              ? "刷新经营数据"
              : view === "risk"
                ? "刷新风险数据"
                : "刷新业务待办",
          exact: true,
        }),
      ).toBeEnabled({ timeout: 20000 });
      await expect(home(page).locator(".wb-error:visible")).toHaveCount(0);
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth + 1,
      );
      expect(overflow, `${width} ${view} page overflow`).toBe(false);
      if (width === 1440 || width === 375)
        await page.screenshot({
          path: `../../outputs/workbench/${view}-${width}.png`,
          fullPage: true,
        });
    }
  }
  await page.setViewportSize({ width: 1280, height: 900 });
  const purchase = page.locator('[data-menu-group="purchase"]');
  await purchase.hover();
  const popup = page.getByRole("navigation", { name: "采购功能", exact: true });
  await expect(popup).toBeVisible();
  await purchase.click();
  await expect(popup).toBeVisible();
  const box = (await popup.boundingBox())!;
  await page.mouse.move(box.x + 25, box.y - 2);
  await page.waitForTimeout(100);
  await page.mouse.move(box.x + 25, box.y + 25);
  await expect(popup).toBeVisible();
  await page.setViewportSize({ width: 1024, height: 768 });
  await expect(popup).toBeVisible();
  await popup.getByRole("button").first().focus();
  await page.keyboard.press("Escape");
  await expect(popup).not.toBeVisible();
  await expect(purchase).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(popup.getByRole("button").first()).toBeFocused();
  await page.keyboard.press("Escape");
  await page.evaluate(() => {
    document.documentElement.style.zoom = "1.25";
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth + 1,
    ),
  ).toBe(false);
});

test("read failures remain explicit and a retry restores live financial data", async ({
  page,
  context,
}) => {
  await wholesale(context, "demo.owner");
  await page.route("**/api/v1/workbench/period?**", (r) =>
    r.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({
        error: { code: "UNAVAILABLE", message: "测试读取暂不可用" },
      }),
    }),
  );
  await page.goto("/");
  await expect(home(page).getByRole("alert")).toContainText("测试读取暂不可用");
  await expect(
    home(page).getByRole("region", { name: "期间经营指标" }),
  ).toContainText("等待真实统计");
  await page.unroute("**/api/v1/workbench/period?**");
  await home(page).getByRole("button", { name: "重试", exact: true }).click();
  await expect(page.locator(".wb-chart-reading:visible")).toBeVisible();
  await expect(home(page).getByRole("alert")).toHaveCount(0);
});

test("permission fallback hides financial views and never requests financial projections", async ({
  page,
  context,
}) => {
  const user = await wholesale(context, "demo.owner");
  // UI-only narrowed session. Real backend permission enforcement is covered by integration tests.
  await page.route("**/auth/session", async (route) => {
    const response = await route.fetch(),
      body = await response.json();
    body.data.permissions = ["tenant.read", "inventory.read", "purchase.read"];
    await route.fulfill({ response, json: body });
  });
  await page.addInitScript(
    ({ tenant, actor, entity }) => {
      localStorage.setItem(
        `medinv.home.v1:${tenant}:${actor}:${entity}`,
        "insights",
      );
    },
    {
      tenant: user.tenant_id,
      actor: user.principal_id,
      entity: user.entities[0].id,
    },
  );
  const projections: string[] = [];
  page.on("request", (r) => {
    if (/\/workbench\/(period|snapshot|attention)/.test(r.url()))
      projections.push(r.url());
  });
  await page.goto("/");
  await expect(home(page)).toHaveAttribute("data-home-view", "operations");
  await expect(picker(page).locator("option")).toHaveCount(2);
  await expect(
    home(page).getByRole("button", { name: "新建采购单" }),
  ).toHaveCount(0);
  await expect(
    home(page).getByRole("button", { name: "刷新业务待办" }),
  ).toBeEnabled();
  expect(projections).toEqual([]);
  await page.getByRole("button", { name: "界面与操作设置" }).click();
  await expect(page.locator(".home-view-choices button")).toHaveCount(2);
});
