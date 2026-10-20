import { test, expect } from "@playwright/test";
import { wholesale, read, nav } from "./business-helpers";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

test("archived fixtures leave active choices while demo history remains readable", async ({page, context}) => {
  const s = await wholesale(context, "demo.owner");
  const plan = JSON.parse(readFileSync(resolve("../../outputs/maintenance/20261019-test-data-plan.json"), "utf8"));
  const retiredTenants = new Set(plan.tenants.map((t: any) => t.id));
  expect(s.tenants.some((t: any) => retiredTenants.has(t.id))).toBe(false);
  const retiredWarehouses = new Set(plan.warehouses.map((w: any) => w.id));
  const options = await read(context, `/api/v1/purchasing/options?entity_id=${s.entities[0].id}`);
  expect(options.warehouses.some((w: any) => retiredWarehouses.has(w.id))).toBe(false);
  expect(options.warehouses.some((w: any) => w.name === "中心仓库")).toBe(true);
  const old = plan.warehouses.find((w: any) => w.tenant_id === s.tenant_id);
  const detail = await read(context, `/api/v1/warehouses/${old.id}`);
  expect(detail.warehouse.archived_at).toBeTruthy();
  await page.goto("/");
  await nav(page, "仓库与库位");
  await expect(page.locator("main")).toContainText("中心仓库");
  await expect(page.locator("main")).not.toContainText(old.name);
  await expect(page.getByRole("combobox", {name: "切换经营主体", exact: true})).toBeVisible();
  await page.screenshot({path: "../../outputs/mdm-usability/归档后仓库列表.png", fullPage: true});
});
