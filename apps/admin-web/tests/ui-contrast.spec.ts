import { test, expect } from "@playwright/test";
import { wholesale, nav } from "./business-helpers";

test("normal theme text and status tag text meet the contrast baseline", async ({
  page,
  context,
}) => {
  await wholesale(context, "demo.owner");
  await page.goto("/");
  await nav(page, "采购入库");
  await expect(
    page.getByRole("button", { name: "查询单据", exact: true }),
  ).toBeEnabled();
  for (const theme of ["teal", "blue", "violet"]) {
    await page
      .getByRole("button", { name: "界面与操作设置", exact: true })
      .click();
    await page
      .locator(".appearance-settings")
      .getByRole("button", {
        name: (
          { teal: "青碧", blue: "清朗蓝", violet: "雅紫" } as Record<
            string,
            string
          >
        )[theme],
        exact: true,
      })
      .click();
    await nav(page, "采购入库");
    const ratios = await page.evaluate(() => {
      const color = (text: string) =>
        text
          .match(/[\d.]+/g)!
          .slice(0, 3)
          .map(Number);
      const luminance = (rgb: number[]) =>
        rgb
          .map((n) => n / 255)
          .map((n) => (n <= 0.04045 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4))
          .reduce((s, n, i) => s + n * [0.2126, 0.7152, 0.0722][i]!, 0);
      const background = (el: Element | null): number[] => {
        if (!el) return [255, 255, 255];
        const s = getComputedStyle(el);
        // All chrome gradients run from accent to the darker hover shade.
        // Measure the lighter start, where white text has the lowest contrast.
        const gradient = s.backgroundImage.match(/rgba?\([^)]+\)/)?.[0];
        const rgba = (gradient || s.backgroundColor)
          .match(/[\d.]+/g)!
          .map(Number);
        const alpha = rgba[3] ?? 1;
        const behind = alpha < 1 ? background(el.parentElement) : [0, 0, 0];
        return rgba
          .slice(0, 3)
          .map((c, i) => c * alpha + behind[i]! * (1 - alpha));
      };
      const contrast = (a: number[], b: number[]) => {
        const x = luminance(a),
          y = luminance(b);
        return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
      };
      return [
        ...document.querySelectorAll<HTMLElement>(
          ".module-navigation button.selected,.el-tag,.business-page-title",
        ),
      ]
        .filter((el) => el.getClientRects().length)
        .map((el) => {
          const style = getComputedStyle(el);
          return {
            text: el.textContent,
            ratio: contrast(color(style.color), background(el)),
          };
        });
    });
    expect(ratios.length).toBeGreaterThan(1);
    for (const r of ratios)
      expect(r.ratio, `${theme} ${r.text}`).toBeGreaterThanOrEqual(4.5);
  }
});
