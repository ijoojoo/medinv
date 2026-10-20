import {
  expect,
  type APIRequestContext,
  type BrowserContext,
  type Page,
} from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
const origin = "http://127.0.0.1:5173";
const values = Object.fromEntries(
  readFileSync(resolve("../../deploy/.env"), "utf8")
    .split(/\r?\n/)
    .filter((l) => l && !l.startsWith("#"))
    .map((l) => {
      const i = l.indexOf("=");
      return [l.slice(0, i), l.slice(i + 1)];
    }),
);

// The test runner disables traces and video. Never expose development passwords
// in screenshots, reports, source files or page URLs.
export async function fillLoginForm(page: Page, username: string) {
  await page
    .getByRole("textbox", { name: "企业账号", exact: true })
    .fill(username);
  await expect(page.locator("#password")).toBeVisible();
  await page.evaluate((password) => {
    const input = document.querySelector<HTMLInputElement>("#password")!;
    input.value = password;
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }, values.DEMO_USER_PASSWORD!);
}

// Exercise real authorization-code + PKCE login using the local seeded identity.
// HTTP form submission keeps development credentials out of screenshots and traces.
export async function login(context: BrowserContext, username: string) {
  const response = await context.request.get(origin + "/auth/login");
  expect(new URL(response.url()).origin).toBe(origin);
  const html = await response.text();
  const match =
    html.match(/<form[^>]*id="kc-form-login"[^>]*action="([^"]+)"/) ||
    html.match(/<form[^>]*action="([^"]+)"[^>]*id="kc-form-login"/);
  expect(match, "Keycloak login form").toBeTruthy();
  const action = match![1]!.replaceAll("&amp;", "&");
  expect(new URL(action).origin).toBe(origin);
  await context.request.post(action, {
    form: { username, password: values.DEMO_USER_PASSWORD!, credentialId: "" },
  });
  const view = await context.request.get(origin + "/auth/session");
  expect(view.status(), "Real OIDC login creates a server session").toBe(200);
  return (await view.json()).data;
}
export function headers(view: any) {
  return {
    Origin: origin,
    "X-Context-Generation": view.context_generation,
    "X-CSRF-Token": view.csrf_token,
  };
}
export async function view(request: APIRequestContext) {
  return (await (await request.get(origin + "/auth/session")).json()).data;
}
