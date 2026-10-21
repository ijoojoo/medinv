import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { resolve } from "node:path";
import { mkdir, writeFile } from "node:fs/promises";
import { wholesale, command, read, nav, origin } from "./business-helpers";
import { headers } from "./helpers";

test("management workspaces: real dictionaries, staff, designs, messages and quality ledgers", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(240000);
  const reviewer = await browser.newContext(),
    viewer = await browser.newContext();
  let owner = await wholesale(context, "demo.owner"),
    quality = await wholesale(reviewer, "demo.quality"),
    reader = await wholesale(viewer, "demo.viewer");
  const original = { owner, quality, reader };
  const suffix = randomUUID().slice(0, 8),
    requestId = randomUUID(),
    folder = resolve("../../artifacts/management-workspaces");
  await mkdir(folder, { recursive: true });
  await mkdir(resolve("../../outputs/management-workspaces"), {
    recursive: true,
  });
  const file = resolve(folder, `tenant-${suffix}.json`);
  await writeFile(
    file,
    JSON.stringify({
      request_id: requestId,
      tenant_name: `管理界面验收-${suffix}`,
      entity_name: `管理测试主体-${suffix}`,
      credit_code: `TEST-${suffix}`,
      warehouse_code: "START",
      warehouse_name: "测试仓库",
      location_code: "A1",
      reason: "隔离企业软件测试，非真实业务资料",
      members: [
        { principal_id: owner.principal_id, role_codes: ["OWNER"] },
        { principal_id: quality.principal_id, role_codes: ["QUALITY"] },
        { principal_id: reader.principal_id, role_codes: ["VIEWER"] },
      ],
    }),
  );
  const output = await promisify(execFile)(
    resolve(
      process.platform === "win32"
        ? "../../backend/.venv/Scripts/python.exe"
        : "../../backend/.venv/bin/python",
    ),
    [resolve("../../scripts/provision_enterprise.py"), file],
    { env: { ...process.env, PYTHONUTF8: "1" }, timeout: 30000 },
  );
  const tenant = JSON.parse(output.stdout);
  async function switchTo(ctx: typeof context, current: any, id: string) {
    const response = await ctx.request.post(origin + "/auth/tenant", {
      headers: headers(current),
      data: { tenant_id: id },
    });
    expect(response.status()).toBe(200);
    return (await response.json()).data;
  }
  async function shot(name: string) {
    await expect(page.locator(".el-message")).toHaveCount(0, { timeout: 8000 });
    await page.locator(".main-shell").evaluate((el) => el.scrollTo(0, 0));
    await page.screenshot({
      path: resolve("../../outputs/management-workspaces/" + name),
      fullPage: true,
      animations: "disabled",
    });
  }
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    owner = await switchTo(context, owner, tenant.tenant_id);
    quality = await switchTo(reviewer, quality, tenant.tenant_id);
    reader = await switchTo(viewer, reader, tenant.tenant_id);
    const year = new Date().getFullYear(),
      dates = { valid_from: `${year}-01-01`, valid_until: `${year + 2}-12-31` };
    const product = await command(context, owner, "/api/v1/catalog/products", {
      ...dates,
      code: "MGMT-P",
      name: "管理验收测试商品",
      category: "DEVICE",
      manufacturer_name: "测试厂家",
      specification: "单件",
      approval_number: "TEST-ONLY",
      sku_code: "MGMT-SKU",
      base_unit: "个",
      qty_scale: 0,
    });
    const party = await command(context, owner, "/api/v1/catalog/parties", {
      ...dates,
      entity_id: tenant.entity_id,
      code: "MGMT-S",
      name: "管理验收测试供应商",
      credit_code: "TEST-ONLY",
      kind: "SUPPLIER",
      license_no: "TEST-ONLY",
      categories: ["DEVICE"],
    });
    for (const [kind, id] of [
      ["products", product.id],
      ["parties", party.id],
    ])
      await command(reviewer, quality, `/api/v1/catalog/${kind}/${id}/review`, {
        expected_version: "1",
        decision: "APPROVE",
        reason: "软件测试审核",
        evidence_ref: "TEST-ONLY",
      });
    await command(context, owner, "/api/v1/purchase-orders", {
      entity_id: tenant.entity_id,
      supplier_id: party.id,
      warehouse_id: tenant.warehouse_id,
      lines: [
        {
          sku_id: product.sku_id,
          qty: "1",
          unit_price: "10",
          tax_rate: "0.13",
        },
      ],
    });
    await page.goto(`/workspace/dictionaries?tenant=${tenant.tenant_id}`);
    const dict = page.getByRole("region", { name: "数据字典", exact: true });
    await expect(dict).toBeVisible();
    await dict
      .getByRole("navigation", { name: "字典大分类" })
      .getByRole("button", { name: /商品资料/ })
      .click();
    await dict
      .getByRole("navigation", { name: "具体字典" })
      .getByRole("button", { name: /基本单位/ })
      .click();
    await dict
      .getByRole("button", { name: "＋ 新增字典值", exact: true })
      .click();
    let drawer = page.locator(".el-drawer:visible");
    await drawer.getByLabel("字典值名称", { exact: true }).fill("测试计量单位");
    await drawer.getByLabel("稳定编码", { exact: true }).fill("UNIT.TEST");
    await drawer.getByLabel("说明", { exact: true }).fill("软件验收专用");
    await drawer.getByRole("button", { name: "应用到配置" }).click();
    await dict.getByLabel("变更原因", { exact: true }).fill("字典抽屉保存验收");
    await dict.getByRole("button", { name: "保存配置", exact: true }).click();
    await expect(dict).toContainText("配置已载入");
    expect(
      (
        await read(context, "/api/v1/catalog/dictionaries/base_unit")
      ).items.some((i: any) => i.code === "UNIT.TEST"),
    ).toBe(true);
    await shot("数据字典.png");
    await page.keyboard.press("Control+k");
    const searchDialog = page.getByRole("dialog", {
      name: "搜索全部字典 / 字典值",
      exact: true,
    });
    await expect(searchDialog).toBeVisible();
    await searchDialog
      .getByRole("textbox", { name: "搜索全部字典", exact: true })
      .fill("UNIT.TEST");
    await searchDialog
      .getByRole("button", { name: "搜索", exact: true })
      .click();
    await searchDialog.getByRole("button", { name: /测试计量单位/ }).click();
    await expect(dict.locator("tr.highlight")).toContainText("UNIT.TEST");
    await dict
      .getByRole("navigation", { name: "字典大分类" })
      .getByRole("button", { name: /系统与对接/ })
      .click();
    await dict
      .getByRole("navigation", { name: "具体字典" })
      .getByRole("button", { name: /职员部门/ })
      .click();
    for (const [name, code, parent] of [
      ["质量管理部", "QA", ""],
      ["养护组", "CARE", "质量管理部"],
    ]) {
      await dict
        .getByRole("button", { name: "＋ 新增字典值", exact: true })
        .click();
      drawer = page.locator(".el-drawer:visible");
      await drawer.getByLabel("字典值名称", { exact: true }).fill(name);
      await drawer.getByLabel("稳定编码", { exact: true }).fill(code);
      if (parent)
        await drawer
          .getByRole("combobox", { name: "上级值", exact: true })
          .selectOption({ label: parent });
      await drawer.getByRole("button", { name: "应用到配置" }).click();
    }
    await dict.getByLabel("变更原因", { exact: true }).fill("部门分级验收");
    await dict.getByRole("button", { name: "保存配置", exact: true }).click();
    await expect(dict).toContainText("配置已载入");
    await dict
      .getByRole("navigation", { name: "具体字典" })
      .getByRole("button", { name: /职员状态/ })
      .click();
    await expect(
      dict.getByRole("button", { name: "保存配置", exact: true }),
    ).toHaveCount(0);
    const protectedData = await read(
      context,
      "/api/v1/catalog/dictionaries/staff_status",
    );
    const denied = await context.request.post(
      origin + "/api/v1/catalog/dictionaries/staff_status",
      {
        headers: { ...headers(owner), "Idempotency-Key": randomUUID() },
        data: {
          expected_version: protectedData.version,
          mode: "STRICT",
          items: protectedData.items.map((i: any) => ({ ...i, active: false })),
          reason: "测试拒绝修改系统值",
        },
      },
    );
    expect(denied.status()).toBe(422);
    await nav(page, "职员资料");
    const staff = page.getByRole("region", { name: "职员资料", exact: true });
    await staff.getByRole("button", { name: "新增职员" }).click();
    await staff.getByLabel("职员编码", { exact: true }).fill("EMP-001");
    await staff.getByLabel("姓名", { exact: true }).fill("验收测试职员");
    await staff
      .getByRole("combobox", { name: "部门", exact: true })
      .selectOption({ label: "养护组" });
    await staff.getByLabel("岗位", { exact: true }).fill("养护员");
    await staff.getByLabel("变更原因", { exact: true }).fill("新增职员验收");
    await staff.getByRole("button", { name: "保存资料", exact: true }).click();
    await expect(staff).toContainText("v1");
    await shot("职员资料.png");
    await nav(page, "审批流设计");
    const design = page.getByRole("region", {
      name: "审批流设计",
      exact: true,
    });
    await design.getByRole("button", { name: "新建审批设计" }).click();
    await design
      .getByLabel("设计名称", { exact: true })
      .fill("采购两级审核设计");
    await design.getByRole("button", { name: "＋ 添加审批节点" }).click();
    await design
      .getByLabel("节点名称", { exact: true })
      .nth(1)
      .fill("负责人复核");
    await design.getByLabel("变更原因", { exact: true }).fill("审批设计验收");
    await design
      .getByRole("button", { name: "保存设计稿", exact: true })
      .click();
    await expect(design).toContainText("v1");
    await shot("审批流设计.png");
    await nav(page, "往来单位");
    const list = page
      .getByRole("region", { name: "基础资料列表", exact: true })
      .filter({ visible: true });
    await list.getByLabel("查询资料", { exact: true }).fill("MGMT-S");
    await list.getByRole("button", { name: "查询", exact: true }).click();
    await list.getByRole("button", { name: "资料详情", exact: true }).click();
    await page
      .getByRole("button", { name: "供应商信息卡", exact: true })
      .click();
    await expect(
      page.getByRole("dialog", { name: "供应商信息卡", exact: true }),
    ).toContainText("管理验收测试供应商");
    await shot("供应商信息卡.png");
    await page.keyboard.press("Escape");
    await nav(page, "商品档案");
    const products = page.locator(".master-browser:visible");
    await products
      .getByRole("textbox", { name: "检索商品主数据", exact: true })
      .fill("MGMT-P");
    await products.getByRole("button", { name: "查询", exact: true }).click();
    await products
      .getByRole("button", { name: "管理验收测试商品", exact: true })
      .click();
    await page.getByRole("button", { name: "商品信息卡", exact: true }).click();
    await expect(
      page.getByRole("dialog", { name: "商品信息卡", exact: true }),
    ).toContainText("管理验收测试商品");
    await expect(
      page.getByRole("dialog", { name: "商品信息卡", exact: true }),
    ).toContainText("旧档原始记载");
    await expect(
      page.getByRole("dialog", { name: "商品信息卡", exact: true }),
    ).toContainText("测试厂家");
    await shot("商品信息卡.png");
    await page.keyboard.press("Escape");
    await nav(page, "质量台账");
    const ledger = page.getByRole("region", { name: "质量台账", exact: true });
    await ledger.getByRole("button", { name: "新增养护记录" }).click();
    await ledger
      .getByRole("combobox", { name: "养护商品", exact: true })
      .selectOption(product.id);
    await ledger.getByLabel("养护人员", { exact: true }).fill("验收测试职员");
    await ledger
      .getByLabel("检查项目与情况", { exact: true })
      .fill("检查包装、外观及储存条件");
    await ledger
      .getByRole("combobox", { name: "记录状态", exact: true })
      .selectOption("COMPLETED");
    await ledger.getByLabel("变更原因", { exact: true }).fill("养护完成验收");
    await ledger.getByRole("button", { name: "保存资料", exact: true }).click();
    await expect(
      ledger.getByRole("button", { name: "作废记录" }),
    ).toBeVisible();
    await nav(page, "质量处置");
    const stops = page.getByRole("region", {
      name: "商品停售管理",
      exact: true,
    });
    await page
      .getByRole("button", { name: "商品停售 / 解除停售", exact: true })
      .click();
    await stops.getByRole("button", { name: /管理验收测试商品/ }).click();
    await stops
      .getByLabel("处理原因", { exact: true })
      .fill("质量停售界面验证");
    await stops.getByRole("button", { name: "确认停售", exact: true }).click();
    await expect(stops).toContainText("质量已停售");
    await stops.getByLabel("处理原因", { exact: true }).fill("恢复测试商品");
    await stops.getByRole("button", { name: "解除停售", exact: true }).click();
    await expect(stops).toContainText("未设置质量停售");
    await nav(page, "消息和提醒");
    const messages = page.getByRole("region", {
      name: "消息和提醒",
      exact: true,
    });
    await expect(messages).toContainText("采购待审核");
    await messages
      .getByRole("button", { name: "标记已读", exact: true })
      .first()
      .click();
    await expect(messages).toContainText("已读");
    await nav(page, "企业设置");
    const settings = page.getByRole("region", {
      name: "企业设置",
      exact: true,
    });
    await settings
      .getByRole("button", { name: "消息与提醒", exact: true })
      .click();
    await settings
      .getByLabel("资质到期提前提醒天数", { exact: true })
      .fill("45");
    await settings.getByLabel("变更原因", { exact: true }).fill("提醒参数验收");
    await settings.getByRole("button", { name: "保存提醒设置" }).click();
    await expect(
      settings.getByRole("button", { name: "保存提醒设置" }),
    ).toBeDisabled();
    const forbidden = await viewer.request.post(
      origin + `/api/v1/management/staff/${randomUUID()}`,
      {
        headers: { ...headers(reader), "Idempotency-Key": randomUUID() },
        data: {
          entity_id: tenant.entity_id,
          expected_version: "0",
          payload: { code: "FORBIDDEN", name: "越权测试" },
          reason: "验证拒绝",
        },
      },
    );
    expect(forbidden.status()).toBe(403);
    await nav(page, "采购入库");
    await page
      .getByRole("navigation", { name: "主要功能", exact: true })
      .getByRole("button", { name: "采购", exact: true })
      .click();
    const menu = page.getByRole("navigation", {
      name: "采购功能",
      exact: true,
    });
    await expect(menu.getByRole("button")).toHaveCount(1);
    await expect(menu).toContainText("采购入库");
    await page.keyboard.press("Escape");
    await nav(page, "数据字典");
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(dict).toBeVisible();
    await expect
      .poll(() =>
        page.evaluate(() => document.documentElement.scrollWidth - innerWidth),
      )
      .toBeLessThanOrEqual(1);
    await shot("数据字典-窄屏.png");
    expect(errors).toEqual([]);
  } finally {
    await switchTo(context, owner, original.owner.tenant_id);
    await switchTo(reviewer, quality, original.quality.tenant_id);
    await switchTo(viewer, reader, original.reader.tenant_id);
    await reviewer.close();
    await viewer.close();
  }
});
