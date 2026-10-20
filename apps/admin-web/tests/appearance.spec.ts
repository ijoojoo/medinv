import { read, command, entryTemplatePath } from "./entry-template-helpers";
import { chooseParty } from "./business-helpers";
import { test, expect } from "@playwright/test";
import { wholesale, nav, stockedCustomer } from "./business-helpers";
import { defaultEntryColumns } from "../src/documentEntry";

test("colored headers and compact entry preserve data, warnings, preferences and reachable navigation", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(120000);
  const owner = await wholesale(context, "demo.owner"),
    reviewer = await browser.newContext();
  const path = entryTemplatePath(true, owner.entities[0].id),
    original = await read(context, path);
  await command(context, owner, path, {
    expected_version: original.version,
    template: {
      schema_version: 2,
      columns: defaultEntryColumns().map(({ required, editable, ...c }) => c),
    },
  });
  const faults: string[] = [];
  page.on("pageerror", (e) => faults.push(e.message));
  try {
    const checker = await wholesale(reviewer, "demo.quality"),
      f = await stockedCustomer(context, owner, reviewer, checker);
    const lookup = await read(
      context,
      `/api/v1/catalog/entry-products?entity_id=${f.entity}&channel=purchase&sku_id=${f.product.sku_id}`,
    );
    const code = lookup.items[0].code;
    await page.setViewportSize({ width: 1366, height: 768 });
    await page.goto("/");
    const theme = page.getByRole("button", {
      name: "界面与操作设置",
      exact: true,
    });
    const setTheme = async (value: string) => {
      const editorTab = page
        .locator(".editor-tab.active")
        .getByRole("button")
        .first();
      const tabName = await editorTab.getAttribute("title");
      await theme.click();
      await page
        .locator(".appearance-settings")
        .getByRole("button", {
          name: (
            { blue: "清朗蓝", teal: "青碧", violet: "雅紫" } as Record<
              string,
              string
            >
          )[value],
          exact: true,
        })
        .click();
      await page.getByTitle(tabName!, { exact: true }).click();
    };
    await expect(page.locator("html")).toHaveAttribute("data-theme", "teal");
    const primary = page.getByRole("navigation", {
      name: "主要功能",
      exact: true,
    });
    expect(
      await primary
        .getByRole("button")
        .first()
        .evaluate((el) => parseFloat(getComputedStyle(el).fontSize)),
    ).toBe(16);
    await nav(page, "采购入库");
    await page
      .getByRole("button", { name: "新建采购订单", exact: true })
      .click();
    const editor = page.getByRole("region", {
      name: "新建采购单",
      exact: true,
    });
    await chooseParty(
      editor.getByRole("combobox", { name: "供应商", exact: true }),
      f.party.id,
    );
    await editor.getByRole("button", { name: "粘贴多行", exact: true }).click();
    const dialog = page.getByRole("dialog", {
      name: "粘贴多行商品",
      exact: true,
    });
    await dialog
      .getByLabel("多行商品数据", { exact: true })
      .fill(Array.from({ length: 12 }, () => `${code}\t2\t3`).join("\n"));
    await dialog
      .getByRole("button", { name: "校验并预览", exact: true })
      .click();
    await dialog
      .getByRole("button", { name: "确认填入 12 行", exact: true })
      .click();
    const rows = editor.locator(".entry-row"),
      density = editor.getByLabel("录入密度", { exact: true });
    await expect(rows).toHaveCount(12);
    await expect(density).toHaveValue("compact");
    const qty = rows.nth(2).getByLabel("采购数量", { exact: true });
    await qty.fill("7");
    await expect(editor.locator(".entry-summary")).toContainText("87.00");
    await expect(
      editor.getByLabel("当前行信息", { exact: true }),
    ).toContainText("第 3 行");
    await expect(rows.nth(2).locator(".entry-note-badge")).toBeVisible();
    await expect(
      editor.getByLabel("当前行信息", { exact: true }),
    ).toContainText("同商品");
    const compactHeight = (await rows.nth(2).boundingBox())!.height;
    expect(compactHeight).toBeLessThanOrEqual(44);
    expect(
      (await editor.locator(".editor-header-fields").boundingBox())!.height,
    ).toBeLessThanOrEqual(36);
    await density.selectOption("comfortable");
    expect((await rows.nth(2).boundingBox())!.height).toBeGreaterThan(
      compactHeight + 20,
    );
    await expect(rows.nth(2).locator(".entry-product-meta")).toBeVisible();
    await expect(qty).toHaveValue("7");
    await density.selectOption("compact");
    const colors: string[] = [],
      headerColors: string[] = [];
    for (const value of ["blue", "teal", "violet"]) {
      await setTheme(value);
      await expect(page.locator("html")).toHaveAttribute("data-theme", value);
      await qty.focus();
      colors.push(
        await editor
          .getByRole("button", { name: "保存采购单", exact: true })
          .evaluate((el) => getComputedStyle(el).backgroundColor),
      );
      headerColors.push(
        await page
          .locator(".workspace-chrome")
          .evaluate((el) => getComputedStyle(el).backgroundColor),
      );
      await expect(primary.getByRole("button").first()).toHaveCSS(
        "color",
        "rgb(255, 255, 255)",
      );
      await expect(qty).toHaveValue("7");
      await expect(editor.locator(".entry-summary")).toContainText("87.00");
      await page.screenshot({
        path: `../../artifacts/appearance-${value}.png`,
        animations: "disabled",
      });
    }
    expect(new Set(colors).size).toBe(3);
    expect(new Set(headerColors).size).toBe(3);
    await density.selectOption("comfortable");
    await editor.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect(editor.locator(".editor-actions")).toContainText("草稿已保存");
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "violet");
    await expect(density).toHaveValue("comfortable");
    await expect(qty).toHaveValue("7");
    await expect(rows).toHaveCount(12);
    await density.selectOption("compact");
    await setTheme("blue");
    for (const size of [
      { width: 1024, height: 768 },
      { width: 390, height: 844 },
    ]) {
      await page.setViewportSize(size);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      await expect(theme).toBeInViewport();
      await primary.getByRole("button", { name: "采购", exact: true }).click();
      await expect(
        page.getByRole("navigation", { name: "采购功能", exact: true }),
      ).toBeVisible();
      await page.keyboard.press("Escape");
      await page.screenshot({
        path: `../../artifacts/appearance-${size.width}.png`,
        animations: "disabled",
      });
    }
    expect(faults).toEqual([]);
  } finally {
    const latest = await read(context, path);
    await command(context, owner, path, {
      expected_version: latest.version,
      template: original.template,
    });
    await reviewer.close();
  }
});
