import { nav, wholesale } from "./business-helpers";
import { test, expect } from "@playwright/test";
import { login, headers, view, fillLoginForm } from "./helpers";
import { resolve } from "node:path";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";

const origin = "http://127.0.0.1:5173";

test("login page renders at desktop and mobile sizes", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "欢迎进入药械云" }),
  ).toBeVisible();
  await page.screenshot({
    path: "../../artifacts/login-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("button", { name: "登录工作台", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "../../artifacts/login-mobile.png",
    fullPage: true,
  });
});

test("website login form handles errors, login and logout without leaving the site", async ({
  page,
  context,
}) => {
  const navigationOrigins: string[] = [];
  page.on("framenavigated", (frame) => {
    if (frame === page.mainFrame() && frame.url().startsWith("http"))
      navigationOrigins.push(new URL(frame.url()).origin);
  });
  await page.goto("/");
  await expect(
    page.getByRole("textbox", { name: "企业账号", exact: true }),
  ).toBeVisible();
  const formAction = await page
    .locator("#kc-form-login")
    .getAttribute("action");
  expect(new URL(formAction!).origin).toBe(origin);
  const authorize = new URL(page.url());
  expect(authorize.searchParams.get("response_type")).toBe("code");
  expect(authorize.searchParams.get("code_challenge_method")).toBe("S256");
  await page
    .getByRole("textbox", { name: "企业账号", exact: true })
    .fill("demo.owner");
  await page.locator("#password").fill("incorrect-password-for-test");
  await page.getByRole("button", { name: "登录工作台", exact: true }).click();
  await expect(
    page.getByText("账号或密码不正确，请重新输入。", { exact: true }),
  ).toBeVisible();
  expect((await context.request.get(origin + "/auth/session")).status()).toBe(
    401,
  );
  await fillLoginForm(page, "demo.owner");
  await page.getByRole("button", { name: "登录工作台", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "企业工作台", exact: true }),
  ).toBeVisible();
  const current = await view(context.request);
  expect(current.tenants.length).toBeGreaterThanOrEqual(2);
  expect(current).not.toHaveProperty("access_token");
  expect(current).not.toHaveProperty("refresh_token");
  expect(
    (await context.cookies()).find((cookie) => cookie.name === "MED_SESSION")
      ?.httpOnly,
  ).toBe(true);
  await page.getByRole("button", { name: "退出", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "登录工作台", exact: true }),
  ).toBeVisible();
  expect((await context.request.get(origin + "/auth/session")).status()).toBe(
    401,
  );
  expect(navigationOrigins.length).toBeGreaterThan(2);
  expect(navigationOrigins.every((item) => item === origin)).toBe(true);
});

test("login callback failure offers retry without redirect loops", async ({
  page,
}) => {
  await page.goto("/?login_error=1");
  await expect(
    page.getByText("登录未完成，请重新尝试或检查企业成员身份"),
  ).toBeVisible();
  await page.getByRole("link", { name: "使用企业账号登录" }).click();
  await expect(
    page.getByRole("button", { name: "登录工作台", exact: true }),
  ).toBeVisible();
  expect(new URL(page.url()).origin).toBe(origin);
});

test("public identity proxy exposes only the application realm and theme", async ({
  request,
}) => {
  for (const path of [
    "/identity",
    "/identity/admin/",
    "/identity/realms/master/",
    "/identity/realms/other/",
    "/identity/resources/%2e%2e/admin/",
    "/identity/health",
    "/identity/metrics",
  ]) {
    expect((await request.get(origin + path)).status()).toBe(404);
  }
  const response = await request.get(
    origin + "/identity/realms/medinv/.well-known/openid-configuration",
  );
  expect(response.status()).toBe(200);
  const discovery = await response.json();
  expect(discovery.issuer).toBe(origin + "/identity/realms/medinv");
  for (const field of [
    "authorization_endpoint",
    "token_endpoint",
    "jwks_uri",
    "revocation_endpoint",
  ]) {
    expect(new URL(discovery[field]).origin).toBe(origin);
  }
});

