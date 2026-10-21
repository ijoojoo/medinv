import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { reminderFixture } from "./reminder-fixture";
import {
  command,
  read,
  nav,
  origin,
  approveCatalogParent,
} from "./business-helpers";
import { headers } from "./helpers";

test("scope follow-up: claim, assign, exact source, unified renewal and verified close", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(240000);
  const f = await reminderFixture(context, browser);
  const errors: string[] = [];
  page.on("pageerror", (e) => {
    errors.push(e.message);
    console.log("PAGEERROR", e.message);
  });
  page.on("response", async (response) => {
    if (response.url().includes("/messages/") && response.status() >= 400)
      console.log("REMINDER_ERROR", response.status(), await response.text());
  });
  try {
    await page.goto(`/workspace/messages?tenant=${f.tenant.tenant_id}`);
    const messages = page.getByRole("region", {
      name: "消息和提醒",
      exact: true,
    });
    await messages
      .getByRole("button", { name: "经营范围异常", exact: true })
      .click();
    await expect(messages.locator(".message-row")).toHaveCount(1);
    await expect(messages).toContainText("第三类器械 06");
    await expect(messages).not.toContainText("第一类器械 06");
    await messages
      .getByRole("button", { name: "标记已读", exact: true })
      .click();
    await expect(messages).toContainText("待处理");
    await messages
      .getByRole("button", { name: "处理事项", exact: true })
      .click();
    const dialog = page.getByRole("dialog", { name: "提醒处理", exact: true });
    await dialog
      .getByLabel("处理说明", { exact: true })
      .fill("已联系供应商，跟进第三类器械证件续期");
    const claimResponse = page.waitForResponse(
      (r) => r.url().endsWith("/api/v1/messages/actions"),
      { timeout: 10000 },
    );
    await dialog.getByRole("button", { name: "认领处理", exact: true }).click();
    const reply = await claimResponse;
    expect(reply.status(), await reply.text()).toBe(200);
    await expect(dialog).toContainText("已联系供应商");
    await dialog
      .getByRole("combobox", { name: "处理动作", exact: true })
      .selectOption("ASSIGN");
    await dialog.locator(".el-select").click();
    await page.getByRole("option", { name: /QA-REM/ }).click();
    await dialog.getByLabel("计划完成日期", { exact: true }).fill(f.today);
    await dialog
      .getByLabel("处理说明", { exact: true })
      .fill("分派质量人员收集更新证件");
    await dialog
      .getByRole("button", { name: "分派负责人", exact: true })
      .click();
    await expect(dialog).toContainText("分派质量人员收集更新证件");
    await expect(dialog.locator('option[value="CLOSE"]')).toHaveJSProperty(
      "disabled",
      true,
    );
    await page.screenshot({
      path: "../../outputs/reminders/处理记录与责任分派.png",
      fullPage: true,
    });
    await dialog.getByRole("button", { name: "查看资料", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`record=${f.party.id}`));
    let detail = page.getByRole("region", { name: "资料详情", exact: true });
    await expect(
      detail.getByRole("tab", { name: /经营范围与准入/ }),
    ).toHaveAttribute("aria-selected", "true");
    await expect(detail).toContainText("提醒软件测试供应商");
    await page.reload();
    detail = page.getByRole("region", { name: "资料详情", exact: true });
    await expect(
      detail.getByRole("tab", { name: /经营范围与准入/ }),
    ).toHaveAttribute("aria-selected", "true");
    await f.renew();
    await nav(page, "消息和提醒");
    await messages
      .getByRole("button", { name: "经营范围异常", exact: true })
      .click();
    await expect(messages.locator(".message-row")).toHaveCount(1);
    await messages
      .getByRole("button", { name: "主体资料待审", exact: true })
      .click();
    await expect(messages).toContainText("资料变更待审核");
    await f.approve();
    await messages
      .getByRole("button", { name: "经营范围异常", exact: true })
      .click();
    await expect(messages.locator(".message-row")).toHaveCount(0);
    await messages.getByRole("button", { name: "处理中", exact: true }).click();
    await expect(messages).toContainText("已消除 · 待确认关闭");
    await expect(messages).toContainText("提醒测试负责人");
    await messages
      .getByRole("button", { name: "处理事项", exact: true })
      .click();
    await dialog
      .getByLabel("处理说明", { exact: true })
      .fill("续期资料已随主体审核，系统复核该类目已恢复");
    await dialog
      .getByRole("button", { name: "核查并关闭", exact: true })
      .click();
    await expect(dialog.locator(".case-condition")).toContainText("已关闭");
    await expect(dialog.locator(".case-history article")).toHaveCount(3);
    await page.screenshot({
      path: "../../outputs/reminders/核查关闭.png",
      fullPage: true,
    });
    await dialog.getByRole("button", { name: "关闭此对话框" }).click();
    await messages.getByRole("button", { name: "已关闭", exact: true }).click();
    await expect(messages.locator(".message-row")).toHaveCount(1);
    const viewerPage = await f.viewer.newPage();
    await viewerPage.goto(`/workspace/messages?tenant=${f.tenant.tenant_id}`);
    const viewerMessages = viewerPage.getByRole("region", {
      name: "消息和提醒",
      exact: true,
    });
    await viewerMessages
      .getByRole("button", { name: "经营范围异常", exact: true })
      .click();
    await viewerMessages
      .getByRole("button", { name: "已关闭", exact: true })
      .click();
    await expect(
      viewerMessages.getByRole("button", { name: "处理事项", exact: true }),
    ).toHaveCount(0);
    await viewerMessages
      .getByRole("button", { name: "处理记录", exact: true })
      .click();
    await expect(
      viewerPage.getByRole("dialog", { name: "提醒处理" }),
    ).toContainText("续期资料已随主体审核");
    expect(errors).toEqual([]);
  } finally {
    await f.dispose();
  }
});

