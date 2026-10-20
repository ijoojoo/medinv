import { test, expect, type Page, type BrowserContext } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { wholesale, read, nav } from "./business-helpers";
import type { WorkQueueItem } from "../src/workQueue";

const queue = (page: Page) =>
  page.getByRole("region", { name: "进销存业务待办", exact: true });
const home = (page: Page) =>
  page.getByRole("region", { name: "企业工作台方案", exact: true });
async function overview(page: Page) {
  await page.getByRole("button", { name: "企业工作台", exact: true }).click();
  await home(page)
    .getByRole("combobox", { name: "工作台方案", exact: true })
    .selectOption("operations");
}
async function find(page: Page, text: string) {
  await queue(page).getByRole("searchbox", { name: "查找待办" }).fill(text);
  await queue(page)
    .getByRole("button", { name: "查询待办", exact: true })
    .click();
  await expect(queue(page).locator("tbody tr").first()).toBeVisible();
}
async function purchaseSources(context: BrowserContext, entity: string) {
  // Read existing pending documents; these navigation tests do not mutate business records.
  const result = await read(
    context,
    `/api/v1/work-queue?entity_id=${entity}&kind=purchase_receive&limit=50`,
  );
  const unique = [
    ...new Map<string, WorkQueueItem>(
      result.items.map((item: WorkQueueItem) => [item.target_id, item]),
    ).values(),
  ];
  expect(
    unique.length,
    "Trial database contains at least two pending purchase documents",
  ).toBeGreaterThanOrEqual(2);
  return unique;
}

test("each original tab retains its own workbench filters and leaves the purchase list intact", async ({
  page,
  context,
}) => {
  const user = await wholesale(context, "demo.owner");
  const [first, second] = await purchaseSources(context, user.entities[0].id);
  const faults: string[] = [];
  page.on("pageerror", (e) => faults.push(e.message));
  await page.goto("/");
  await nav(page, "采购入库");
  const listSearch = page.getByPlaceholder("单号、供应商名称或编码");
  await listSearch.fill("保留记录页条件");
  await page.getByRole("button", { name: "查询单据", exact: true }).click();
  await overview(page);
  await find(page, first!.number);
  await queue(page).getByRole("button", { name: "查看原单 ↗" }).first().click();
  await expect(page.locator(".document-number:visible").first()).toHaveText(
    first!.number,
  );
  await expect(page.locator(".record-tab")).toHaveCount(1);
  mkdirSync("../../outputs/workbench", { recursive: true });
  await page.screenshot({
    path: "../../outputs/workbench/linked-original-1440.png",
    animations: "disabled",
  });
  const firstTab = page
    .getByRole("navigation", { name: "工作标签" })
    .getByRole("button", { name: first!.number, exact: true });
  await page.getByRole("button", { name: "返回业务待办", exact: true }).click();
  await expect(queue(page).getByLabel("查找待办")).toHaveValue(first!.number);
  await expect(
    queue(page).getByRole("button", { name: "查看原单 ↗" }).first(),
  ).toBeFocused();
  await find(page, second!.number);
  await queue(page).getByRole("button", { name: "查看原单 ↗" }).first().click();
  await expect(page.locator(".record-tab")).toHaveCount(2);
  await expect(page.locator(".document-number:visible").first()).toHaveText(
    second!.number,
  );
  await firstTab.click();
  await expect(page.locator('[aria-label="原单待办来源"]')).toContainText(
    first!.number,
  );
  await page.getByRole("button", { name: "返回业务待办", exact: true }).click();
  await expect(queue(page).getByLabel("查找待办")).toHaveValue(first!.number);
  const returnButton = queue(page)
    .getByRole("button", { name: "查看原单 ↗" })
    .first();
  await expect(returnButton).toBeFocused();
  await returnButton.press("Enter");
  await expect(page.locator(".record-tab")).toHaveCount(2);
  await page
    .getByRole("button", { name: "关闭" + first!.number, exact: true })
    .click();
  await expect(home(page)).toBeVisible();
  await expect(queue(page).getByLabel("查找待办")).toHaveValue(first!.number);
  await expect(page.locator(".record-tab")).toHaveCount(1);
  await nav(page, "采购入库");
  await expect(listSearch).toHaveValue("保留记录页条件");
  await expect(page.locator('[aria-label="原单待办来源"]')).toHaveCount(0);
  expect(faults).toEqual([]);
});