test("real login, tenant fencing, stock lookup, member edit and logout", async ({
  page,
  context,
  browser,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  let initial = await login(context, "demo.owner");
  expect(initial.tenants.length).toBeGreaterThanOrEqual(2);
  const seeded = initial.tenants.find(
    (t: any) => t.name === "安心药械批发 · 演示",
  );
  expect(seeded).toBeTruthy();
  if (initial.tenant_id !== seeded.id) {
    const response = await context.request.post(origin + "/auth/tenant", {
      headers: headers(initial),
      data: { tenant_id: seeded.id },
    });
    expect(response.status()).toBe(200);
    initial = (await response.json()).data;
  }
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "企业工作台", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "../../artifacts/workspace-desktop.png",
    fullPage: true,
  });
  await nav(page, "库存查询");
  // New warehouses may sort before the seeded warehouse. Select this test's stock explicitly.
  await page
    .getByRole("combobox", { name: "库存仓库", exact: true })
    .press("Enter");
  await page
    .getByRole("option", {
      name: initial.tenant_name.includes("批发") ? "中心仓库" : "门店库房",
      exact: true,
    })
    .click();
  await page
    .getByRole("combobox", { name: "库存商品", exact: true })
    .fill("DEMO-SKU");
  await page.getByRole("option", { name: / · DEMO-SKU$/ }).click();
  await page.getByRole("button", { name: "查询库存", exact: true }).click();
  await expect(page.getByText("可分配", { exact: true })).toBeVisible();
  await page.screenshot({
    path: "../../artifacts/inventory-desktop.png",
    fullPage: true,
  });

  const before = await view(context.request);
  const other = before.tenants.find((t: any) => t.id !== before.tenant_id);
  await page.getByRole("combobox", { name: "切换企业" }).press("Enter");
  await page.getByRole("option", { name: other.name }).click();
  await expect(
    page.getByRole("heading", { name: other.name, exact: true }),
  ).toBeVisible();
  const current = await view(context.request);
  expect(current.context_generation).not.toBe(before.context_generation);
  const stale = await context.request.get(origin + "/control/members", {
    headers: headers(before),
  });
  expect(stale.status()).toBe(409);
  const foreign = await context.request.get(
    origin + "/api/v1/inventory/options?entity_id=" + before.entities[0].id,
    { headers: headers(current) },
  );
  expect(foreign.status()).toBe(404);

  const wholesale = current.tenants.find((t: any) => t.name.includes("批发"));
  if (current.tenant_id !== wholesale.id) {
    await page.getByRole("combobox", { name: "切换企业" }).press("Enter");
    await page.getByRole("option", { name: wholesale.name }).click();
    await expect(
      page.getByRole("heading", { name: wholesale.name, exact: true }),
    ).toBeVisible();
  }
  const viewer = await browser.newContext();
  try {
    await login(viewer, "demo.viewer");
    await nav(page, "成员与权限");
    const memberRow = page.getByRole("row").filter({ hasText: "演示只读成员" });
    await expect(memberRow).toBeVisible();
    await memberRow.getByRole("button", { name: "编辑权限" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    // Save unchanged permissions: version increments and old sessions must be revoked.
    await page.getByRole("button", { name: "保存权限", exact: true }).click();
    await expect(page.getByRole("dialog")).not.toBeVisible();
    expect((await viewer.request.get(origin + "/auth/session")).status()).toBe(
      401,
    );
  } finally {
    await viewer.close();
  }
  await page.screenshot({
    path: "../../artifacts/members-desktop.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "退出", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "欢迎进入药械云" }),
  ).toBeVisible();
  expect((await context.request.get(origin + "/auth/session")).status()).toBe(
    401,
  );
  expect(errors).toEqual([]);
});

test("viewer sees inventory but cannot administer members", async ({
  page,
  context,
}) => {
  const session = await wholesale(context, "demo.viewer");
  expect(
    session.tenants.some((t: { id: string }) => t.id === session.tenant_id),
  ).toBe(true);
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "企业工作台", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "成员与权限", exact: false }),
  ).toHaveCount(0);
  expect(
    (
      await context.request.get(origin + "/control/members", {
        headers: headers(session),
      })
    ).status(),
  ).toBe(403);
  await nav(page, "库存查询");
  // New warehouses may sort before the seeded warehouse. Select this test's stock explicitly.
  await page
    .getByRole("combobox", { name: "库存仓库", exact: true })
    .press("Enter");
  await page.getByRole("option", { name: "中心仓库", exact: true }).click();
  await page
    .getByRole("combobox", { name: "库存商品", exact: true })
    .fill("DEMO-SKU");
  await page.getByRole("option", { name: / · DEMO-SKU$/ }).click();
  await page.getByRole("button", { name: "查询库存", exact: true }).click();
  await expect(page.getByText("1200", { exact: true }).first()).toBeVisible();
  await page.getByRole("button", { name: "退出", exact: true }).click();
});

test("real Keycloak rotates refresh tokens twice and rejects expired access", async ({
  context,
}) => {
  const current = await login(context, "demo.owner");
  const cookie = (await context.cookies()).find(
    (c) => c.name === "MED_SESSION",
  )!;
  const token_hash = createHash("sha256").update(cookie.value).digest("hex");
  const python = resolve(
    "../../backend/.venv",
    process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
  );
  function probe(expire: boolean) {
    return JSON.parse(
      execFileSync(python, [resolve("../../scripts/demo_session_probe.py")], {
        cwd: resolve("../.."),
        input: JSON.stringify({ token_hash, expire }),
        encoding: "utf8",
        timeout: 10000,
      }),
    );
  }
  try {
    for (let iteration = 0; iteration < 2; iteration++) {
      const old = probe(true);
      expect(
        (await context.request.get(origin + "/api/v1/context")).status(),
      ).toBe(401);
      const refreshed = await context.request.post(origin + "/auth/refresh", {
        headers: headers(current),
      });
      expect(refreshed.status()).toBe(200);
      const fresh = probe(false);
      expect(fresh.version).toBe(old.version + 1);
      expect(fresh.refresh_digest).not.toBe(old.refresh_digest);
      expect(
        (await context.request.get(origin + "/api/v1/context")).status(),
      ).toBe(200);
    }
  } finally {
    await context.request.post(origin + "/auth/logout", {
      headers: headers(current),
    });
  }
  expect((await context.request.get(origin + "/auth/session")).status()).toBe(
    401,
  );
});
