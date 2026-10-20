import { test, expect, type BrowserContext, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { command, read, wholesale } from "./business-helpers";
import { defaultEntryColumns, type EntryChannel } from "../src/documentEntry";
import { entryResumeKey } from "../src/entryResume";

async function draft(
  context: BrowserContext,
  owner: any,
  channel: EntryChannel,
) {
  const id = randomUUID();
  const body = {
    expected_version: "0",
    channel,
    entity_id: owner.entities[0].id,
    title: "录入位置验收",
    source_order_id: null,
    source_version: null,
    content: {
      supplier_id: "",
      customer_id: "",
      warehouse_id: "",
      delivery_address: "",
      minimum_shelf_days: "0",
      credit_days: "30",
      reason: "",
      lines: Array.from({ length: 20 }, (_, i) => ({
        client_key: randomUUID(),
        sku_id: "",
        packaging_id: "",
        qty: String(i + 1),
        unit_price: "1.234567",
        tax_rate: "0.13",
        search_text: `待确认商品${i + 1}`,
      })),
    },
  };
  const saved = await command(
    context,
    owner,
    `/api/v1/document-drafts/${id}`,
    body,
  );
  return {
    id,
    body,
    saved,
    url: `/workspace/${channel === "purchase" ? "purchasing" : "selling"}/drafts/${id}?tenant=${owner.tenant_id}`,
    key: entryResumeKey(
      owner.tenant_id,
      owner.principal_id,
      body.entity_id,
      id,
    ),
  };
}
const editor = (page: Page) => page.locator(".document-editor:visible");
const price = (page: Page, key: string) =>
  editor(page).locator(`[data-row-key="${key}"] [data-col="price"] input`);
async function visibleTarget(page: Page, key: string) {
  const input = price(page, key);
  await expect(input).toBeFocused();
  await expect
    .poll(() =>
      input.evaluate((el) => {
        const bounds = el.getBoundingClientRect();
        const grid = el.closest(".document-entry")!;
        const header = grid.querySelector("thead")!.getBoundingClientRect();
        const summary = grid
          .querySelector(".entry-summary")!
          .getBoundingClientRect();
        return (
          bounds.top >= header.bottom &&
          bounds.bottom < summary.top &&
          el ===
            document.elementFromPoint(
              bounds.x + bounds.width / 2,
              bounds.y + bounds.height / 2,
            )
        );
      }),
    )
    .toBe(true);
}

for (const channel of ["purchase", "sale"] as const) {
  test(`${channel} resumes the stable row and field across tabs, saved drafts and reload without saving view changes`, async ({
    page,
    context,
  }) => {
    test.setTimeout(90000);
    const owner = await wholesale(context, "demo.owner");
    const f = await draft(context, owner, channel);
    const key = f.body.content.lines[18]!.client_key;
    await page.setViewportSize({ width: 1366, height: 768 });
    await page.goto(f.url);
    await price(page, key).focus();
    await visibleTarget(page, key);
    await editor(page)
      .getByRole("button", {
        name: `返回${channel === "purchase" ? "采购" : "销售"}记录`,
        exact: true,
      })
      .click();
    await page
      .getByRole("button", {
        name: channel === "purchase" ? "新建采购订单" : "新建销售单",
        exact: true,
      })
      .click();
    await editor(page).getByLabel("草稿备注").fill("另一张草稿");
    await page
      .locator(".editor-tab")
      .getByRole("button", { name: new RegExp(`单 · ${f.id.slice(0, 4)}`) })
      .click();
    await visibleTarget(page, key);
    await price(page, key).fill("12.345678");
    await editor(page)
      .getByRole("button", { name: "保存草稿", exact: true })
      .click();
    await expect(editor(page).locator(".editor-actions")).toContainText(
      "草稿已保存",
    );
    const saved = await read(context, `/api/v1/document-drafts/${f.id}`);
    await page.reload();
    await visibleTarget(page, key);
    await expect(price(page, key)).toHaveValue("12.345678");
    await expect(
      editor(page)
        .locator(`[data-row-key="${key}"]`)
        .getByRole("combobox", { name: "商品", exact: true }),
    ).toHaveValue("待确认商品19");
    // Moving focus only must not dirty the draft or issue an autosave.
    await editor(page).getByLabel("草稿备注").focus();
    await expect(editor(page).locator(".editor-actions")).toContainText(
      "草稿已保存",
    );
    await page.reload();
    await expect(editor(page).getByLabel("草稿备注")).toBeFocused();
    const unchanged = await read(context, `/api/v1/document-drafts/${f.id}`);
    expect(unchanged.version).toBe(saved.version);
    expect(unchanged.content).toEqual(saved.content);
    await page.screenshot({
      path: `../../artifacts/entry-resume-${channel}.png`,
      animations: "disabled",
    });
    // Closing a saved editor and opening it again in the same browser tab also resumes.
    await page
      .getByRole("button", {
        name: `关闭单据 ${f.id.slice(0, 4)}`,
        exact: true,
      })
      .click();
    await page.goto(f.url);
    await expect(editor(page).getByLabel("草稿备注")).toBeFocused();
  });
}

test("reordered and deleted draft rows resolve by identity; hidden columns fall back without changing the template", async ({
  page,
  context,
}) => {
  const owner = await wholesale(context, "demo.owner");
  const f = await draft(context, owner, "purchase");
  const key = f.body.content.lines[18]!.client_key;
  await page.goto(f.url);
  await price(page, key).focus();
  const reordered = [...f.body.content.lines].reverse();
  let saved = await command(context, owner, `/api/v1/document-drafts/${f.id}`, {
    ...f.body,
    expected_version: f.saved.version,
    content: { ...f.body.content, lines: reordered },
  });
  await page.reload();
  await visibleTarget(page, key);
  expect(
    await price(page, key).evaluate(
      (el) => el.closest("tr")!.querySelector("th")!.textContent,
    ),
  ).toBe("2");
  const remaining = reordered.filter((r) => r.client_key !== key);
  saved = await command(context, owner, `/api/v1/document-drafts/${f.id}`, {
    ...f.body,
    expected_version: saved.version,
    content: { ...f.body.content, lines: remaining },
  });
  await page.reload();
  await visibleTarget(page, remaining[1]!.client_key);
  await editor(page)
    .locator(".entry-row")
    .nth(1)
    .getByLabel("税率（小数）", { exact: true })
    .focus();
  await page.route(
    "**/api/v1/document-columns/orders/editor?*",
    async (route) => {
      const response = await route.fetch(),
        body = await response.json();
      body.data.columns = body.data.columns.map((c: any) => ({
        ...c,
        visible: c.key === "tax" ? false : c.visible,
      }));
      await route.fulfill({ response, json: body });
    },
  );
  await page.reload();
  await expect(
    editor(page)
      .locator(".entry-row")
      .nth(1)
      .getByRole("combobox", { name: "商品", exact: true }),
  ).toBeFocused();
  await expect(editor(page).locator('[data-col="tax"]')).toHaveCount(0);
  expect((await read(context, `/api/v1/document-drafts/${f.id}`)).version).toBe(
    saved.version,
  );
});

test("late options never steal focus after the user starts another action", async ({
  page,
  context,
}) => {
  const owner = await wholesale(context, "demo.owner");
  const f = await draft(context, owner, "purchase");
  await page.goto(f.url);
  await price(page, f.body.content.lines[18]!.client_key).focus();
  let release = () => {};
  const hold = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/v1/purchasing/options?**", async (route) => {
    await hold;
    await route.continue();
  });
  try {
    await page.reload();
    const save = editor(page).getByRole("button", {
      name: "保存草稿",
      exact: true,
    });
    await save.focus();
    release();
    await expect(
      editor(page).getByRole("combobox", { name: "供应商", exact: true }),
    ).toBeEnabled();
    await expect(save).toBeFocused();
  } finally {
    release();
  }
});

