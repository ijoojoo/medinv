import { expect, test, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { command, nav, read, wholesale } from "./business-helpers";
import { headers } from "./helpers";

async function openParty(page: Page, id: string, code: string) {
  await page.goto("/");
  await nav(page, "往来单位");
  const list = page.getByRole("region", { name: "基础资料列表" });
  await expect(
    list.getByRole("button", { name: "查询", exact: true }),
  ).toBeEnabled();
  await list.getByLabel("查询资料").fill(code);
  await list.getByLabel("查询资料").press("Enter");
  await list.locator(`button[data-catalog-record="${id}"]`).click();
  const detail = page.getByRole("region", { name: "资料详情", exact: true });
  await detail.getByRole("tab", { name: /^质量管控/ }).click();
  const card = detail.getByRole("region", {
    name: "往来单位证照与授权",
    exact: true,
  });
  await expect(card.getByRole("button", { name: "重新加载证照" })).toHaveCount(
    0,
  );
  return card;
}

test("party multi-certificate and authorization lifecycle preserves admission and daily contacts", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(120000);
  const owner = await wholesale(context, "demo.owner");
  const suffix = randomUUID().slice(0, 8),
    code = `PC-${suffix}`;
  const year = new Date().getFullYear();
  const item = await command(context, owner, "/api/v1/catalog/parties", {
    entity_id: owner.entities[0].id,
    code,
    name: `多证照验收-${suffix}`,
    credit_code: code,
    kind: "BOTH",
    license_no: "LEGACY-TEST",
    categories: ["DEVICE"],
    valid_from: `${year}-01-01`,
    valid_until: `${year + 2}-12-31`,
    contact_name: "原日常联系人",
    contact_phone: "010-12345678",
    organization: {},
  });
  const reviewer = await browser.newContext();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  try {
    const quality = await wholesale(reviewer, "demo.quality");
    await command(
      reviewer,
      quality,
      `/api/v1/catalog/parties/${item.id}/review`,
      {
        expected_version: item.version,
        decision: "APPROVE",
        evidence_ref: "TEST-PARENT",
        reason: "软件验收原有准入",
      },
    );
    const before = (await read(context, `/api/v1/catalog/parties/${item.id}`))
      .record;
    const root = `/api/v1/catalog/parties/${item.id}/certificates`;
    const card = await openParty(page, item.id, code);
    await expect(card).toContainText("LEGACY-TEST");
    await card.getByRole("button", { name: "新增证照", exact: true }).click();
    const form = card.getByRole("form", { name: "证照编辑", exact: true });
    for (const [label, value] of [
      ["证照编号", "BUSINESS-" + suffix],
      ["证照主体 / 授权单位", "软件验收单位"],
      ["发证机关 / 签发单位", "软件验收签发方"],
      ["原件凭据编号", "FILE-TEST"],
      ["原件经营范围", "仅为软件验收的原件范围"],
      ["证照操作说明", "补录营业执照"],
    ])
      await form.getByLabel(label, { exact: true }).fill(value);
    await form
      .getByRole("combobox", { name: "有效期方式", exact: true })
      .selectOption("LONG_TERM");
    await form
      .getByRole("button", { name: "保存证照草稿", exact: true })
      .click();
    await expect(form).not.toBeVisible();
    await expect(card.locator("tbody").first()).toContainText("原件载明长期");
    await card.getByRole("button", { name: "新增证照", exact: true }).click();
    await form
      .getByRole("combobox", { name: "证照类型", exact: true })
      .selectOption("AUTHORIZATION");
    for (const [label, value] of [
      ["证照编号", "AUTH-" + suffix],
      ["证照主体 / 授权单位", "软件验收单位"],
      ["发证机关 / 签发单位", "软件验收授权方"],
      ["原件凭据编号", "AUTH-FILE"],
      ["授权联系人", "授权张女士"],
      ["授权联系电话", "010-87654321"],
      ["授权事项", "仅供货及业务洽谈"],
      ["证照操作说明", "按授权原件建档"],
    ])
      await form.getByLabel(label, { exact: true }).fill(value);
    await form.getByLabel("作为客户购货", { exact: true }).uncheck();
    await form
      .getByRole("combobox", { name: "有效期方式", exact: true })
      .selectOption("FIXED");
    await form
      .getByRole("textbox", { name: "有效期起始日期", exact: true })
      .fill(`${year}0101`);
    await form
      .getByRole("textbox", { name: "有效期截止日期", exact: true })
      .fill(`${year + 1}1231`);
    const detail = page.getByRole("region", { name: "资料详情", exact: true });
    // Tabs retain the editor; leaving the party page uses its own unsaved guard.
    await detail.getByRole("tab", { name: "基本信息", exact: true }).click();
    await detail.getByRole("tab", { name: /^质量管控/ }).click();
    await expect(form.getByLabel("授权联系人", { exact: true })).toHaveValue(
      "授权张女士",
    );
    await detail
      .getByRole("button", { name: "← 返回资料列表", exact: true })
      .click();
    const close = page.getByRole("dialog", {
      name: "关闭前保存修改",
      exact: true,
    });
    await close.getByRole("button", { name: "取消关闭", exact: true }).click();
    await expect(form.getByLabel("授权事项", { exact: true })).toHaveValue(
      "仅供货及业务洽谈",
    );
    await form
      .getByRole("button", { name: "保存证照草稿", exact: true })
      .click();
    await expect(form).not.toBeVisible();
    let rows = (await read(context, root)).items;
    expect(rows).toHaveLength(2);
    const auth = rows.find((r: any) => r.certificate_type === "AUTHORIZATION");
    expect(auth.business_uses).toEqual(["SUPPLY"]);
    await card
      .getByRole("button", { name: "核验 / 停用", exact: true })
      .click();
    await expect(
      form
        .getByRole("combobox", { name: "证照处理", exact: true })
        .locator('option[value="VERIFY"]'),
    ).toHaveJSProperty("disabled", true);
    await form
      .getByRole("button", { name: "取消证照编辑", exact: true })
      .click();
    const qualityPage = await reviewer.newPage();
    qualityPage.on("pageerror", (e) => errors.push(e.message));
    const qualityCard = await openParty(qualityPage, item.id, code);
    expect(
      await qualityCard
        .getByRole("button", { name: "新增证照", exact: true })
        .count(),
    ).toBe(0);
    await qualityCard
      .locator("tbody tr")
      .filter({ hasText: "AUTH-" + suffix })
      .getByRole("button", { name: "查看证照", exact: true })
      .click();
    await qualityCard
      .getByRole("button", { name: "核验 / 停用", exact: true })
      .click();
    const qualityForm = qualityCard.getByRole("form", {
      name: "证照编辑",
      exact: true,
    });
    await qualityForm
      .getByLabel("核验凭据编号", { exact: true })
      .fill("REVIEW-TEST");
    await qualityForm
      .getByLabel("证照操作说明", { exact: true })
      .fill("独立核对授权原件");
    await qualityForm
      .getByRole("button", { name: "提交证照核验", exact: true })
      .click();
    await expect(qualityForm).not.toBeVisible();
    await expect(qualityCard.locator("tbody").first()).toContainText("已核验");
    await card.getByRole("button", { name: "刷新证照", exact: true }).click();
    await expect(card.getByRole("region", { name: "证照详情" })).toContainText(
      "第 2 版",
    );
    await card.getByRole("button", { name: "修订证照", exact: true }).click();
    await form.getByLabel("授权事项", { exact: true }).fill("仅供货");
    await form.getByLabel("证照操作说明", { exact: true }).fill("收窄授权事项");
    await form
      .getByRole("button", { name: "保存证照草稿", exact: true })
      .click();
    await expect(form).not.toBeVisible();
    rows = (await read(context, root)).items;
    expect(rows.find((r: any) => r.id === auth.id).state).toBe("DRAFT");
    const history = await read(context, `${root}/${auth.id}`);
    expect(history.history).toHaveLength(3);
    expect(history.history[0].detail.before.authorization_duties).toBe(
      "仅供货及业务洽谈",
    );
    expect(history.history[0].detail.after.authorization_duties).toBe("仅供货");
    expect(
      (await read(context, `/api/v1/catalog/parties/${item.id}`)).record,
    ).toEqual(before);
    await card.getByText("证照变更记录", { exact: true }).click();
    await card.getByText(/证照修订（待重新核验）/).click();
    await expect(card.locator(".catalog-history table").first()).toContainText(
      "授权事项",
    );
    await page.screenshot({
      path: "../../outputs/party-certificates/多证照与授权历史.png",
      fullPage: true,
    });
    await command(
      context,
      owner,
      `/api/v1/catalog/parties/${item.id}/operations`,
      {
        expected_version: before.version,
        values: { contact_name: "新日常联系人" },
        reason: "更新日常联系方式",
      },
    );
    expect(
      (await read(context, root)).items.find((r: any) => r.id === auth.id),
    ).toMatchObject({ authorized_name: "授权张女士", version: "3" });
    const after = (await read(context, `/api/v1/catalog/parties/${item.id}`))
      .record;
    expect(after.state).toBe("APPROVED");
    expect(after.contact_name).toBe("新日常联系人");
    expect(errors).toEqual([]);
  } finally {
    await reviewer.close();
  }
});

