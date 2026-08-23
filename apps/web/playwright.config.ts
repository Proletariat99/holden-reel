import { defineConfig } from "@playwright/test";

delete process.env.FORCE_COLOR;
delete process.env.NO_COLOR;

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results/playwright",
  globalSetup: "./e2e/global-setup.ts",
  fullyParallel: false,
  workers: 1,
  timeout: 120_000,
  expect: {
    timeout: 10_000,
  },
  use: {
    trace: "retain-on-failure",
  },
});
