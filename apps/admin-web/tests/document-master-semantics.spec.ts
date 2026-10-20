import { test, expect } from "@playwright/test";
import { mkdir, readFile } from "node:fs/promises";
import {
  wholesale,
  read,
  command,
  nav,
  chooseEntrySku,
} from "./business-helpers";

const subjectLabel = "持有人／注册人／备案人";
const payload = (version: string, columns: any[]) => ({
  expected_version: version,
  columns: columns.map(({ required, editable, ...c }) => c),
});

test("mixed drug and device lines share one subject in editor, restored draft, history and CSV", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(180000);
  const owner = await wholesale(context, "demo.owner"),
    entity = owner.entities[0].id;
  const reviewer = await browser.newContext();
  const checker = await wholesale(reviewer, "demo.quality");
  const editorPath = `/api/v1/document-columns/orders/editor?entity_id=${entity}`;
  const viewPath = `/api/v1/document-columns/orders/view?entity_id=${entity}`;
  const originalEditor = await read(context, editorPath);
  const originalView = await read(context, viewPath);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const suffix = Date.now().toString(36),
    year = new Date().getFullYear();
  let orderId = "";
  try {
    // Submit the old three keys as an already-open legacy client would.
    const columns = originalEditor.fields
      .map((f: any) => ({
        ...f.default,
        visible:
          f.default.visible || ["code", "manufacturer_name"].includes(f.key),
      }))
      .filter((c: any) => c.key !== "regulatory_subject");
    const subject = originalEditor.fields.find(
      (f: any) => f.key === "regulatory_subject",
    ).default;
    columns.splice(
      1,
      0,
      {
        ...subject,
        key: "authorization_holder",
        label: "上市许可持有人",
        visible: true,
        width: 250,
      },
      { ...subject, key: "registrant_name", label: "注册人", visible: true },
      { ...subject, key: "filing_holder", label: "备案人", visible: true },
    );
    await command(
      context,
      owner,
      editorPath,
      payload(originalEditor.version, columns),
    );
    const configured = await read(context, editorPath);
    expect(
      configured.columns.filter((c: any) => c.key === "regulatory_subject"),
    ).toHaveLength(1);
    expect(configured.columns[1]).toMatchObject({
      key: "regulatory_subject",
      label: subjectLabel,
      width: 250,
    });
    const schema = await read(context, "/api/v1/catalog/master/schema");
    const examples = [
      {
        profile: "drug",
        category: "DRUG",
        subtype: "CHEMICAL",
        name: "药品",
        role: "DRUG_MAH",
        cert: "DRUG_APPROVAL",
        subject: "软件验收持有人",
      },
      {
        profile: "device",
        category: "DEVICE",
        subtype: "MEDICAL_DEVICE",
        name: "一类器械",
        device_class: "I",
        role: "DEVICE_FILER",
        cert: "DEVICE_FILING",
        subject: "软件验收备案人",
      },
      {
        profile: "device",
        category: "DEVICE",
        subtype: "MEDICAL_DEVICE",
        name: "二类器械",
        device_class: "II",
        role: "DEVICE_REGISTRANT",
        cert: "DEVICE_REGISTRATION",
        subject: "软件验收注册人",
      },
    ];
    const products: {
      id: string;
      sku: string;
      code: string;
      subject: string;
    }[] = [];
    for (const [i, example] of examples.entries()) {
      const values = {
        code: `SM-${suffix}-${i}`,
        name: `列映射验收-${example.name}-${suffix}`,
        base_unit: "盒",
        specification: "软件验收规格",
        regulatory_subtype: example.subtype,
        ...(example.device_class
          ? { device_class: example.device_class }
          : { drug_sale_class: "OTC" }),
        regulatory_subject: [
          { organization_name: example.subject, role_code: example.role },
        ],
        manufacturer: [
          {
            organization_name: "软件验收受托生产企业",
            role_code: "CONTRACT_MANUFACTURER",
          },
        ],
      };
      const saved = await command(
        context,
        owner,
        "/api/v1/catalog/master/products",
        {
          category_node_id: schema.payload.nodes.find(
            (n: any) => n.node_code === example.profile,
          ).node_id,
          form_revision: schema.revision,
          values,
        },
      );
      const certificate = await command(
        context,
        owner,
        `/api/v1/catalog/products/${saved.id}/certificates`,
        {
          certificate_type: example.cert,
          number: `TEST-SM-${suffix}-${i}`,
          holder_name: example.subject,
          issuer: "软件验收机关",
          validity_mode: "FIXED",
          valid_from: `${year}-01-01`,
          valid_until: `${year + 2}-12-31`,
          evidence_ref: "DEMO-EVIDENCE",
          reason: "软件验收虚拟证件",
        },
      );
      const verified = await command(
        reviewer,
        checker,
        `/api/v1/catalog/products/${saved.id}/certificates/${certificate.id}/review`,
        {
          expected_version: certificate.version,
          decision: "VERIFY",
          evidence_ref: "DEMO-EVIDENCE",
          reason: "软件验收核对",
        },
      );
      const current = await read(
        context,
        `/api/v1/catalog/master/products/${saved.id}`,
      );
      const updated = await command(
        context,
        owner,
        "/api/v1/catalog/master/products",
        {
          product_id: saved.id,
          category_node_id: current.category_node_id,
          form_revision: schema.revision,
          expected_version: Number(current.version),
          values: {
            product_certificates: [
              {
                certificate_id: certificate.id,
                certificate_version: Number(verified.version),
                business_use: true,
              },
            ],
          },
        },
      );
      await command(
        reviewer,
        checker,
        `/api/v1/catalog/products/${saved.id}/review`,
        {
          expected_version: String(updated.version),
          decision: "APPROVE",
          evidence_ref: "DEMO-EVIDENCE",
          reason: "软件验收核对",
        },
      );
      products.push({
        id: saved.id,
        sku: current.skus[0].id,
        code: values.code,
        subject: example.subject,
      });
    }
    const options = await read(
      context,
      `/api/v1/purchasing/options?entity_id=${entity}&include_skus=false`,
    );
    const supplier = await command(context, owner, "/api/v1/catalog/parties", {
      entity_id: entity,
      code: `SM-S-${suffix}`,
      name: `列映射验收供应商-${suffix}`,
      credit_code: "DEMO-CREDIT",
      kind: "SUPPLIER",
      license_no: "DEMO-ONLY",
      valid_from: `${year}-01-01`,
      valid_until: `${year + 2}-12-31`,
      categories: ["DRUG", "DEVICE"],
    });
    await command(
      reviewer,
      checker,
      `/api/v1/catalog/parties/${supplier.id}/review`,
      {
        expected_version: "1",
        decision: "APPROVE",
        evidence_ref: "DEMO-EVIDENCE",
        reason: "软件验收核对",
      },
    );
    const created = await command(context, owner, "/api/v1/purchase-orders", {
      entity_id: entity,
      supplier_id: supplier.id,
      warehouse_id: options.warehouses[0].id,
      lines: products.map((p) => ({
        sku_id: p.sku,
        qty: "2",
        unit_price: "10",
        tax_rate: "0.13",
      })),
    });
    orderId = created.id;
    const order = await read(context, `/api/v1/purchase-orders/${orderId}`);
    expect(
      order.lines.map((l: any) => l.document_values.regulatory_subject),
    ).toEqual(products.map((p) => p.subject));
    await page.goto("/");
    await nav(page, "采购入库");
    await page
      .getByRole("button", { name: "新建采购订单", exact: true })
      .click();
    const editor = page.getByRole("region", {
      name: "新建采购单",
      exact: true,
    });
    const rows = editor.locator(".entry-row");
    for (const [i, product] of products.entries()) {
      if (i)
        await editor
          .getByRole("button", { name: "添加商品行", exact: true })
          .click();
      await chooseEntrySku(rows.nth(i), product.sku);
      await rows.nth(i).getByLabel("采购数量", { exact: true }).fill("2");
      await rows
        .nth(i)
        .getByLabel("含税单价（元）", { exact: true })
        .fill("10");
      await expect(
        rows.nth(i).locator('[data-col="regulatory_subject"]'),
      ).toHaveText(product.subject);
    }
    await expect(
      editor.getByRole("columnheader", { name: subjectLabel, exact: true }),
    ).toHaveCount(1);
    await expect(
      editor.getByRole("columnheader", { name: "注册人", exact: true }),
    ).toHaveCount(0);
    await editor.getByRole("button", { name: "列设置", exact: true }).click();
    const settings = page.getByRole("dialog", {
      name: "配置单据明细列",
      exact: true,
    });
    for (const query of ["持有人", "注册人", "备案人"]) {
      await settings.getByLabel("搜索可选字段", { exact: true }).fill(query);
      await expect(
        settings.getByRole("checkbox", { name: subjectLabel, exact: true }),
      ).toHaveCount(1);
      for (const oldLabel of ["上市许可持有人", "注册人", "备案人"])
        await expect(
          settings.getByRole("checkbox", { name: oldLabel, exact: true }),
        ).toHaveCount(0);
      await expect(
        settings.getByRole("checkbox", { name: subjectLabel, exact: true }),
      ).toBeChecked();
    }
    await settings.getByRole("button", { name: "取消", exact: true }).click();
    await editor.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect(editor.locator(".editor-actions")).toContainText("草稿已保存");
    await page.reload();
    await expect(rows).toHaveCount(3);
    for (const [i, product] of products.entries())
      await expect(
        rows.nth(i).locator('[data-col="regulatory_subject"]'),
      ).toHaveText(product.subject);
    await mkdir("../../outputs/document-master-semantics", { recursive: true });
    await page.screenshot({
      path: "../../outputs/document-master-semantics/混合品类统一主体列.png",
      fullPage: true,
      animations: "disabled",
    });
    await editor.getByRole("button", { name: "导出明细", exact: true }).click();
    const exportDialog = page.getByRole("dialog", {
      name: "导出商品明细",
      exact: true,
    });
    await exportDialog.getByRole("radio", { name: /按当前显示列/ }).check();
    const download = page.waitForEvent("download");
    await exportDialog
      .getByRole("button", { name: "下载CSV", exact: true })
      .click();
    const csv = await readFile((await (await download).path())!, "utf8");
    expect(csv.split(subjectLabel)).toHaveLength(2);
    for (const product of products) expect(csv).toContain(product.subject);
    await editor
      .getByRole("button", { name: "返回采购记录", exact: true })
      .click();
    const list = page.locator(".purchase-worklist:visible");
    await list
      .getByRole("searchbox", { name: "查找单据" })
      .fill(order.order.number);
    await list.getByRole("button", { name: "查询单据" }).click();
    await list
      .getByRole("button", { name: order.order.number, exact: true })
      .click();
    const grid = page.getByRole("region", {
      name: "采购订单明细",
      exact: true,
    });
    await grid
      .getByRole("button", { name: "配置单据明细列", exact: true })
      .click();
    await settings
      .getByRole("button", { name: "从录入配置复制", exact: true })
      .click();
    await settings
      .getByRole("button", { name: "保存配置", exact: true })
      .click();
    await expect(
      grid.getByRole("columnheader", { name: subjectLabel, exact: true }),
    ).toHaveCount(1);
    for (const product of products)
      await expect(
        grid.getByText(product.subject, { exact: true }),
      ).toHaveCount(1);
    const historyDownload = page.waitForEvent("download");
    await grid
      .getByRole("button", { name: "导出当前视图", exact: true })
      .click();
    const historyCsv = await readFile(
      (await (await historyDownload).path())!,
      "utf8",
    );
    expect(historyCsv.split(subjectLabel)).toHaveLength(2);
    for (const product of products)
      expect(historyCsv).toContain(product.subject);
    expect(errors).toEqual([]);
  } finally {
    if (orderId) {
      const current = await read(context, `/api/v1/purchase-orders/${orderId}`);
      if (current.order.approval_state === "DRAFT")
        await command(
          context,
          owner,
          `/api/v1/purchase-orders/${orderId}/cancel`,
          {
            expected_version: current.order.version,
            reason: "列映射软件验收完成，不执行采购",
          },
        );
    }
    for (const [path, original] of [
      [editorPath, originalEditor],
      [viewPath, originalView],
    ] as const) {
      const current = await read(context, path);
      await command(
        context,
        owner,
        path,
        payload(current.version, original.columns),
      );
    }
    await reviewer.close();
  }
});
