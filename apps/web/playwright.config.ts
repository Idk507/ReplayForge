import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "./tests", timeout: 30000, fullyParallel: false, workers: 1,
  reporter: [["list"], ["junit", {outputFile: "test-results/results.xml"}]],
  use: {baseURL: "http://127.0.0.1:3000", ...devices["Desktop Chrome"], channel: "msedge", screenshot: "only-on-failure"},
  webServer: {command: "npm run start", url: "http://127.0.0.1:3000", reuseExistingServer: false, timeout: 60000, env: {REPLAYFORGE_API_TOKEN: process.env.REPLAYFORGE_API_TOKEN ?? "synthetic-local-testing-token-12345678", REPLAYFORGE_API_URL: process.env.REPLAYFORGE_API_URL ?? "http://127.0.0.1:8109"}},
});