test("viewer reads expired verified evidence but cannot write; stopped records retain history", async ({
  page,
  context,
  browser,
}) => {
  const owner = await wholesale(context, "demo.owner");
  const suffix = randomUUID().slice(0, 8),
    code = `PV-${suffix}`;
  const item = await command(context, owner, "/api/v1/catalog/parties", {
    entity_id: owner.entities[0].id,
    code,
    name: `证照只读验收-${suffix}`,
    credit_code: code,
    kind: "CUSTOMER",
    license_no: "LEGACY-TEST",
    categories: ["DEVICE"],
    valid_from: "2026-01-01",
    valid_until: "2030-12-31",
  });
  const root = `/api/v1/catalog/parties/${item.id}/certificates`;
  const payload = {
    certificate_type: "BUSINESS_LICENSE",
    number: "EXPIRED-" + suffix,
    holder_name: "软件验收单位",
    issuer: "测试签发方",
    business_uses: ["PURCHASE"],
    validity_mode: "FIXED",
    valid_from: "2020-01-01",
    valid_until: "2021-01-01",
    evidence_ref: "TEST-ORIGINAL",
    reason: "留存历史证照",
  };
  let record = await command(context, owner, root, payload);
  const qualityContext = await browser.newContext();
  const viewerContext = await browser.newContext();
  try {
    const quality = await wholesale(qualityContext, "demo.quality");
    record = await command(
      qualityContext,
      quality,
      `${root}/${record.id}/review`,
      {
        expected_version: record.version,
        decision: "VERIFY",
        evidence_ref: "TEST-REVIEW",
        reason: "历史资料与原件一致",
      },
    );
    const viewer = await wholesale(viewerContext, "demo.viewer");
    const viewerPage = await viewerContext.newPage();
    const card = await openParty(viewerPage, item.id, code);
    await expect(card.locator("tbody").first()).toContainText("已核验");
    await expect(card.locator("tbody").first()).toContainText("已到期");
    await expect(
      card.getByRole("button", { name: "新增证照", exact: true }),
    ).toHaveCount(0);
    await card.getByRole("button", { name: "查看证照", exact: true }).click();
    await expect(
      card.getByRole("button", { name: "修订证照", exact: true }),
    ).toHaveCount(0);
    await expect(
      card.getByRole("button", { name: "核验 / 停用", exact: true }),
    ).toHaveCount(0);
    const denied = await viewerContext.request.post(root, {
      headers: { ...headers(viewer), "Idempotency-Key": randomUUID() },
      data: payload,
    });
    expect(denied.status()).toBe(403);
    const ownerCard = await openParty(page, item.id, code);
    await ownerCard
      .getByRole("button", { name: "查看证照", exact: true })
      .click();
    await ownerCard
      .getByRole("button", { name: "核验 / 停用", exact: true })
      .click();
    const form = ownerCard.getByRole("form", { name: "证照编辑", exact: true });
    await form
      .getByRole("combobox", { name: "证照处理", exact: true })
      .selectOption("INACTIVATE");
    await form.getByLabel("核验凭据编号", { exact: true }).fill("STOP-TEST");
    await form.getByLabel("证照操作说明", { exact: true }).fill("旧证停用留档");
    await form
      .getByRole("button", { name: "提交证照核验", exact: true })
      .click();
    await expect(form).not.toBeVisible();
    await expect(ownerCard.locator("tbody").first()).toContainText("已停用");
    expect((await read(context, `${root}/${record.id}`)).history).toHaveLength(
      3,
    );
    expect(
      (await read(context, `/api/v1/catalog/parties/${item.id}`)).record.state,
    ).toBe("DRAFT");
  } finally {
    await qualityContext.close();
    await viewerContext.close();
  }
});
