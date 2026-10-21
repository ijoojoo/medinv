import { test, expect, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { resolve } from "node:path";
import { mkdir, writeFile } from "node:fs/promises";
import {
  wholesale,
  command,
  read,
  origin,
  approveCatalogParent,
} from "./business-helpers";
import { headers } from "./helpers";

test("published purchase workflow executes role steps, return/resubmit and frozen versions", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(240000);
  const reviewer = await browser.newContext(),
    adminContext = await browser.newContext();
  let owner = await wholesale(context, "demo.owner"),
    quality = await wholesale(reviewer, "demo.quality"),
    admin = await wholesale(adminContext, "demo.viewer");
  const original = { owner, quality, admin };
  const suffix = randomUUID().slice(0, 8),
    folder = resolve("../../artifacts/approvals"),
    shots = resolve("../../outputs/approvals");
  await mkdir(folder, { recursive: true });
  await mkdir(shots, { recursive: true });
  const file = resolve(folder, `tenant-${suffix}.json`);
  await writeFile(
    file,
    JSON.stringify({
      request_id: randomUUID(),
      tenant_name: `采购审批验收-${suffix}`,
      entity_name: `审批软件测试主体-${suffix}`,
      credit_code: `TEST-${suffix}`,
      warehouse_code: "TEST",
      warehouse_name: "测试仓库",
      location_code: "A1",
      reason: "隔离企业软件测试，非真实业务资料",
      members: [
        { principal_id: owner.principal_id, role_codes: ["OWNER"] },
        { principal_id: quality.principal_id, role_codes: ["QUALITY"] },
        { principal_id: admin.principal_id, role_codes: ["ADMIN"] },
      ],
    }),
  );
  const result = await promisify(execFile)(
    resolve("../../backend/.venv/Scripts/python.exe"),
    [resolve("../../scripts/provision_enterprise.py"), file],
    { env: { ...process.env, PYTHONUTF8: "1" }, timeout: 30000 },
  );
  const tenant = JSON.parse(result.stdout);
  async function switchTo(ctx: typeof context, current: any, id: string) {
    const response = await ctx.request.post(origin + "/auth/tenant", {
      headers: headers(current),
      data: { tenant_id: id },
    });
    expect(response.status()).toBe(200);
    return (await response.json()).data;
  }
  const qPage = await reviewer.newPage(),
    aPage = await adminContext.newPage(),
    errors: string[] = [];
  for (const p of [page, qPage, aPage])
    p.on("pageerror", (e) => errors.push(e.message));
  async function openOrder(p: Page, id: string) {
    await p.goto(
      `/workspace/purchasing?tenant=${tenant.tenant_id}&entity=${tenant.entity_id}&order=${id}`,
    );
    await expect(
      p.getByRole("region", { name: "采购审批进度", exact: true }),
    ).toBeVisible();
  }
  async function act(p: Page, action: "approve" | "reject" | "resubmit") {
    const labels = {
      approve: "通过当前审批节点",
      reject: "退回修改",
      resubmit: "重新提交审批",
    };
    await p.getByRole("button", { name: labels[action], exact: true }).click();
    const dialog = p.locator(".el-dialog:visible");
    if (action === "approve") {
      await dialog
        .getByLabel("补充说明", { exact: true })
        .fill("核对软件测试采购内容");
      await dialog
        .getByRole("button", { name: "确认审核", exact: true })
        .click();
    } else {
      await dialog
        .getByLabel("操作说明", { exact: true })
        .fill(
          action === "reject"
            ? "请核对采购依据后重提"
            : "已核对采购依据，重新提交",
        );
      await dialog
        .getByRole("button", {
          name: action === "reject" ? "确认退回" : "确认重新提交",
          exact: true,
        })
        .click();
    }
    await expect(dialog).not.toBeVisible();
  }
  async function shot(p: Page, name: string) {
    await expect(p.locator(".el-message")).toHaveCount(0, { timeout: 8000 });
    await p.screenshot({
      path: resolve(shots, name),
      fullPage: true,
      animations: "disabled",
    });
    expect(
      await p.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  }
  try {
    owner = await switchTo(context, owner, tenant.tenant_id);
    quality = await switchTo(reviewer, quality, tenant.tenant_id);
    admin = await switchTo(adminContext, admin, tenant.tenant_id);
    const year = new Date().getFullYear(),
      dates = { valid_from: `${year}-01-01`, valid_until: `${year + 2}-12-31` };
    const product = await command(context, owner, "/api/v1/catalog/products", {
      ...dates,
      code: "APR-P",
      name: "采购审批软件测试商品",
      category: "DEVICE",
      manufacturer_name: "TEST厂家",
      specification: "单件",
      approval_number: "TEST-ONLY",
      sku_code: "APR-SKU",
      base_unit: "个",
      qty_scale: 0,
    });
    const party = await command(context, owner, "/api/v1/catalog/parties", {
      ...dates,
      entity_id: tenant.entity_id,
      code: "APR-S",
      name: "采购审批软件测试供应商",
      credit_code: "TEST-ONLY",
      kind: "SUPPLIER",
      license_no: "TEST-ONLY",
      categories: ["DEVICE"],
    });
    await approveCatalogParent(reviewer, quality, "products", product.id);
    await approveCatalogParent(reviewer, quality, "parties", party.id);
    const body = {
      entity_id: tenant.entity_id,
      supplier_id: party.id,
      warehouse_id: tenant.warehouse_id,
      lines: [
        {
          sku_id: product.sku_id,
          qty: "2",
          unit_price: "10",
          tax_rate: "0.13",
        },
      ],
    };
    const legacy = await command(
      context,
      owner,
      "/api/v1/purchase-orders",
      body,
    );
    await page.goto(`/workspace/approval-design?tenant=${tenant.tenant_id}`);
    const design = page.getByRole("region", {
      name: "审批流设计",
      exact: true,
    });
    await design.getByRole("button", { name: "新建审批设计" }).click();
    await design
      .getByLabel("设计名称", { exact: true })
      .fill("采购质量与负责人审核");
    await design
      .getByLabel("节点名称", { exact: true })
      .first()
      .fill("质量审核");
    await design
      .getByRole("button", { name: "＋ 添加审批节点", exact: true })
      .click();
    await design
      .getByLabel("节点名称", { exact: true })
      .nth(1)
      .fill("负责人复核");
    await design
      .getByRole("combobox", { name: "审批角色", exact: true })
      .nth(1)
      .selectOption("ADMIN");
    await design
      .getByLabel("变更原因", { exact: true })
      .fill("创建采购逐级审批测试流程");
    await design
      .getByRole("button", { name: "保存设计稿", exact: true })
      .click();
    const publication = design.getByRole("region", {
      name: "采购审批发布",
      exact: true,
    });
    await expect(publication).toBeVisible();
    await publication
      .getByLabel("发布/停用说明", { exact: true })
      .fill("在隔离测试主体启用采购审批");
    await publication
      .getByRole("button", { name: "发布并用于新采购单", exact: true })
      .click();
    await expect(publication).toContainText("本主体当前使用");
    await publication.scrollIntoViewIfNeeded();
    await shot(page, "采购审批发布.png");
    const order = await command(
      context,
      owner,
      "/api/v1/purchase-orders",
      body,
    );
    expect(
      (await read(context, `/api/v1/purchase-orders/${legacy.id}`))
        .approval_workflow,
    ).toBeNull();
    const root = `/api/v1/purchase-orders/${order.id}`;
    // Save and publish a new design while the existing order retains its snapshot.
    await design
      .getByLabel("节点名称", { exact: true })
      .nth(1)
      .fill("新版本负责人复核");
    await design
      .getByLabel("变更原因", { exact: true })
      .fill("测试在途版本隔离");
    await design
      .getByRole("button", { name: "保存设计稿", exact: true })
      .click();
    await expect(publication).toContainText("本设计有未发布修改");
    await publication
      .getByLabel("发布/停用说明", { exact: true })
      .fill("发布第二版供后续单据使用");
    await publication
      .getByRole("button", { name: "发布并用于新采购单", exact: true })
      .click();
    await expect(publication).toContainText("设计版本 2");
    expect(
      (await read(context, root)).approval_workflow.definition.steps[1].name,
    ).toBe("负责人复核");
    await openOrder(page, order.id);
    await expect(
      page.getByRole("button", { name: "通过当前审批节点", exact: true }),
    ).toBeDisabled();
    await openOrder(aPage, order.id);
    await expect(
      aPage.getByRole("button", { name: "通过当前审批节点", exact: true }),
    ).toBeDisabled();
    await openOrder(qPage, order.id);
    await act(qPage, "approve");
    await expect(qPage.locator(".approval-progress li.current")).toContainText(
      "负责人复核",
    );
    expect((await read(context, root)).order.approval_state).toBe("DRAFT");
    await aPage.reload();
    await act(aPage, "reject");
    await expect(
      aPage.getByRole("region", { name: "采购审批进度", exact: true }),
    ).toContainText("已退回");
    await shot(aPage, "采购审批退回.png");
    await page.reload();
    await act(page, "resubmit");
    await expect(
      page.getByRole("region", { name: "采购审批进度", exact: true }),
    ).toContainText("第 2 轮");
    await qPage.reload();
    await act(qPage, "approve");
    await aPage.reload();
    await act(aPage, "approve");
    const progress = aPage.getByRole("region", {
      name: "采购审批进度",
      exact: true,
    });
    await expect(progress).toContainText("审批通过");
    await progress.getByText("本版审批记录", { exact: false }).click();
    await shot(aPage, "采购审批通过.png");
    const final = await read(context, root);
    expect(final.order.approval_state).toBe("APPROVED");
    expect(final.approval_workflow.decisions.map((d: any) => d.action)).toEqual(
      ["APPROVE", "REJECT", "RESUBMIT", "APPROVE", "APPROVE"],
    );
    expect(final.approval_workflow.decisions[2].role).toBeNull();
    expect(final.receipts).toHaveLength(0);
    await aPage.setViewportSize({ width: 768, height: 1024 });
    await shot(aPage, "采购审批窄屏.png");
    await writeFile(
      resolve(folder, "ui-evidence.json"),
      JSON.stringify(
        {
          tenant_id: tenant.tenant_id,
          order_id: order.id,
          policy_version: final.approval_workflow.policy_version,
          round: final.approval_workflow.round,
          approval_state: final.order.approval_state,
          actions: final.approval_workflow.decisions.map((d: any) => d.action),
          errors,
        },
        null,
        2,
      ),
    );
    expect(errors).toEqual([]);
  } finally {
    await switchTo(context, owner, original.owner.tenant_id);
    await switchTo(reviewer, quality, original.quality.tenant_id);
    await switchTo(adminContext, admin, original.admin.tenant_id);
    await reviewer.close();
    await adminContext.close();
  }
});