test("certificate and maintenance alerts open the exact record and remain usable on narrow screens", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(180000);
  const f = await reminderFixture(context, browser);
  const errors: string[] = [];
  page.on("pageerror", (e) => {
    errors.push(e.message);
    console.log("PAGEERROR", e.message);
  });
  page.on("response", async (response) => {
    if (response.url().includes("/messages/") && response.status() >= 400)
      console.log("REMINDER_ERROR", response.status(), await response.text());
  });
  try {
    const maintenance = await command(
      context,
      f.owner,
      `/api/v1/management/maintenance/${randomUUID()}`,
      {
        entity_id: f.tenant.entity_id,
        expected_version: "0",
        reason: "测试养护提醒",
        payload: {
          product_id: f.product.id,
          lot_no: "REM-LOT-1",
          checked_on: f.today,
          next_due: f.today,
          operator_name: "软件测试人员",
          checks: "外观、包装、储存条件",
          result: "NORMAL",
          state: "COMPLETED",
        },
      },
    );
    await command(
      context,
      f.owner,
      `/api/v1/catalog/products/${f.product.id}/certificates`,
      {
        certificate_type: "DEVICE_FILING",
        number: "TEST-PRODUCT-CERT",
        holder_name: "测试备案人",
        issuer: "测试机关",
        validity_mode: "FIXED",
        valid_from: `${f.year - 1}-01-01`,
        valid_until: f.today,
        evidence_ref: "TEST",
        reason: "测试商品证件提醒",
      },
    );
    await approveCatalogParent(f.reviewer, f.quality, "products", f.product.id);
    await page.goto(`/workspace/messages?tenant=${f.tenant.tenant_id}`);
    const messages = page.getByRole("region", {
      name: "消息和提醒",
      exact: true,
    });
    await messages
      .getByRole("button", { name: "资质到期", exact: true })
      .click();
    const product = messages
      .locator(".message-row")
      .filter({ hasText: "TEST-PRODUCT-CERT" });
    await expect(product).toBeVisible();
    await product
      .getByRole("button", { name: "查看资料", exact: true })
      .click();
    await expect(page).toHaveURL(new RegExp(`record=${f.product.id}`));
    await expect(page.locator(".el-dialog:visible")).toContainText(
      "TEST-PRODUCT-CERT",
    );
    await page.reload();
    await expect(page.locator(".el-dialog:visible")).toContainText(
      "TEST-PRODUCT-CERT",
    );
    await page.keyboard.press("Escape");
    await nav(page, "消息和提醒");
    await messages
      .getByRole("button", { name: "养护提醒", exact: true })
      .click();
    await messages
      .getByRole("button", { name: "查看资料", exact: true })
      .click();
    await expect(page).toHaveURL(new RegExp(`record=${maintenance.id}`));
    const ledger = page.getByRole("region", { name: "质量台账", exact: true });
    await expect(ledger.getByLabel("批号", { exact: true })).toHaveValue(
      "REM-LOT-1",
    );
    await page.reload();
    await expect(ledger.getByLabel("批号", { exact: true })).toHaveValue(
      "REM-LOT-1",
    );
    await nav(page, "消息和提醒");
    await messages
      .getByRole("button", { name: "养护提醒", exact: true })
      .click();
    await page.setViewportSize({ width: 768, height: 1000 });
    await expect(
      messages.getByRole("button", { name: "处理事项", exact: true }),
    ).toBeVisible();
    expect(
      await messages.evaluate((el) => el.scrollWidth <= el.clientWidth + 2),
    ).toBe(true);
    await page.screenshot({
      path: "../../outputs/reminders/窄屏提醒.png",
      fullPage: true,
    });
    const item = (
      await read(
        context,
        `/api/v1/messages?entity_id=${f.tenant.entity_id}&source=maintenance`,
      )
    ).items[0];
    const denied = await f.viewer.request.post(
      origin + "/api/v1/messages/actions",
      {
        headers: { ...headers(f.reader), "Idempotency-Key": randomUUID() },
        data: {
          entity_id: f.tenant.entity_id,
          source: item.source,
          source_key: item.source_key,
          expected_version: "0",
          observed_revision: item.source_revision,
          action: "CLAIM",
          reason: "只读权限测试",
        },
      },
    );
    expect(denied.status()).toBe(403);
    expect(errors).toEqual([]);
  } finally {
    await f.dispose();
  }
});
