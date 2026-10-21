import { expect, test, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { command, nav, read, wholesale } from "./business-helpers";
import { headers } from "./helpers";

const png = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a2ioAAAAASUVORK5CYII=",
  "base64",
);
const pdf = Buffer.from("%PDF-1.4\n1 0 obj <</Type /Catalog>> endobj\n%%EOF\n");
async function open(page: Page, area: string, id: string, code: string) {
  await page.goto("/");
  if (area === "parties") {
    await nav(page, "往来单位");
    const list = page.getByRole("region", { name: "基础资料列表" });
    await expect(
      list.getByRole("button", { name: "查询", exact: true }),
    ).toBeEnabled();
    await list.getByLabel("查询资料").fill(code);
    await list.getByLabel("查询资料").press("Enter");
    await list.locator(`button[data-catalog-record="${id}"]`).click();
    await page
      .getByRole("region", { name: "资料详情", exact: true })
      .getByRole("tab", { name: /^质量管控/ })
      .click();
  } else {
    await nav(page, "商品档案");
    await page.getByLabel("检索商品主数据", { exact: true }).fill(code);
    await page.getByRole("button", { name: "查询", exact: true }).click();
    await page
      .locator(".master-list-table tr")
      .filter({ hasText: code })
      .getByRole("button", { name: "维护", exact: true })
      .click();
    await page
      .getByRole("button", { name: "证件详情与维护", exact: true })
      .click();
    await page
      .getByText("维护证件原件 / 续期（随商品审核）", { exact: true })
      .click();
  }
  const card = page.getByRole("region", {
    name: area === "parties" ? "往来单位证照与授权" : "商品证照档案",
    exact: true,
  });
  await card
    .getByRole("button", { name: "查看证照", exact: true })
    .first()
    .click();
  return card;
}