test("returning from a disappeared last-page item clamps the page and focuses remaining work", async ({
  page,
  context,
}) => {
  const user = await wholesale(context, "demo.owner");
  const [source] = await purchaseSources(context, user.entities[0].id);
  const rows = Array.from({ length: 51 }, (_, index) => ({
    ...source!,
    item_id: randomUUID(),
    number: `分页验收-${String(index + 1).padStart(2, "0")}`,
  }));
  let total = 51;
  const queries: { offset: number; limit: number }[] = [];
  // Simulate only queue pagination; original documents, login and permissions remain real.
  await page.route("**/api/v1/work-queue?**", (route) => {
    const params = new URL(route.request().url()).searchParams;
    const offset = Number(params.get("offset")),
      limit = Number(params.get("limit"));
    queries.push({ offset, limit });
    return route.fulfill({
      json: {
        data: {
          items: rows.slice(offset, Math.min(total, offset + limit)),
          total,
          has_more: offset + limit < total,
          counts: { purchase_receive: total },
          kinds: [{ code: "purchase_receive", name: "采购待收货" }],
          priority_count: 0,
          as_of: new Date().toISOString(),
        },
      },
    });
  });
  await page.goto("/");
  await overview(page);
  await queue(page).getByRole("button", { name: "查看全部待办 ↗" }).click();
  await expect(queue(page).locator("tbody tr")).toHaveCount(50);
  const lowerRow = queue(page)
    .getByRole("button", { name: "查看原单 ↗" })
    .nth(40);
  await lowerRow.click();
  await expect(page.locator(".document-number:visible").first()).toHaveText(
    source!.number,
  );
  await page.getByRole("button", { name: "返回业务待办", exact: true }).click();
  await expect(lowerRow).toBeFocused();
  await expect(lowerRow).toBeInViewport();
  expect(
    await page.locator(".main-shell").evaluate((el) => el.scrollTop),
  ).toBeGreaterThan(500);
  await queue(page)
    .getByRole("button", { name: "下一页待办", exact: true })
    .click();
  await expect(queue(page).locator("tbody tr")).toHaveCount(1);
  await queue(page).getByRole("button", { name: "查看原单 ↗" }).click();
  await expect(page.locator(".document-number:visible").first()).toHaveText(
    source!.number,
  );
  total = 50;
  await page.getByRole("button", { name: "返回业务待办", exact: true }).click();
  await expect(queue(page).locator("tbody tr")).toHaveCount(50);
  await expect(
    queue(page).getByRole("button", { name: "上一页待办", exact: true }),
  ).toBeDisabled();
  await expect(
    queue(page).getByRole("button", { name: "下一页待办", exact: true }),
  ).toBeDisabled();
  await expect(
    queue(page).getByRole("button", { name: "查看原单 ↗" }).first(),
  ).toBeFocused();
  await expect(queue(page).getByRole("status")).toContainText(
    "原事项已不在本页",
  );
  expect(queries.slice(-2)).toEqual([
    { offset: 50, limit: 50 },
    { offset: 0, limit: 50 },
  ]);
});

test("risk-origin documents return to the original drawer and search, rather than resetting the view", async ({
  page,
  context,
}) => {
  const user = await wholesale(context, "demo.owner");
  const [source] = await purchaseSources(context, user.entities[0].id);
  // UI-only risk source. Risk classifications and counts have separate real integration coverage.
  const item = { ...source!, kind: "purchase_handback" };
  const risks = {
    items: [item],
    total: 1,
    total_open: 1,
    has_more: false,
    counts: { purchase_handback: 1 },
    kinds: [{ code: "purchase_handback", name: "拒收待退回" }],
    groups: [
      {
        code: "receipt",
        name: "采购拒收交还",
        count: 1,
        kinds: ["purchase_handback"],
      },
    ],
    as_of: new Date().toISOString(),
  };
  await page.route("**/api/v1/workbench/risks?**", (route) =>
    route.fulfill({ json: { data: risks } }),
  );
  await page.goto("/");
  await home(page)
    .getByRole("combobox", { name: "工作台方案", exact: true })
    .selectOption("risk");
  await home(page).getByRole("button", { name: "查看全部事项" }).click();
  const drawer = page.getByRole("dialog", {
    name: "全部已接入风险事项",
    exact: true,
  });
  await drawer
    .getByRole("searchbox", { name: "检索风险事项" })
    .fill(source!.number);
  await drawer.getByRole("button", { name: "查询事项", exact: true }).click();
  await drawer.locator("summary").click();
  await drawer.getByRole("button", { name: "查看原单并办理 ↗" }).click();
  await expect(page.locator(".document-number:visible").first()).toHaveText(
    source!.number,
  );
  await expect(
    page.getByRole("button", { name: "返回业务待办", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "返回风险事项", exact: true }).click();
  await expect(home(page)).toHaveAttribute("data-home-view", "risk");
  await expect(drawer).toBeVisible();
  await expect(
    drawer.getByRole("searchbox", { name: "检索风险事项" }),
  ).toHaveValue(source!.number);
  await expect(drawer.locator("summary")).toBeFocused();
  await expect(drawer.getByRole("status")).toContainText("风险事项已刷新");
});
