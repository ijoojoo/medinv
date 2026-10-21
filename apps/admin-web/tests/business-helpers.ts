import {
  expect,
  type BrowserContext,
  type Page,
  type Locator,
} from "@playwright/test";
import { randomUUID } from "node:crypto";
import { login, headers, view } from "./helpers";
import { navigationGroups } from "../src/navigation";
export const origin = "http://127.0.0.1:5173";
export async function approveCatalogParent(
  context: BrowserContext,
  current: any,
  area: "products" | "parties",
  id: string,
) {
  const root = `/api/v1/catalog/${area}/${id}`;
  const { record } = await read(context, root);
  return command(context, current, `${root}/review`, {
    expected_version: record.version,
    decision: "APPROVE",
    evidence_ref: "TEST-PARENT-REVIEW",
    reason: "统一核对主体资料及本次证件变更",
  });
}
export async function chooseParty(
  input: Locator,
  value: string | { label: string },
) {
  const page = input.page();
  let label = typeof value === "string" ? "" : value.label;
  if (!label) {
    const scope = await input.evaluate((el) => {
      const root = el.closest<HTMLElement>(".party-lookup")!;
      return { entity: root.dataset.entity!, channel: root.dataset.channel! };
    });
    const data = await read(
      page.context(),
      `/api/v1/catalog/entry-parties?${new URLSearchParams({ entity_id: scope.entity, channel: scope.channel, party_id: value as string })}`,
    );
    expect(data.items).toHaveLength(1);
    label = data.items[0].code;
  }
  await input.fill(label);
  const options = page.locator(".party-lookup-popover:visible [data-party]");
  const option =
    typeof value === "string"
      ? page.locator(`.party-lookup-popover:visible [data-party="${value}"]`)
      : options.filter({ hasText: value.label });
  await expect(option).toHaveCount(1);
  await option.click();
  await expect(page.locator(".party-lookup-popover:visible")).toHaveCount(0);
}
export async function chooseWarehouse(input: Locator, id: string) {
  const page = input.page();
  const scope = await input.evaluate((el) => {
    const root = el.closest<HTMLElement>(".warehouse-lookup")!;
    return { entity: root.dataset.entity!, channel: root.dataset.channel! };
  });
  const data = await read(
    page.context(),
    `/api/v1/warehouses/lookup?${new URLSearchParams({
      entity_id: scope.entity,
      channel: scope.channel,
      warehouse_id: id,
    })}`,
  );
  expect(data.items).toHaveLength(1);
  await input.fill(data.items[0].code);
  await page
    .locator(`.warehouse-lookup-popover:visible [data-warehouse="${id}"]`)
    .click();
  await expect(input).toHaveValue(data.items[0].name);
  await expect(page.locator(".warehouse-lookup-popover:visible")).toHaveCount(
    0,
  );
}
export async function chooseEntrySku(
  scope: Locator,
  sku: string | { code: string },
) {
  const page = scope.page(),
    grid = page.locator(".document-entry:visible").first();
  const params = new URLSearchParams({
    entity_id: (await grid.getAttribute("data-entity"))!,
    channel: (await grid.getAttribute("data-channel"))!,
    ...(typeof sku === "string" ? { sku_id: sku } : { search: sku.code }),
  });
  const data = await read(
    page.context(),
    `/api/v1/catalog/entry-products?${params}`,
  );
  expect(data.items).toHaveLength(1);
  await scope
    .getByRole("combobox", { name: "商品", exact: true })
    .fill(data.items[0].code);
  await page
    .locator(`.entry-product-popup [data-sku="${data.items[0].id}"]`)
    .click();
}
export async function wholesale(context: BrowserContext, user: string) {
  let current = await login(context, user);
  const target = current.tenants.find((t: { name: string }) =>
    t.name.includes("批发"),
  );
  if (current.tenant_id !== target.id) {
    const response = await context.request.post(origin + "/auth/tenant", {
      headers: headers(current),
      data: { tenant_id: target.id },
    });
    expect(response.status()).toBe(200);
    current = (await response.json()).data;
  }
  return current;
}
export async function command(
  context: BrowserContext,
  current: any,
  path: string,
  data: unknown,
) {
  const response = await context.request.post(origin + path, {
    headers: { ...headers(current), "Idempotency-Key": randomUUID() },
    data,
  });
  expect(response.status(), path + ": " + (await response.text())).toBe(200);
  return (await response.json()).data;
}
export async function read(context: BrowserContext, path: string) {
  const get = async (url: string) => {
    const response = await context.request.get(origin + url, {
      headers: headers(await view(context.request)),
    });
    expect(response.status(), url + ": " + (await response.text())).toBe(200);
    return (await response.json()).data;
  };
  let data = await get(path);
  if (!/^\/api\/v1\/(purchasing|selling|inventory)\/options\?/.test(path))
    return data;
  const result = { ...data };
  let offset = 0;
  while (data.has_more) {
    expect(data.next_offset).toBeGreaterThan(offset);
    expect(data.next_offset).toBeLessThanOrEqual(100000);
    offset = data.next_offset;
    data = await get(path + "&offset=" + offset);
    for (const [name, items] of Object.entries(data)) {
      if (Array.isArray(items))
        result[name] = [
          ...new Map(
            [...result[name], ...items].map((item) => [item.id, item]),
          ).values(),
        ];
    }
  }
  return { ...result, has_more: false, next_offset: null };
}
export async function nav(page: Page, name: string) {
  name =
    ({ 商品字典: "数据字典", 批发销售: "销售出库" } as Record<string, string>)[
      name
    ] || name;
  if (["商品字段设置", "成员与权限"].includes(name)) {
    await nav(page, "企业设置");
    const settings = page.getByRole("region", {
      name: "企业设置",
      exact: true,
    });
    await settings
      .getByRole("navigation", { name: "设置功能分组" })
      .getByRole("button", {
        name: name === "成员与权限" ? "人员与权限" : "基础资料",
        exact: true,
      })
      .click();
    await settings
      .getByRole("button", {
        name: name === "成员与权限" ? /成员与权限/ : /商品字段与分类规则/,
      })
      .click();
    return;
  }
  const primary = page.getByRole("navigation", {
    name: "主要功能",
    exact: true,
  });
  const group = navigationGroups.find((g) =>
    g.items.some((i) => i.name === name),
  );
  if (group) {
    await primary
      .getByRole("button", { name: group.name, exact: true })
      .click();
    await page
      .getByRole("navigation", { name: group.name + "功能", exact: true })
      // Legacy callers navigate to the purchasing workspace's orders list.
      .getByRole("button", {
        name,
        exact: true,
      })
      .click();
  } else await primary.getByRole("button", { name, exact: true }).click();
  await expect(
    page.getByRole("heading", { name, exact: true, level: 1 }),
  ).toBeVisible();
}

