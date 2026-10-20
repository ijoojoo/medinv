import { test, expect } from "@playwright/test";
import { wholesale, nav, read } from "./business-helpers";

test("module tabs close independently and protect inline master input", async ({
  page,
  context,
}) => {
  await wholesale(context, "demo.owner");
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await nav(page, "采购入库");
  await nav(page, "商品档案");
  await page
    .getByRole("button", { name: "新建商品 / 项目", exact: true })
    .click();
  const schema = await read(context, "/api/v1/catalog/master/schema");
  const node = schema.payload.nodes.find((n: any) => n.node_code === "drug");
  const choose = page.getByRole("dialog", { name: "明确选择建档品类" });
  await choose.getByLabel("新建商品品类").selectOption(node.node_id);
  await choose.getByRole("button", { name: "进入建档" }).click();
  const suffix = Date.now().toString(36),
    organization = `直接输入厂家-${suffix}`,
    number = `输入证件-${suffix}`;
  await page
    .locator('[data-field="name"] input')
    .fill(`便捷建档验收-${suffix}`);
  await page.locator('[data-field="base_unit"] input').fill("盒");
  await page
    .locator('[data-field="regulatory_subject"] input')
    .fill(organization);
  await page.locator('[data-field="manufacturer"] input').fill(organization);
  await page.locator('[data-field="product_certificates"] input').fill(number);
  await page.getByRole("button", { name: "关闭采购入库", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "关闭采购入库", exact: true }),
  ).toHaveCount(0);
  await expect(page.locator('[data-field="manufacturer"] input')).toHaveValue(
    organization,
  );
  await page.getByRole("button", { name: "关闭商品档案", exact: true }).click();
  const leave = page.getByRole("dialog", { name: "关闭前保存修改" });
  await expect(leave).toBeVisible();
  await leave.getByRole("button", { name: "取消关闭", exact: true }).click();
  await expect(page.locator('[data-field="manufacturer"] input')).toHaveValue(
    organization,
  );
  await page.getByRole("button", { name: "保存资料草稿", exact: true }).click();
  await expect(page.locator(".master-state")).toContainText("待审核");
  const organizations = await read(
    context,
    `/api/v1/catalog/master/organizations?search=${encodeURIComponent(organization)}`,
  );
  const certificates = await read(
    context,
    `/api/v1/catalog/master/certificates?search=${encodeURIComponent(number)}`,
  );
  expect(organizations.items).toHaveLength(1);
  expect(certificates.items).toHaveLength(1);
  expect(certificates.items[0].state).toBe("DRAFT");
  const manufacturer = page.locator('[data-field="manufacturer"] input');
  await manufacturer.fill(organization.slice(0, -1));
  await expect(
    page.locator(".master-reference-candidates:visible"),
  ).toContainText(organization);
  await manufacturer.press("ArrowDown");
  await manufacturer.press("Enter");
  await expect(manufacturer).toHaveValue(organization);
  await page.getByRole("button", { name: "保存资料草稿", exact: true }).click();
  await expect(page.locator(".master-state")).not.toContainText("已修改");
  await expect(
    page.getByRole("button", { name: "保存资料草稿", exact: true }),
  ).toBeEnabled();
  await expect(page.locator(".el-message:visible")).toHaveCount(0);
  await page.screenshot({
    path: "../../outputs/mdm-usability/直接输入与顶部主体.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "关闭商品档案", exact: true }).click();
  await expect(page.locator(".master-editor")).toHaveCount(0);
  await expect(page.locator(".module-tab.active")).toContainText("工作台");
  expect(errors).toEqual([]);
});

test("top entity is the sole business selector and follows tenant changes", async ({
  page,
  context,
}) => {
  const s = await wholesale(context, "demo.owner");
  await page.goto("/");
  await expect(
    page.getByRole("combobox", { name: "切换经营主体", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".workspace-access")).toHaveCount(0);
  await nav(page, "采购入库");
  await expect(
    page.locator(".business-toolbar").filter({ hasText: "经营主体" }),
  ).toHaveCount(0);
  const retail = s.tenants.find((t: any) => t.name.includes("零售"));
  await page.locator(".account .el-select").first().click();
  await page.getByRole("option", { name: retail.name, exact: true }).click();
  await expect(page.locator(".account .el-select").last()).toContainText(
    "零售经营主体",
  );
  const request = page.waitForRequest((r) =>
    r.url().includes("/api/v1/purchasing/options?"),
  );
  await nav(page, "采购入库");
  const sent = await request;
  const current = await readSession(context);
  expect(new URL(sent.url()).searchParams.get("entity_id")).toBe(
    current.entities[0].id,
  );
  await expect(
    page.getByRole("button", { name: "关闭采购入库", exact: true }),
  ).toBeVisible();
});
async function readSession(context: any) {
  return (
    await (
      await context.request.get("http://127.0.0.1:5173/auth/session")
    ).json()
  ).data;
}

test("authorized entity selection reloads scoped lists and survives refresh", async ({
  page,
  context,
}) => {
  const session = await wholesale(context, "demo.owner");
  // Client-only scope fixture: no entities or business records are written.
  const extra = {
    id: "16baebf7-7f27-4700-b294-0b6cb2ee7e85",
    name: "分店作用域验证",
  };
  await page.route("**/auth/session", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.data.entities = [...session.entities, extra];
    await route.fulfill({ response, json: body });
  });
  await page.route("**/api/v1/purchasing/**", async (route) => {
    if (route.request().method() !== "GET")
      throw new Error("Scope fixture must remain read-only");
    await route.fulfill({
      json: {
        data: {
          items: [],
          has_more: false,
          suppliers: [],
          warehouses: [],
          locations: [],
          skus: [],
        },
      },
    });
  });
  await page.goto("/");
  await nav(page, "采购入库");
  const loaded = page.waitForRequest(
    (r) =>
      r.url().includes("/purchasing/options?") &&
      new URL(r.url()).searchParams.get("entity_id") === extra.id,
  );
  await page.locator(".account .el-select").last().click();
  await page.getByRole("option", { name: extra.name, exact: true }).click();
  await loaded;
  await expect(page.locator(".account .el-select").last()).toContainText(
    extra.name,
  );
  await page.reload();
  await expect(page.locator(".account .el-select").last()).toContainText(
    extra.name,
  );
  await expect(
    page.getByRole("button", { name: "关闭采购入库", exact: true }),
  ).toBeVisible();
});
