import { expect, test, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { command, nav, read, wholesale } from "./business-helpers";

test("party overview shortcuts, grouped daily maintenance and extension cards preserve the approved identity", async ({
  page,
  context,
  browser,
}) => {
  test.setTimeout(120000);
  const owner = await wholesale(context, "demo.owner");
  const reviewer = await browser.newContext();
  const checker = await wholesale(reviewer, "demo.quality");
  const suffix = randomUUID().slice(0, 8),
    code = `PO-${suffix}`;
  const year = new Date().getFullYear();
  const item = await command(context, owner, "/api/v1/catalog/parties", {
    entity_id: owner.entities[0].id,
    code,
    name: `往来单位维护验收-${suffix}`,
    credit_code: code,
    kind: "BOTH",
    license_no: "TEST-ONLY",
    categories: ["DEVICE"],
    valid_from: `${year}-01-01`,
    valid_until: `${year + 2}-12-31`,
    short_name: "验收单位简称",
    contact_name: "原业务联系人",
    contact_phone: "010-12345678",
    delivery_address: "原收发货地址",
    registered_address: "软件验收注册地址",
    organization_type: "WHOLESALER",
  });
  const approved = await command(
    reviewer,
    checker,
    `/api/v1/catalog/parties/${item.id}/review`,
    {
      expected_version: item.version,
      decision: "APPROVE",
      evidence_ref: "TEST-ONLY",
      reason: "软件验收虚拟资料",
    },
  );
  const before = (await read(context, `/api/v1/catalog/parties/${item.id}`))
    .record;
  expect(before.state).toBe("APPROVED");
  expect(approved).toBeTruthy();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  async function openRecord(target: Page) {
    await target.goto("/");
    await nav(target, "往来单位");
    const list = target.getByRole("region", { name: "基础资料列表" });
    await expect(
      list.getByRole("button", { name: "查询", exact: true }),
    ).toBeEnabled();
    await list.getByLabel("查询资料").fill(code);
    await list.getByLabel("查询资料").press("Enter");
    const row = list.locator(`button[data-catalog-record="${item.id}"]`);
    await expect(row).toBeVisible();
    await row.click();
    return target.getByRole("region", { name: "资料详情", exact: true });
  }
  try {
    const detail = await openRecord(page);
    const contactSummary = detail.getByRole("region", {
      name: "日常联系",
      exact: true,
    });
    const identity = detail.getByRole("region", {
      name: "单位识别",
      exact: true,
    });
    await expect(contactSummary).toContainText("原收发货地址");
    await expect(identity).toContainText("软件验收注册地址");
    await expect(identity).toContainText("批发企业");
    const changes: { values: Record<string, string>; reason: string }[] = [];
    page.on("request", (request) => {
      if (
        request.method() === "POST" &&
        request.url().endsWith(`/${item.id}/operations`)
      )
        changes.push(request.postDataJSON());
    });
    // The shortcut must wait for its first read, then focus the requested input.
    let firstRead = true;
    await page.route(
      `**/api/v1/catalog/parties/${item.id}/operations`,
      async (route) => {
        if (route.request().method() === "GET" && firstRead) {
          firstRead = false;
          await new Promise((resolve) => setTimeout(resolve, 250));
        }
        await route.continue();
      },
    );
    await detail
      .getByRole("button", { name: "维护联系信息", exact: true })
      .click();
    const operations = detail.getByRole("region", { name: "经营信息卡片" });
    const contact = operations.getByLabel("联系人", { exact: true });
    await expect(contact).toBeFocused();
    for (const title of [
      "联系与收发货",
      "名称与检索",
      "分类与推荐",
      "业务备注",
    ])
      await expect(
        operations.getByRole("region", { name: title, exact: true }),
      ).toBeVisible();
    await contact.fill("新业务联系人");
    const phone = operations.getByLabel("联系电话", { exact: true });
    await phone.fill("不合法的号码");
    await operations.getByLabel("变更说明").fill("更新业务联系资料软件验收");
    await phone.press("Control+s");
    await expect(phone).toBeFocused();
    expect(
      await phone.evaluate(
        (input: HTMLInputElement) => input.validity.patternMismatch,
      ),
    ).toBe(true);
    expect(changes).toHaveLength(0);
    await phone.fill("+86 (010) 87654321 转 102");
    const address = "软件验收新收发货地址\n一号库收货处";
    await operations
      .getByLabel("默认收发货地址", { exact: true })
      .fill(address);
    // Switching to the overview shows the saved snapshot and explains pending edits.
    await detail.getByRole("tab", { name: "基本信息", exact: true }).click();
    await expect(detail.getByRole("status")).toContainText(
      "以下显示已保存内容",
    );
    await expect(contactSummary).toContainText("原业务联系人");
    await expect(contactSummary).not.toContainText("新业务联系人");
    await detail
      .getByRole("button", { name: "维护联系信息", exact: true })
      .click();
    await expect(contact).toBeFocused();
    await expect(contact).toHaveValue("新业务联系人");
    await contact.press("Control+s");
    await expect(operations).toContainText("已与当前档案同步");
    await detail.getByRole("tab", { name: "基本信息", exact: true }).click();
    await expect(contactSummary).toContainText("新业务联系人");
    await expect(contactSummary).toContainText(address);
    expect(changes).toHaveLength(1);
    expect(changes[0].values).toEqual({
      contact_name: "新业务联系人",
      contact_phone: "+86 (010) 87654321 转 102",
      delivery_address: address,
    });
    await expect(detail.getByRole("status")).toHaveCount(0);

    // Re-entering an already loaded card focuses the correct field; unsaved exit stays protected.
    await detail
      .getByRole("button", { name: "维护简称与检索", exact: true })
      .click();
    const shortName = operations.getByLabel("单位简称", { exact: true });
    await expect(shortName).toBeFocused();
    await shortName.fill("新的单位简称");
    await operations
      .getByLabel("检索别名 / 助记码", { exact: true })
      .fill(`daily-${suffix}`);
    await detail.getByRole("button", { name: "← 返回资料列表" }).click();
    const leave = page.getByRole("dialog", { name: "关闭前保存修改" });
    await leave.getByRole("button", { name: "取消关闭" }).click();
    await expect(shortName).toHaveValue("新的单位简称");
    await operations.getByLabel("变更说明").fill("更新简称与检索软件验收");
    const delayedDetail = page.waitForResponse(
      (response) =>
        response.request().method() === "GET" &&
        response.url().includes(`/api/v1/catalog/parties/${item.id}?`),
    );
    await page.route(
      `**/api/v1/catalog/parties/${item.id}?*`,
      async (route) => {
        await new Promise((resolve) => setTimeout(resolve, 600));
        await route.continue();
      },
      { times: 1 },
    );
    await detail.getByRole("button", { name: "← 返回资料列表" }).click();
    await leave.getByRole("button", { name: "保存后关闭" }).click();
    const list = page.getByRole("region", { name: "基础资料列表" });
    const row = list.locator(`button[data-catalog-record="${item.id}"]`);
    await expect(row).toBeFocused();
    await delayedDetail;
    await expect(detail).not.toBeVisible();
    await expect(row).toBeFocused();
    await expect(list).toContainText("新业务联系人");
    expect(changes).toHaveLength(2);
    const latest = (await read(context, `/api/v1/catalog/parties/${item.id}`))
      .record;
    expect(latest.short_name).toBe("新的单位简称");
    expect(latest.delivery_address).toBe(address);
    for (const key of [
      "id",
      "name",
      "credit_code",
      "kind",
      "organization_id",
      "registered_address",
      "license_no",
      "state",
      "revision_editors",
    ])
      expect(latest[key], key).toEqual(before[key]);

    await row.click();
    await expect(identity).toContainText("新的单位简称");
    for (const name of ["质量管控", "医保信息", "追溯信息", "第三方对接"]) {
      const entry = detail.getByRole("region", { name: "扩展资料入口" });
      await entry.getByRole("button", { name: new RegExp(`^${name}`) }).click();
      const tab = detail.getByRole("tab", { name, exact: true });
      await expect(tab).toHaveAttribute("aria-selected", "true");
      await expect(tab).toBeFocused();
      await detail.getByRole("tab", { name: "基本信息", exact: true }).click();
    }
    await page.screenshot({
      path: "../../outputs/party-overview/资料首页.png",
      fullPage: true,
    });
    await detail
      .getByRole("button", { name: "维护联系信息", exact: true })
      .click();
    await expect(contact).toBeFocused();
    await page.screenshot({
      path: "../../outputs/party-overview/分组经营信息.png",
      fullPage: true,
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(
      operations.getByLabel("默认收发货地址", { exact: true }),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
    await page.screenshot({
      path: "../../outputs/party-overview/窄屏经营信息.png",
      fullPage: true,
    });
    await detail.getByRole("tab", { name: "基本信息", exact: true }).click();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
    await page.screenshot({
      path: "../../outputs/party-overview/窄屏资料首页.png",
      fullPage: true,
    });

    // Review-only users can inspect the same information without a daily edit shortcut.
    const reviewPage = await reviewer.newPage();
    const reviewDetail = await openRecord(reviewPage);
    await reviewDetail
      .getByRole("tab", { name: "基本信息", exact: true })
      .click();
    await expect(
      reviewDetail.getByRole("region", { name: "日常联系", exact: true }),
    ).toContainText("新业务联系人");
    await expect(
      reviewDetail.getByRole("button", { name: "维护联系信息", exact: true }),
    ).toHaveCount(0);
    await reviewDetail
      .getByRole("tab", { name: "经营信息", exact: true })
      .click();
    await expect(
      reviewDetail.getByLabel("联系人", { exact: true }),
    ).toBeDisabled();
    expect(errors).toEqual([]);
  } finally {
    await reviewer.close();
  }
});
