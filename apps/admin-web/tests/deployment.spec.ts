import { test, expect } from "@playwright/test";
import { request as httpRequest } from "node:http";

const origin = "http://127.0.0.1:5173";

test("trial gateway serves the built application and live service readiness", async ({
  request,
}) => {
  for (const service of ["web", "core", "auth"]) {
    const response = await request.get(`${origin}/health/${service}`);
    expect(response.status()).toBe(200);
    expect((await response.json()).status).toBe("ok");
  }
  const response = await request.get(origin + "/");
  const html = await response.text();
  expect(response.headers()["cache-control"]).toBe("no-store");
  expect(html).not.toContain("/@vite/client");
  const asset = html.match(/src="(\/assets\/[^\"]+\.js)"/);
  expect(asset).toBeTruthy();
  const script = await request.get(origin + asset![1]);
  expect(script.status()).toBe(200);
  expect(script.headers()["content-type"]).toContain("javascript");
  expect(script.headers()["cache-control"]).toContain("immutable");
  expect(
    (
      await request.get(origin + "/assets/missing-deployment-check.js")
    ).status(),
  ).toBe(404);
  expect((await request.get(origin + "/api/v1/context")).status()).toBe(401);
});

test("trial gateway rejects raw encoded and dot traversal paths", async () => {
  // node:http retains raw paths; a browser URL parser would normalize dot segments.
  for (const path of [
    "/identity/resources/../admin/",
    "/identity/resources/%2e%2e/admin/",
    "/identity/resources/%252e%252e/admin/",
    "/identity/realms/medinv/../../master/",
    "/identity/resources/%5c..%5cadmin/",
  ]) {
    const status = await new Promise<number | undefined>((resolve, reject) => {
      const call = httpRequest(
        { hostname: "127.0.0.1", port: 5173, path },
        (response) => {
          response.resume();
          response.on("end", () => resolve(response.statusCode));
        },
      );
      call.setTimeout(5000, () =>
        call.destroy(new Error("Gateway request timed out")),
      );
      call.on("error", reject);
      call.end();
    });
    expect(status, path).toBe(404);
  }
});
