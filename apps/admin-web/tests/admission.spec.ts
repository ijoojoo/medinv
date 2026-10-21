import { expect, test } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { resolve } from "node:path";
import { mkdir, writeFile } from "node:fs/promises";
import {
  chooseParty,
  approveCatalogParent,
  command,
  nav,
  read,
  wholesale,
  origin,
} from "./business-helpers";
import { headers } from "./helpers";

test("isolated enterprise: own scope configuration retains legacy party grants and rechecks parent-approved certificate changes", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(180000);
  const reviewer = await browser.newContext(),
    viewer = await browser.newContext();
  let owner = await wholesale(context, "demo.owner"),
    quality = await wholesale(reviewer, "demo.quality"),
    reader = await wholesale(viewer, "demo.viewer");
  const original = { owner, quality, reader },
    errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const suffix = randomUUID().slice(0, 8),
    run = randomUUID(),
    year = new Date().getFullYear();
  const path = resolve(
    `../../artifacts/structured-admission/browser-tenant-${suffix}.json`,
  );
  await mkdir(resolve("../../artifacts/structured-admission"), {
    recursive: true,
  });
  await writeFile(
    path,
    JSON.stringify({
      request_id: run,
      tenant_name: `准入软件验收-${suffix}`,
      entity_name: `准入测试主体-${suffix}`,
      credit_code: `TEST-${suffix}`,
      warehouse_code: "START",
      warehouse_name: "测试仓库",
      location_code: "A1",
      reason: "隔离企业软件测试，非真实资质",
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
    [resolve("../../scripts/provision_enterprise.py"), path],
    { env: { ...process.env, PYTHONUTF8: "1" }, timeout: 30000 },
  );
  const opened = JSON.parse(output.stdout);
  async function switchTo(ctx: typeof context, current: any, tenant: string) {
    const response = await ctx.request.post(origin + "/auth/tenant", {
      headers: headers(current),
      data: { tenant_id: tenant },
    });
    expect(response.status()).toBe(200);
    return (await response.json()).data;
  }
  try {
    owner = await switchTo(context, owner, opened.tenant_id);
    quality = await switchTo(reviewer, quality, opened.tenant_id);
    reader = await switchTo(viewer, reader, opened.tenant_id);
    const root = `/api/v1/catalog/admission/${opened.entity_id}/PURCHASE`;
    await command(
      context,
      owner,
      "/api/v1/catalog/dictionaries/device_business_scope",
      {
        expected_version: "0",
        mode: "STRICT",
        items: [{ value: "软件测试范围A", active: true, order: 10 }],
        reason: "仅测试的范围分类",
      },
    );
    const dates = {
      valid_from: `${year}-01-01`,
      valid_until: `${year + 1}-12-31`,
    };
    const party = await command(context, owner, "/api/v1/catalog/parties", {
      ...dates,
      entity_id: opened.entity_id,
      code: "SCOPE-PARTY",
      name: "准入测试供货单位",
      credit_code: "TEST",
      kind: "SUPPLIER",
      license_no: "TEST-ONLY",
      categories: ["DEVICE"],
    });
    const product = await command(context, owner, "/api/v1/catalog/products", {
      ...dates,
      code: "SCOPE-PRODUCT",
      name: "准入测试商品",
      category: "DEVICE",
      manufacturer_name: "测试厂家",
      specification: "测试规格",
      approval_number: "TEST-ONLY",
      sku_code: "SCOPE-SKU",
      base_unit: "个",
      qty_scale: 0,
      device_business_scope: "软件测试范围A",
    });
    for (const [area, record] of [
      ["parties", party],
      ["products", product],
    ] as const)
      await command(
        reviewer,
        quality,
        `/api/v1/catalog/${area}/${record.id}/review`,
        {
          expected_version: record.version,
          decision: "APPROVE",
          evidence_ref: "TEST-REVIEW",
          reason: "独立测试核验",
        },
      );
    const certRoot = `/api/v1/catalog/parties/${party.id}/certificates`;
    const cert = await command(context, owner, certRoot, {
      certificate_type: "DEVICE_BUSINESS_LICENSE",
      number: "TEST-LICENSE",
      holder_name: "准入测试供货单位",
      issuer: "软件测试签发单位",
      business_uses: ["SUPPLY"],
      validity_mode: "LONG_TERM",
      scope_text: "仅软件测试原文；不代表真实经营资格",
      evidence_ref: "TEST-FILE",
      reason: "测试建档",
    });
    await approveCatalogParent(reviewer, quality, "parties", party.id);
    // Retain a pre-existing legacy grant while the UI edits only enterprise scopes.
    const legacyScope = {
      category: "DEVICE",
      value: "软件测试范围A",
      ...dates,
      evidence_ref: "TEST-SCOPE",
      party_id: party.id,
      certificate_id: cert.id,
      certificate_version: cert.version,
    };
    await command(context, owner, root, {
      expected_version: "0",
      reason: "历史范围兼容软件验收",
      payload: { enabled: false, own_scopes: [], party_scopes: [legacyScope] },
    });
    await page.goto("/");
    await nav(page, "经营范围与准入");
    const panel = page.getByRole("region", {
      name: "经营范围与准入",
      exact: true,
    });
    await expect(panel).toContainText("未启用结构化准入");
    const form = panel.getByRole("form", { name: "准入配置草稿" });
    await form.getByRole("button", { name: "添加本企业范围" }).click();
    await expect(
      form.getByRole("button", { name: "添加往来单位范围" }),
    ).toHaveCount(0);
    const grants = form.locator(".admission-grant");
    for (let i = 0; i < 1; i++) {
      const grant = grants.nth(i);
      await grant.getByLabel("范围名称", { exact: true }).fill("软件测试范围A");
      await grant
        .getByRole("combobox", { name: "器械管理类别", exact: true })
        .selectOption({ label: "旧式范围（按字典匹配）" });
      await grant
        .getByLabel("核验截止日期", { exact: true })
        .fill(dates.valid_until);
      await grant.getByLabel("核验截止日期", { exact: true }).press("Tab");
      await grant.getByLabel("核验凭据编号").fill("TEST-SCOPE");
    }
    await form
      .getByRole("checkbox", { name: "启用本业务的结构化范围校验" })
      .check();
    await form.getByLabel("配置原因").fill("软件验收配置，等待独立复核");
    await form.getByRole("button", { name: "保存草稿" }).click();
    await expect(panel).toContainText("有待复核草稿 v2");
    await expect(
      panel.getByRole("button", { name: "复核并生效" }),
    ).toBeDisabled();
    const trial = panel.getByRole("region", { name: "准入试算", exact: true });
    await trial.getByLabel("检索试算商品").fill("SCOPE-SKU");
    await trial.getByRole("button", { name: "查找商品" }).click();
    await trial
      .getByRole("combobox", { name: "试算商品", exact: true })
      .selectOption(product.sku_id);
    await chooseParty(
      trial.getByLabel("试算往来单位", { exact: true }),
      party.id,
    );
    await trial
      .getByRole("combobox", { name: "试算依据", exact: true })
      .selectOption("DRAFT");
    await trial.getByRole("button", { name: "检查范围" }).click();
    await expect(trial.locator(".admission-result")).toContainText(
      "范围匹配通过",
    );
    const reviewPage = await reviewer.newPage();
    reviewPage.on("pageerror", (e) => errors.push(e.message));
    await reviewPage.goto("/");
    await nav(reviewPage, "经营范围与准入");
    const reviewForm = reviewPage.getByRole("form", {
      name: "独立复核准入配置",
    });
    await reviewForm.getByLabel("复核意见").fill("独立核验软件测试规则");
    await reviewForm.getByRole("button", { name: "复核并生效" }).click();
    await expect(
      reviewPage.getByRole("region", { name: "经营范围与准入", exact: true }),
    ).toContainText("已启用结构化准入");
    await panel.getByRole("button", { name: "刷新配置" }).click();
    await expect(panel).toContainText("生效版本 3");
    const orderBody = {
      entity_id: opened.entity_id,
      supplier_id: party.id,
      warehouse_id: opened.warehouse_id,
      lines: [
        {
          sku_id: product.sku_id,
          qty: "1",
          unit_price: "10",
          tax_rate: "0.13",
        },
      ],
    };
    await command(context, owner, "/api/v1/purchase-orders", orderBody);
    await command(context, owner, `${certRoot}/${cert.id}/review`, {
      expected_version: cert.version,
      decision: "INACTIVATE",
      evidence_ref: "TEST-CHANGE",
      reason: "测试证照停用",
    });
    await approveCatalogParent(reviewer, quality, "parties", party.id);
    await trial
      .getByRole("combobox", { name: "试算依据", exact: true })
      .selectOption("ACTIVE");
    await trial.getByRole("button", { name: "检查范围" }).click();
    await expect(trial.locator(".admission-result")).toContainText(
      "证照版本已变化",
    );
    const rejected = await context.request.post(
      origin + "/api/v1/purchase-orders",
      {
        headers: { ...headers(owner), "Idempotency-Key": randomUUID() },
        data: orderBody,
      },
    );
    expect(rejected.status()).toBe(422);
    expect((await rejected.json()).error.code).toBe("ADMISSION_BLOCKED");
    await mkdir(resolve("../../outputs/structured-admission"), {
      recursive: true,
    });
    await trial.locator(".admission-result li").last().scrollIntoViewIfNeeded();
    await page.screenshot({
      path: resolve("../../outputs/structured-admission/准入配置与拦截.png"),
      fullPage: true,
    });
    await panel.locator(".admission-heading").scrollIntoViewIfNeeded();
    await page.screenshot({
      path: resolve("../../outputs/structured-admission/范围配置.png"),
      fullPage: true,
    });
    const readerPage = await viewer.newPage();
    await readerPage.goto("/");
    await nav(readerPage, "经营范围与准入");
    await expect(
      readerPage.getByRole("checkbox", { name: "启用本业务的结构化范围校验" }),
    ).toBeDisabled();
    await expect(
      readerPage.getByRole("button", { name: "添加本企业范围" }),
    ).toHaveCount(0);
    await expect(
      readerPage.getByRole("button", { name: "复核并生效" }),
    ).toHaveCount(0);
    expect((await read(context, root)).active_payload.enabled).toBe(true);
    expect(errors).toEqual([]);
    await writeFile(
      resolve("../../artifacts/structured-admission/browser-scope.json"),
      JSON.stringify({
        tenant_id: opened.tenant_id,
        entity_id: opened.entity_id,
        isolated_test_tenant: true,
        original_tenant_unchanged: true,
      }),
    );
  } finally {
    await switchTo(context, owner, original.owner.tenant_id);
    await switchTo(reviewer, quality, original.quality.tenant_id);
    await switchTo(viewer, reader, original.reader.tenant_id);
    await reviewer.close();
    await viewer.close();
  }
});
