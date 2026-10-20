import { test, expect } from "@playwright/test";
import { wholesale, read, nav } from "./business-helpers";

for (const [channel, module, title, label, key] of [
  ["purchase", "采购入库", "新建采购单", "供应商", "supplier_id"],
  ["sale", "批发销售", "新建销售单", "客户", "customer_id"],
] as const) {
  test(`${channel} lookup defaults to first result, confirms on Enter and rejects late confirmation`, async ({
    page,
    context,
  }) => {
    test.setTimeout(75000);
    const owner = await wholesale(context, "demo.owner");
    const real = await read(
      context,
      `/api/v1/catalog/entry-parties?entity_id=${owner.entities[0].id}&channel=${channel}`,
    );
    expect(real.items.length).toBeGreaterThanOrEqual(2);
    const rows = real.items.slice(0, 2);
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    let arrived: () => void = () => {},
      release: () => void = () => {},
      completed: () => void = () => {};
    let gate = Promise.resolve();
    // Keep real identities and eligibility; control only keyword timing and result order.
    await page.route("**/api/v1/catalog/entry-parties?*", async (route) => {
      const q = new URL(route.request().url()).searchParams;
      if (q.get("channel") !== channel || q.has("party_id"))
        return route.continue();
      const query = q.get("search");
      if (query === "即时" || query === "取消") {
        arrived();
        await gate;
      }
      if (["多条", "即时", "取消", "无结果"].includes(query || "")) {
        await route.fulfill({
          json: {
            data: { items: query === "无结果" ? [] : rows, has_more: false },
          },
        });
        if (query === "即时" || query === "取消") completed();
      } else await route.continue();
    });
    await page.goto("/");
    await nav(page, module);
    await page.getByRole("button", { name: title === "新建采购单" ? "新建采购订单" : title, exact: true }).click();
    const editor = page.locator(".document-editor:visible");
    const input = editor.getByRole("combobox", { name: label, exact: true });
    const popup = page.locator(".party-lookup-popover:visible");
    const chosen = popup.getByRole("option", { selected: true });
    await input.fill("多条");
    await expect(popup.getByRole("option")).toHaveCount(2);
    await expect(chosen).toHaveAttribute("data-party", rows[0].id);
    await expect(input).toHaveValue("多条");
    expect(
      await input.evaluate((e: HTMLInputElement) => e.validity.valid),
    ).toBe(false);
    await page.screenshot({
      path: `../../outputs/dictionary-lookup/${channel}-default-first.png`,
      fullPage: true,
    });
    await input.press("Enter");
    await expect(input).toHaveValue(rows[0].name);
    await expect(popup).toHaveCount(0);
    await editor.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect(editor.locator(".editor-actions")).toContainText("草稿已保存");
    const id = new URL(page.url()).pathname.split("/").pop();
    expect(
      (await read(context, `/api/v1/document-drafts/${id}`)).content[key],
    ).toBe(rows[0].id);

    await input.fill("多条");
    await expect(chosen).toHaveAttribute("data-party", rows[0].id);
    await input.press("ArrowDown");
    await expect(chosen).toHaveAttribute("data-party", rows[1].id);
    await input.press("Enter");
    await expect(input).toHaveValue(rows[1].name);

    const start = () => {
      const reached = new Promise<void>((resolve) => {
        arrived = resolve;
      });
      const handled = new Promise<void>((resolve) => {
        completed = resolve;
      });
      gate = new Promise<void>((resolve) => {
        release = resolve;
      });
      return { reached, handled };
    };
    let delay = start();
    try {
      await input.fill("即时");
      await input.press("Enter");
      await delay.reached;
      await expect(input).toHaveValue("即时");
    } finally {
      release();
    }
    await delay.handled;
    await expect(input).toHaveValue(rows[0].name);
    await expect(popup).toHaveCount(0);

    delay = start();
    try {
      await input.fill("取消");
      await input.press("Enter");
      await delay.reached;
      await input.press("Escape");
    } finally {
      release();
    }
    await delay.handled;
    await expect(popup).toHaveCount(0);
    await expect(input).toHaveValue("取消");
    await input.fill("无结果");
    await expect(popup).toContainText("未找到匹配单位");
    await input.press("Enter");
    await expect(input).toHaveValue("无结果");
    await expect(popup.getByRole("option")).toHaveCount(0);
    await editor.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect(editor.locator(".editor-actions")).toContainText("草稿已保存");
    expect(
      (await read(context, `/api/v1/document-drafts/${id}`)).content[key],
    ).toBe("");
    expect(errors).toEqual([]);
  });
}