for (const area of ["parties", "products"] as const) {
  test(`${area}: private upload preview replacement and certificate history`, async ({
    page,
    context,
    browser,
  }) => {
    test.setTimeout(120000);
    const owner = await wholesale(context, "demo.owner");
    const code = `ATT-${randomUUID().slice(0, 8)}`;
    const dates = { valid_from: "2026-01-01", valid_until: "2028-12-31" };
    const parent = await command(
      context,
      owner,
      `/api/v1/catalog/${area}`,
      area === "parties"
        ? {
            ...dates,
            entity_id: owner.entities[0].id,
            code,
            name: `原件附件验收-${code}`,
            credit_code: code,
            kind: "SUPPLIER",
            license_no: "TEST",
            categories: ["DEVICE"],
            organization: {},
          }
        : {
            code,
            name: `原件附件验收-${code}`,
            ...dates,
            category: "DEVICE",
            device_class: "I",
            manufacturer_name: "软件测试厂家",
            specification: "测试型号",
            base_unit: "个",
            approval_number: "TEST",
          },
    );
    const root = `/api/v1/catalog/${area}/${parent.id}`;
    const cert = await command(context, owner, root + "/certificates", {
      certificate_type:
        area === "parties" ? "BUSINESS_LICENSE" : "DEVICE_FILING",
      number: "FILE-TEST",
      holder_name: "软件测试主体",
      issuer: "软件测试机关",
      validity_mode: "NOT_STATED",
      evidence_ref: "TEST-ARCHIVE",
      reason: "软件验收建档",
      ...(area === "parties" ? { business_uses: ["SUPPLY"] } : {}),
    });
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    const card = await open(page, area, parent.id, code);
    const files = card.getByRole("region", { name: "私有附件" });
    await expect(files).toContainText("尚未上传附件");
    await files.getByLabel("选择附件", { exact: true }).setInputFiles({
      name: "营业执照.png",
      mimeType: "image/png",
      buffer: png,
    });
    await files.getByLabel("附件上传说明", { exact: true }).fill("第一份原件");
    if (area === "parties") {
      await page
        .getByRole("button", { name: "← 返回资料列表", exact: true })
        .click();
      await page
        .getByRole("dialog", { name: "关闭前保存修改", exact: true })
        .getByRole("button", { name: "取消关闭", exact: true })
        .click();
      await expect(
        files.getByLabel("附件上传说明", { exact: true }),
      ).toHaveValue("第一份原件");
    }
    // Lose one committed response, then retry the unchanged file with the same derived key.
    if (area === "parties") {
      let intercepted = false;
      await page.route("**/attachments?*", async (route) => {
        if (route.request().method() === "POST" && !intercepted) {
          intercepted = true;
          await route.fetch();
          await route.abort("failed");
        } else await route.continue();
      });
      await files
        .getByRole("button", { name: "上传附件", exact: true })
        .click();
      await expect(
        files.getByRole("button", { name: "上传附件", exact: true }),
      ).toBeEnabled();
      await page.unroute("**/attachments?*");
    }
    await files.getByRole("button", { name: "上传附件", exact: true }).click();
    await expect(files.locator("tbody tr")).toContainText(
      "营业执照.png · 第 1 版",
    );
    let rows = (await read(context, root + "/attachments")).items;
    expect(rows).toHaveLength(1);
    const first = rows[0];
    await files
      .getByRole("button", { name: "查看 / 下载附件", exact: true })
      .click();
    const preview = page.getByRole("dialog", {
      name: "营业执照.png",
      exact: true,
    });
    await expect(
      preview.getByRole("img", { name: "营业执照.png" }),
    ).toBeVisible();
    await expect(
      preview.getByRole("img", { name: "营业执照.png" }),
    ).toHaveJSProperty("naturalWidth", 1);
    await preview.getByRole("button", { name: "关闭此对话框" }).click();
    await card.getByRole("button", { name: "修订证照", exact: true }).click();
    await files
      .getByRole("combobox", { name: "证照关联附件版本", exact: true })
      .selectOption(first.id);
    await card
      .getByLabel("证照操作说明", { exact: true })
      .fill("关联第一版原件");
    await card
      .getByRole("button", { name: "保存证照草稿", exact: true })
      .click();
    await expect(
      card.getByRole("form", { name: "证照编辑" }),
    ).not.toBeVisible();
    await files
      .getByRole("combobox", { name: "上传方式", exact: true })
      .selectOption(first.id);
    await files.getByLabel("选择附件", { exact: true }).setInputFiles({
      name: "营业执照扫描.pdf",
      mimeType: "application/pdf",
      buffer: pdf,
    });
    await files
      .getByLabel("附件上传说明", { exact: true })
      .fill("补充完整扫描件");
    await files.getByRole("button", { name: "上传附件", exact: true }).click();
    await expect(files.locator("tbody tr")).toHaveCount(2);
    rows = (await read(context, root + "/attachments")).items;
    const second = rows.find((r: any) => r.version === 2);
    expect(
      (await read(context, root + `/certificates/${cert.id}`)).record
        .attachment_id,
    ).toBe(first.id);
    const downloadWait = page.waitForEvent("download");
    await files
      .locator(`tr[data-attachment-id="${second.id}"]`)
      .getByRole("button", { name: "查看 / 下载附件" })
      .click();
    expect((await downloadWait).suggestedFilename()).toBe("营业执照扫描.pdf");
    await card.getByRole("button", { name: "修订证照", exact: true }).click();
    await files
      .getByRole("combobox", { name: "证照关联附件版本", exact: true })
      .selectOption(second.id);
    await card.getByLabel("证照操作说明", { exact: true }).fill("更新原件关联");
    await card
      .getByRole("button", { name: "保存证照草稿", exact: true })
      .click();
    await expect(
      card.getByRole("form", { name: "证照编辑" }),
    ).not.toBeVisible();
    const detail = await read(context, root + `/certificates/${cert.id}`);
    expect(detail.record.state).toBe("DRAFT");
    expect(detail.history[0].detail.before.attachment_id).toBe(first.id);
    expect(detail.history[0].detail.after.attachment_id).toBe(second.id);
    const old = await context.request.get(
      root + `/attachments/${first.id}/content`,
      { headers: headers(owner) },
    );
    expect(old.status()).toBe(200);
    expect(await old.body()).toEqual(png);
    await card.getByText("证照变更记录", { exact: true }).click();
    await card
      .locator(".catalog-history details")
      .first()
      .locator("summary")
      .click();
    await expect(
      card
        .locator(".catalog-history details")
        .first()
        .getByRole("button", { name: "查看 / 下载附件" }),
    ).toHaveCount(2);
    mkdirSync("../../outputs/private-attachments", { recursive: true });
    await files.scrollIntoViewIfNeeded();
    await expect(page.locator(".el-message")).toHaveCount(0, {
      timeout: 15000,
    });
    await page.screenshot({
      path: `../../outputs/private-attachments/${area}-附件版本.png`,
    });
    const viewer = await browser.newContext();
    try {
      const view = await wholesale(viewer, "demo.viewer");
      const viewPage = await viewer.newPage();
      const viewCard = await open(viewPage, area, parent.id, code);
      expect(
        await viewCard
          .getByRole("button", { name: "上传附件", exact: true })
          .count(),
      ).toBe(0);
      const forbidden = await viewer.request.post(
        root + "/attachments?filename=x.pdf&reason=test",
        {
          headers: {
            ...headers(view),
            "Idempotency-Key": randomUUID(),
            "Content-Type": "application/octet-stream",
          },
          data: pdf,
        },
      );
      expect(forbidden.status()).toBe(403);
    } finally {
      await viewer.close();
    }
    expect(errors).toEqual([]);
  });
}
