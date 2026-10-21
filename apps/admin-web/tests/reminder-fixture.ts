import { expect, type Browser, type BrowserContext } from "@playwright/test";
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

export async function reminderFixture(
  context: BrowserContext,
  browser: Browser,
) {
  const reviewer = await browser.newContext(),
    viewer = await browser.newContext();
  let owner = await wholesale(context, "demo.owner"),
    quality = await wholesale(reviewer, "demo.quality"),
    reader = await wholesale(viewer, "demo.viewer");
  const originals = { owner, quality, reader };
  const suffix = randomUUID().slice(0, 8),
    folder = resolve("../../artifacts/reminders");
  await mkdir(folder, { recursive: true });
  await mkdir(resolve("../../outputs/reminders"), { recursive: true });
  const file = resolve(folder, `tenant-${suffix}.json`);
  await writeFile(
    file,
    JSON.stringify({
      request_id: randomUUID(),
      tenant_name: `提醒闭环验收-${suffix}`,
      entity_name: `提醒软件测试主体-${suffix}`,
      credit_code: `TEST-${suffix}`,
      warehouse_code: "TEST",
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
  const result = await promisify(execFile)(
    resolve("../../backend/.venv/Scripts/python.exe"),
    [resolve("../../scripts/provision_enterprise.py"), file],
    { env: { ...process.env, PYTHONUTF8: "1" }, timeout: 30000 },
  );
  const tenant = JSON.parse(result.stdout);
  async function switchTo(ctx: BrowserContext, current: any, id: string) {
    const response = await ctx.request.post(origin + "/auth/tenant", {
      headers: headers(current),
      data: { tenant_id: id },
    });
    expect(response.status()).toBe(200);
    return (await response.json()).data;
  }
  owner = await switchTo(context, owner, tenant.tenant_id);
  quality = await switchTo(reviewer, quality, tenant.tenant_id);
  reader = await switchTo(viewer, reader, tenant.tenant_id);
  const today = new Date().toISOString().slice(0, 10),
    year = new Date().getFullYear();
  const product = await command(context, owner, "/api/v1/catalog/products", {
    code: `REM-P-${suffix}`,
    name: "提醒软件测试商品",
    category: "DEVICE",
    device_class: "I",
    manufacturer_name: "TEST厂家",
    specification: "单件",
    approval_number: "TEST-ONLY",
    valid_from: `${year}-01-01`,
    valid_until: `${year + 2}-12-31`,
    sku_code: `REM-SKU-${suffix}`,
    base_unit: "个",
    qty_scale: 0,
  });
  await approveCatalogParent(reviewer, quality, "products", product.id);
  const party = await command(context, owner, "/api/v1/catalog/parties", {
    entity_id: tenant.entity_id,
    code: `REM-S-${suffix}`,
    name: "提醒软件测试供应商",
    credit_code: `TEST-${suffix}`,
    kind: "SUPPLIER",
    categories: ["DEVICE"],
    organization: {},
  });
  const root = `/api/v1/catalog/parties/${party.id}`;
  const base = {
    holder_name: "TEST测试单位",
    issuer: "TEST机关",
    business_uses: ["SUPPLY"],
    evidence_ref: "TEST-ONLY",
    reason: "软件测试",
  };
  const identity = await command(context, owner, root + "/certificates", {
    ...base,
    certificate_type: "BUSINESS_LICENSE",
    number: `TEST-BL-${suffix}`,
    validity_mode: "LONG_TERM",
  });
  const permitBody = {
    ...base,
    certificate_type: "DEVICE_BUSINESS_LICENSE",
    number: `TEST-III-${suffix}`,
    validity_mode: "FIXED",
    valid_from: `${year - 1}-01-01`,
    valid_until: new Date(Date.now() - 86400000).toISOString().slice(0, 10),
  };
  const permit = await command(
    context,
    owner,
    root + "/certificates",
    permitBody,
  );
  const scope = await read(context, root + "/scopes");
  await command(context, owner, root + "/scopes", {
    expected_version: scope.version,
    payload: {
      scopes: [
        {
          id: randomUUID(),
          category: "DEVICE",
          name: "第一类器械 06",
          device_class: "I",
          codes: ["06"],
          channels: ["PURCHASE"],
          certificate_ids: [identity.id],
        },
        {
          id: randomUUID(),
          category: "DEVICE",
          name: "第三类器械 06",
          device_class: "III",
          codes: ["06"],
          channels: ["PURCHASE"],
          certificate_ids: [identity.id, permit.id],
        },
      ],
    },
    reason: "按测试资料配置经营范围",
  });
  await approveCatalogParent(reviewer, quality, "parties", party.id);
  const staff = await command(
    context,
    owner,
    `/api/v1/management/staff/${randomUUID()}`,
    {
      entity_id: tenant.entity_id,
      expected_version: "0",
      payload: { code: "QA-REM", name: "提醒测试负责人", job: "质量跟进" },
      reason: "测试职员",
    },
  );
  return {
    owner,
    quality,
    reader,
    reviewer,
    viewer,
    tenant,
    product,
    party,
    permit,
    permitBody,
    staff,
    today,
    year,
    async renew() {
      const current = await read(context, root + `/certificates/${permit.id}`);
      await command(
        context,
        owner,
        root + `/certificates/${permit.id}/revise`,
        {
          ...permitBody,
          expected_version: current.record.version,
          valid_until: `${year + 2}-12-31`,
          reason: "测试续期，与主体统一审核",
        },
      );
    },
    async approve() {
      await approveCatalogParent(reviewer, quality, "parties", party.id);
    },
    async dispose() {
      await switchTo(context, owner, originals.owner.tenant_id);
      await switchTo(reviewer, quality, originals.quality.tenant_id);
      await switchTo(viewer, reader, originals.reader.tenant_id);
      await reviewer.close();
      await viewer.close();
    },
  };
}
