import { test, expect, type Page, type BrowserContext } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { wholesale, command, read, nav } from "./business-helpers";

const tool = (page: Page, key: string) =>
  page.locator(`.data-toolbox:visible [data-tool="${key}"]`);
const layout = (page: Page) => page.locator(".data-workspace:visible");
async function expand(page: Page) {
  const button = layout(page).getByRole("button", {
    name: "展开工具箱",
    exact: true,
  });
  if (await button.count()) await button.click();
}
async function party(context: BrowserContext, owner: any) {
  const code = `3COL-${randomUUID().slice(0, 8)}`;
  return {
    code,
    ...(await command(context, owner, "/api/v1/catalog/parties", {
      entity_id: owner.entities[0].id,
      code,
      name: `三栏界面验证-${code}`,
      credit_code: code,
      kind: "BOTH",
      categories: ["DEVICE"],
      license_no: "TEST-ONLY",
      valid_from: "2026-01-01",
      valid_until: "2029-12-31",
      contact_name: "原联系人",
      short_name: "三栏验证单位",
      registered_address: "软件验证注册地址",
    })),
  };
}
async function openParty(page: Page, code: string) {
  await page.goto("/");
  await nav(page, "往来单位");
  const list = page.getByRole("region", { name: "基础资料列表" });
  await list.getByLabel("查询资料").fill(code);
  await list.getByLabel("查询资料").press("Enter");
  await list.getByRole("button", { name: "资料详情", exact: true }).click();
  return page.getByRole("region", { name: "资料详情", exact: true });
}
async function noOverflow(page: Page) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBe(true);
}