test("position recovery rejects stale or malformed cache and does not use another account's position", async ({
  page,
  context,
}) => {
  const owner = await wholesale(context, "demo.owner");
  const f = await draft(context, owner, "purchase");
  await page.goto(f.url);
  await price(page, f.body.content.lines[18]!.client_key).focus();
  const cached = await page.evaluate(
    (key) => sessionStorage.getItem(key),
    f.key,
  );
  expect(cached).toBeTruthy();
  for (const scenario of ["stale", "invalid", "other"]) {
    await page.evaluate(
      ({ key, original, scenario }) => {
        const value = JSON.parse(original!);
        sessionStorage.removeItem(key);
        if (scenario === "stale") value.time = Date.now() - 86400001;
        if (scenario === "invalid")
          value.position = {
            area: "line",
            key: '"] input',
            index: 99,
            column: "price",
          };
        sessionStorage.setItem(
          scenario === "other"
            ? key.replace(
                "medinv.entry-position.v1:",
                "medinv.entry-position.v1:other:",
              )
            : key,
          JSON.stringify(value),
        );
      },
      { key: f.key, original: cached, scenario },
    );
    await page.reload();
    await expect(
      editor(page).getByRole("combobox", { name: "供应商", exact: true }),
    ).toBeEnabled();
    await expect(
      editor(page).getByRole("button", { name: "列设置", exact: true }),
    ).toBeEnabled();
    await expect(
      price(page, f.body.content.lines[18]!.client_key),
    ).not.toBeFocused();
    await expect(editor(page).locator(".editor-actions")).toContainText(
      "草稿已保存",
    );
  }
});