export async function stockedCustomer(
  context: BrowserContext,
  owner: any,
  reviewer: BrowserContext,
  checker: any,
  destination?: { warehouse: string; location: string },
) {
  const suffix = Date.now().toString(36),
    year = new Date().getFullYear();
  const name = "销售验收器械-" + suffix,
    customer = "销售验收客户-" + suffix,
    lot = "SALE-" + suffix;
  const entity = owner.entities[0].id;
  // Prepare qualified stock through the actual catalog and procurement APIs; no direct database fixtures.
  const product = await command(context, owner, "/api/v1/catalog/products", {
    code: "SP-" + suffix,
    name,
    category: "DEVICE",
    device_class: "I",
    manufacturer_name: "软件测试厂家",
    specification: "单件",
    approval_number: "DEMO-ONLY",
    valid_from: `${year}-01-01`,
    valid_until: `${year + 2}-12-31`,
    sku_code: "SS-" + suffix,
    base_unit: "个",
    qty_scale: 0,
  });
  // Complete the current MDM identity before review; do not relax required rules
  // just to keep historical fixture preparation working after a schema upgrade.
  const schema = await read(context, "/api/v1/catalog/master/schema");
  const certificate = await command(
    context,
    owner,
    `/api/v1/catalog/products/${product.id}/certificates`,
    {
      certificate_type: "DEVICE_FILING",
      number: "TEST-FILING-" + suffix,
      holder_name: "软件测试厂家",
      issuer: "软件测试机关",
      validity_mode: "FIXED",
      valid_from: `${year}-01-01`,
      valid_until: `${year + 2}-12-31`,
      evidence_ref: "DEMO-EVIDENCE",
      reason: "仅供软件测试的虚拟证件",
    },
  );
  const current = (
    await read(context, `/api/v1/catalog/products/${product.id}`)
  ).record;
  const master = await command(
    context,
    owner,
    "/api/v1/catalog/master/products",
    {
      product_id: product.id,
      category_node_id: schema.payload.nodes.find(
        (n: any) => n.node_code === "device",
      ).node_id,
      form_revision: schema.revision,
      expected_version: Number(current.version),
      values: {
        regulatory_subtype: "MEDICAL_DEVICE",
        device_class: "I",
        regulatory_subject: [
          { organization_name: "软件测试厂家", role_code: "DEVICE_FILER" },
        ],
        manufacturer: [
          { organization_name: "软件测试厂家", role_code: "MANUFACTURER" },
        ],
        product_certificates: [
          {
            certificate_id: certificate.id,
            certificate_version: Number(certificate.version),
            business_use: true,
          },
        ],
      },
      reason: "软件验收补齐监管身份",
    },
  );
  product.version = String(master.version);
  const party = await command(context, owner, "/api/v1/catalog/parties", {
    entity_id: entity,
    code: "SC-" + suffix,
    name: customer,
    credit_code: "DEMO-CREDIT",
    kind: "BOTH",
    license_no: "DEMO-ONLY",
    valid_from: `${year}-01-01`,
    valid_until: `${year + 2}-12-31`,
    categories: ["DEVICE"],
  });
  for (const [kind, id] of [
    ["products", product.id],
    ["parties", party.id],
  ])
    await command(reviewer, checker, `/api/v1/catalog/${kind}/${id}/review`, {
      expected_version: kind === "products" ? product.version : "1",
      decision: "APPROVE",
      evidence_ref: "DEMO-EVIDENCE",
      reason: "仅供软件测试的虚拟资质",
    });
  const choices = await read(
    context,
    `/api/v1/purchasing/options?entity_id=${entity}`,
  );
  const warehouse = destination?.warehouse || choices.warehouses[0].id,
    location =
      destination?.location ||
      choices.locations.find((l: any) => l.warehouse_id === warehouse).id;
  const purchase = await command(context, owner, "/api/v1/purchase-orders", {
    entity_id: entity,
    supplier_id: party.id,
    warehouse_id: warehouse,
    lines: [
      {
        sku_id: product.sku_id,
        qty: "10",
        unit_price: "13",
        tax_rate: "0.13",
      },
    ],
  });
  await command(
    reviewer,
    checker,
    `/api/v1/purchase-orders/${purchase.id}/approve`,
    { expected_version: "1", reason: "审核采购" },
  );
  let incoming = await read(context, `/api/v1/purchase-orders/${purchase.id}`);
  const receipt = await command(
    context,
    owner,
    `/api/v1/purchase-orders/${purchase.id}/receipts`,
    {
      expected_version: incoming.order.version,
      shipment_ref: "DELIVERY-" + suffix,
      transport_note: "常温随货核查",
      lines: [
        {
          order_line_id: incoming.lines[0].id,
          location_id: location,
          qty: "10",
          lot_no: lot,
          mfg_date: `${year}-01-01`,
          expiry_date: `${year + 1}-12-31`,
        },
      ],
    },
  );
  incoming = await read(context, `/api/v1/purchase-orders/${purchase.id}`);
  const inspection = await command(
    reviewer,
    checker,
    `/api/v1/receipts/${receipt.id}/inspections`,
    {
      expected_version: "1",
      note: "全部合格",
      lines: [
        {
          receipt_line_id: incoming.receipts[0].lines[0].id,
          qualified_qty: "10",
          rejected_qty: "0",
        },
      ],
    },
  );
  await command(
    reviewer,
    checker,
    `/api/v1/receipt-postings/${inspection.receipt_posting_id}/post`,
    {
      expected_version: "1",
      inspection_id: inspection.inspection_id,
      posting_segment_id: inspection.receipt_posting_id,
    },
  );

  return {
    suffix,
    year,
    name,
    customer,
    lot,
    entity,
    product,
    party,
    warehouse,
    purchase,
    receipt,
  };
}