test("party fixed toolbox, read-only tabs, guarded daily task, formal/pending separation and refresh failure", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(120000);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const owner = await wholesale(context, "demo.owner"),
    item = await party(context, owner);
  const reviewer = await browser.newContext();
  const checker = await wholesale(reviewer, "demo.quality");
  await command(
    reviewer,
    checker,
    `/api/v1/catalog/parties/${item.id}/review`,
    {
      expected_version: item.version,
      decision: "APPROVE",
      evidence_ref: "TEST",
      reason: "软件验证",
    },
  );
  const detail = await openParty(page, item.code);
  await expect(
    layout(page).getByRole("button", { name: "展开工具箱" }),
  ).toHaveAttribute("aria-expanded", "false");
  await expect(detail.getByRole("tab", { name: "变更记录" })).toHaveCount(0);
  await expect(
    detail.getByRole("button", {
      name: /维护联系信息|维护简称与检索|刷新资料详情|信息卡/,
    }),
  ).toHaveCount(0);
  await expect(
    detail.getByRole("region", { name: "扩展资料入口" }),
  ).toHaveCount(0);
  await expand(page);
  const keys = await page
    .locator(".data-toolbox:visible [data-tool]")
    .evaluateAll((es) => es.map((e) => e.getAttribute("data-tool")));
  const widths = await layout(page).evaluate((el) => [
    el.querySelector(".master-sidebar")!.getBoundingClientRect().width,
    el.querySelector(".data-toolbox")!.getBoundingClientRect().width,
  ]);
  expect(Math.abs(widths[0] - widths[1])).toBeLessThan(1);
  for (const tab of [
    "经营信息",
    "质量管控",
    "经营范围与准入",
    "医保信息",
    "追溯信息",
    "第三方对接",
  ]) {
    await detail.getByRole("tab", { name: tab, exact: true }).click();
    expect(
      await page
        .locator(".data-toolbox:visible [data-tool]")
        .evaluateAll((es) => es.map((e) => e.getAttribute("data-tool"))),
    ).toEqual(keys);
    await expect(
      detail.locator(
        "input:visible:not([disabled]), textarea:visible:not([disabled])",
      ),
    ).toHaveCount(0);
  }
  await detail.getByRole("tab", { name: "经营信息", exact: true }).click();
  await tool(page, "revise").click();
  const task = page.getByRole("dialog", { name: "修订资料", exact: true });
  await task.getByLabel("联系人", { exact: true }).fill("新的联系人");
  await task.getByRole("button", { name: "关闭此对话框" }).click();
  const guard = page.getByRole("dialog", { name: "关闭前保存修改" });
  await guard.getByRole("button", { name: "取消关闭" }).click();
  await expect(task.getByLabel("联系人", { exact: true })).toHaveValue(
    "新的联系人",
  );
  await task.getByLabel("变更说明").fill("三栏维护验证");
  await task.getByRole("button", { name: "保存经营信息", exact: true }).click();
  await expect(task.getByLabel("变更说明")).toHaveValue("");
  await task.getByRole("button", { name: "关闭此对话框" }).click();
  await expect(task).not.toBeVisible();
  const current = (await read(context, `/api/v1/catalog/parties/${item.id}`))
    .record;
  expect(current.contact_name).toBe("新的联系人");
  expect(current.state).toBe("APPROVED");
  await command(context, owner, `/api/v1/catalog/parties/${item.id}/revise`, {
    expected_version: current.version,
    name: "待审核的新名称",
    kind: current.kind,
    categories: current.categories,
    license_no: current.license_no,
    valid_from: current.valid_from,
    valid_until: current.valid_until,
    reason: "验证正式资料不被待审核覆盖",
  });
  await tool(page, "refresh").click();
  await expect(detail).toContainText("有待审核变更");
  await expect(
    detail.getByRole("heading", {
      name: `三栏界面验证-${item.code}`,
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    detail.getByRole("tab", { name: "经营信息", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await tool(page, "history").click();
  const history = page.getByRole("dialog", { name: "变更记录", exact: true });
  await expect(history).toContainText("三栏维护验证");
  await page.keyboard.press("Escape");
  await expect(history).not.toBeVisible();
  await expect(tool(page, "history")).toBeFocused();
  await tool(page, "revise").click();
  await task
    .getByRole("navigation", { name: "修订资料分区" })
    .getByRole("button", { name: "基本信息", exact: true })
    .click();
  await task.getByRole("button", { name: "修订单位身份与资质" }).click();
  const identityTask = page.getByRole("region", {
    name: "修订资料",
    exact: true,
  });
  await expect(identityTask.getByLabel("名称", { exact: true })).toHaveValue(
    "待审核的新名称",
  );
  await identityTask.getByRole("button", { name: "取消", exact: true }).click();
  await expect(
    detail.getByRole("tab", { name: "经营信息", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await tool(page, "attachments").click();
  await expect(
    page
      .getByRole("dialog", { name: "证照附件", exact: true })
      .getByRole("button", { name: "上传附件", exact: true }),
  ).toHaveCount(0);
  await page.keyboard.press("Escape");
  await page.route(
    `**/api/v1/catalog/parties/${item.id}?*`,
    (r) =>
      r.fulfill({ status: 503, body: '{"error":{"message":"模拟读取失败"}}' }),
    { times: 1 },
  );
  await tool(page, "refresh").click();
  await expect(detail).toContainText(`三栏界面验证-${item.code}`);
  await detail.getByRole("tab", { name: "基本信息", exact: true }).click();
  await expect(page.locator(".el-message:visible")).toHaveCount(0, {
    timeout: 10000,
  });
  await page.screenshot({
    path: "../../outputs/three-column/往来单位-展开.png",
    fullPage: true,
  });
  await reviewer.close();
  expect(errors).toEqual([]);
});

test("toolbox session state, medium overlay, narrow layouts and role permissions", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(120000);
  const owner = await wholesale(context, "demo.owner"),
    item = await party(context, owner);
  await openParty(page, item.code);
  await expand(page);
  await nav(page, "职员资料");
  await page.setViewportSize({ width: 1050, height: 900 });
  await expect(
    page.getByRole("dialog", { name: "往来单位工具箱" }),
  ).toHaveCount(0);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await nav(page, "往来单位");
  await expect(
    layout(page).getByRole("button", { name: "收起工具箱" }),
  ).toHaveAttribute("aria-expanded", "true");
  await page.setViewportSize({ width: 1050, height: 900 });
  const panel = page.getByRole("dialog", { name: "往来单位工具箱" });
  await expect(panel).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(panel).not.toBeVisible();
  await layout(page)
    .getByRole("button", { name: "展开工具箱", exact: true })
    .click();
  await panel.getByRole("button", { name: "单位信息卡", exact: true }).click();
  await expect(panel).not.toBeVisible();
  const card = page.getByRole("dialog").filter({ hasText: item.code });
  await expect(card).toBeVisible();
  await expect
    .poll(() => card.evaluate((el) => el.contains(document.activeElement)))
    .toBe(true);
  await page.keyboard.press("Escape");
  for (const width of [390, 768, 1440, 2560]) {
    await page.setViewportSize({ width, height: 1000 });
    await noOverflow(page);
    await expect(page.locator(".el-message:visible")).toHaveCount(0, {
      timeout: 10000,
    });
    await page.screenshot({
      path: `../../outputs/three-column/往来单位-${width}.png`,
      fullPage: true,
    });
  }
  const viewer = await browser.newContext();
  await wholesale(viewer, "demo.quality");
  const viewPage = await viewer.newPage();
  await openParty(viewPage, item.code);
  await expect(tool(viewPage, "revise")).toHaveAttribute(
    "aria-disabled",
    "true",
  );
  await tool(viewPage, "revise").press("Enter");
  await expect(
    viewPage.getByRole("dialog", { name: "修订资料", exact: true }),
  ).toHaveCount(0);
  await viewer.close();
});

test("product read-only fields retain slots and editing remains explicit with saved history", async ({
  page,
  context,
}) => {
  test.setTimeout(120000);
  const owner = await wholesale(context, "demo.owner");
  const schema = await read(context, "/api/v1/catalog/master/schema");
  const name = `三栏商品-${randomUUID().slice(0, 8)}`;
  const item = await command(
    context,
    owner,
    "/api/v1/catalog/master/products",
    {
      product_id: null,
      category_node_id: schema.payload.nodes.find(
        (n: any) => n.node_code === "daily",
      ).node_id,
      form_revision: schema.revision,
      expected_version: 0,
      values: { name, base_unit: "个" },
      reason: "三栏界面验证",
    },
  );
  await page.goto("/");
  await nav(page, "商品档案");
  await page.getByLabel("检索商品主数据").fill(name);
  await page.getByRole("button", { name: "查询", exact: true }).click();
  await page.locator(`[data-catalog-record="${item.id}"]`).click();
  await expect(page.locator('[data-tab="basic"] [data-slot]')).toHaveCount(15);
  await expect(page.locator('[data-field="name"] .data-read-value')).toHaveText(
    name,
  );
  await expect(
    page.getByRole("button", { name: "保存资料草稿", exact: true }),
  ).toHaveCount(0);
  await tool(page, "revise").click();
  await page.locator('[data-field="name"] input').fill(name + "修订");
  await page.getByLabel("主数据修订说明").fill("三栏显式修订");
  await page.getByRole("button", { name: "保存资料草稿", exact: true }).click();
  await expect(page.locator(".master-state")).not.toContainText("已修改");
  await page.getByRole("button", { name: "返回只读查阅", exact: true }).click();
  await expect(page.locator('[data-field="name"] .data-read-value')).toHaveText(
    name + "修订",
  );
  await tool(page, "history").click();
  await expect(page.getByRole("dialog", { name: "变更记录" })).toContainText(
    "数据 v",
  );
  await page.keyboard.press("Escape");
  await expand(page);
  await expect(page.locator(".el-message:visible")).toHaveCount(0, {
    timeout: 10000,
  });
  await page.screenshot({
    path: "../../outputs/three-column/商品档案.png",
    fullPage: true,
  });
  const latest = await read(
    context,
    `/api/v1/catalog/master/products/${item.id}`,
  );
  expect(latest.values.name).toBe(name + "修订");
});

test("staff left scopes and right maintenance save a real record and show audit", async ({
  page,
  context,
}) => {
  const owner = await wholesale(context, "demo.owner"),
    code = `EMP-${randomUUID().slice(0, 8)}`;
  await page.goto("/");
  await nav(page, "职员资料");
  await tool(page, "new").click();
  await page.getByLabel("职员编码", { exact: true }).fill(code);
  await page.getByLabel("姓名", { exact: true }).fill("三栏职员");
  await page.getByLabel("变更原因", { exact: true }).fill("三栏职员建档验证");
  await page.getByRole("button", { name: "保存资料", exact: true }).click();
  await expect(page.locator(".data-read-grid")).toContainText(code);
  await expect(page.getByLabel("姓名", { exact: true })).toHaveCount(0);
  await tool(page, "revise").click();
  await page.getByLabel("任职状态").selectOption("LEFT");
  await page.getByLabel("变更原因", { exact: true }).fill("三栏离职范围验证");
  await page.getByRole("button", { name: "保存资料", exact: true }).click();
  await expect(page.locator(".data-read-grid")).toContainText("离职");
  await tool(page, "history").click();
  await expect(page.getByRole("dialog", { name: "变更记录" })).toContainText(
    "三栏离职范围验证",
  );
  await page.keyboard.press("Escape");
  await page
    .getByRole("navigation", { name: "职员范围" })
    .getByRole("button", { name: "离职职员" })
    .click();
  await page.getByRole("textbox", { name: "查询", exact: true }).fill(code);
  await page.getByRole("button", { name: "查询", exact: true }).click();
  await expect(page.getByRole("row").filter({ hasText: code })).toHaveCount(1);
  await page
    .getByRole("navigation", { name: "职员范围" })
    .getByRole("button", { name: "在职职员" })
    .click();
  await expect(page.getByRole("row").filter({ hasText: code })).toHaveCount(0);
  await expand(page);
  await expect(page.locator(".el-message:visible")).toHaveCount(0, {
    timeout: 10000,
  });
  await page.screenshot({
    path: "../../outputs/three-column/职员资料.png",
    fullPage: true,
  });
  const rows = await read(
    context,
    `/api/v1/management/staff?entity_id=${owner.entities[0].id}&search=${code}&staff_status=LEFT`,
  );
  expect(rows.items).toHaveLength(1);
});

test("warehouse tasks preserve stock identity and history is a single right tool", async ({
  page,
  context,
}) => {
  await wholesale(context, "demo.owner");
  const code = `WH-${randomUUID().slice(0, 8)}`;
  await page.goto("/");
  await nav(page, "仓库与库位");
  await tool(page, "new").click();
  let dialog = page.getByRole("dialog", { name: "新增实体仓库" });
  await dialog.getByLabel("仓库编码", { exact: true }).fill(code);
  await dialog.getByLabel("仓库名称", { exact: true }).fill("三栏仓库");
  await dialog.getByLabel("首个库位编码").fill("MAIN");
  await dialog.getByRole("button", { name: "保存仓库", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await expand(page);
  await tool(page, "location").click();
  dialog = page.getByRole("dialog", { name: "新增库位", exact: true });
  await dialog.getByLabel("库位编码", { exact: true }).fill("SECOND");
  await dialog.getByLabel("库位名称", { exact: true }).fill("第二库位");
  await dialog.getByLabel("维护原因").fill("三栏库位验证");
  await dialog.getByRole("button", { name: "保存库位", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await page
    .getByRole("row")
    .filter({ hasText: "SECOND" })
    .getByRole("button", { name: "选择库位" })
    .click();
  await tool(page, "rename-location").click();
  dialog = page.getByRole("dialog", { name: "修改库位名称" });
  await dialog.getByLabel("库位名称", { exact: true }).fill("第二收货位");
  await dialog.getByLabel("维护原因").fill("只调整名称");
  await dialog.getByRole("button", { name: "保存名称", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await expect(
    page.getByRole("row").filter({ hasText: "SECOND" }),
  ).toContainText("第二收货位");
  await expect(
    layout(page).getByRole("heading", { name: "维护记录" }),
  ).toHaveCount(0);
  await tool(page, "history").click();
  await expect(page.getByRole("dialog", { name: "变更记录" })).toContainText(
    "只调整名称",
  );
  await page.keyboard.press("Escape");
  await expect(page.locator(".el-message:visible")).toHaveCount(0, {
    timeout: 10000,
  });
  await page.screenshot({
    path: "../../outputs/three-column/仓库与库位.png",
    fullPage: true,
  });
});

test("dictionary browse, explicit revision, cancellation, saved history and search", async ({
  page,
  context,
}) => {
  const owner = await wholesale(context, "demo.owner");
  const original = await read(
    context,
    "/api/v1/catalog/dictionaries/dosage_form",
  );
  try {
    await page.goto("/");
    await nav(page, "数据字典");
    const dict = page.locator(".dictionary-content:visible");
    await expect(dict.getByRole("button", { name: /新增字典值/ })).toHaveCount(
      0,
    );
    await expect(dict.getByRole("switch")).toHaveCount(0);
    await tool(page, "revise").click();
    const row = dict.getByRole("row").filter({ hasText: "片剂" }).first();
    await row.getByRole("button", { name: "编辑", exact: true }).click();
    const edit = page.getByRole("dialog", { name: "编辑字典值" });
    const note = edit.getByLabel("说明", { exact: true }),
      old = await note.inputValue();
    await note.fill("临时修改验证");
    await edit.getByRole("button", { name: "关闭此对话框" }).click();
    await page
      .getByRole("dialog", { name: "关闭编辑" })
      .getByRole("button", { name: "放弃", exact: true })
      .click();
    await dict.getByRole("button", { name: "取消修订" }).click();
    await tool(page, "revise").click();
    await row.getByRole("button", { name: "编辑", exact: true }).click();
    await expect(note).toHaveValue(old);
    await note.fill("三栏字典说明验证-" + Date.now());
    await edit.getByRole("button", { name: "应用到配置" }).click();
    await dict.getByLabel("变更原因").fill("三栏字典界面验证");
    expect(
      await dict.locator("form").evaluateAll((fs) =>
        fs.flatMap((f) =>
          [
            ...f.querySelectorAll<HTMLInputElement>(
              "input:invalid,select:invalid",
            ),
          ].map((e) => ({
            label: e.getAttribute("aria-label"),
            html: e.outerHTML,
            message: e.validationMessage,
          })),
        ),
      ),
    ).toEqual([]);
    const savedResponse = page.waitForResponse(
      (r) =>
        r.request().method() === "POST" &&
        r.url().includes("/catalog/dictionaries/"),
    );
    await dict.getByRole("button", { name: "保存配置" }).click();
    const saved = await savedResponse;
    expect(saved.status(), await saved.text()).toBe(200);
    await expect(dict.getByRole("switch")).toHaveCount(0);
    await tool(page, "history").click();
    await expect(page.getByRole("dialog", { name: "变更记录" })).toContainText(
      "三栏字典界面验证",
    );
    await page.keyboard.press("Escape");
    await tool(page, "search").click();
    const search = page.getByRole("dialog", { name: "搜索全部字典 / 字典值" });
    await search.getByLabel("搜索全部字典", { exact: true }).fill("片剂");
    await search.getByRole("button", { name: "搜索", exact: true }).click();
    await expect(search.locator(".dictionary-search-results")).toContainText(
      "片剂",
    );
    await page.keyboard.press("Escape");
    await expand(page);
    await expect(page.locator(".el-message:visible")).toHaveCount(0, {
      timeout: 10000,
    });
    await page.screenshot({
      path: "../../outputs/three-column/数据字典.png",
      fullPage: true,
    });
    for (const width of [390, 768, 1440]) {
      await page.setViewportSize({ width, height: 1000 });
      if (width < 1280) await page.keyboard.press("Escape");
      await noOverflow(page);
    }
  } finally {
    const current = await read(
      context,
      "/api/v1/catalog/dictionaries/dosage_form",
    );
    if (JSON.stringify(current.items) !== JSON.stringify(original.items))
      await command(
        context,
        owner,
        "/api/v1/catalog/dictionaries/dosage_form",
        {
          expected_version: current.version,
          mode: original.mode,
          items: original.items,
          reason: "验证结束恢复原字典说明",
        },
      );
  }
});
