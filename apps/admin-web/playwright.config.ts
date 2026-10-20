import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  timeout: 45000,
  reporter: "list",
  outputDir: "../../artifacts/browser-tests",
  use: {
    actionTimeout: 10000,
    channel: process.platform === "win32" ? "msedge" : undefined,
    baseURL: "http://127.0.0.1:5173",
    viewport: { width: 1440, height: 1000 },
    screenshot: "only-on-failure",
    trace: "off",
    video: "off",
  },
});
