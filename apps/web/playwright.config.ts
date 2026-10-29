import { defineConfig, devices } from "@playwright/test";
const port = Number(process.env.PLAYWRIGHT_WEB_PORT ?? 3000);
const apiUrl = process.env.PLAYWRIGHT_API_URL ?? "http://127.0.0.1:8109";
export default defineConfig({
  testDir: "./tests", timeout: 30000, fullyParallel: false, workers: 1,
  reporter: [["list"], ["junit", {outputFile: "test-results/results.xml"}]],
  use: {baseURL: `http://127.0.0.1:${port}`, ...devices["Desktop Chrome"], channel: "msedge", screenshot: "only-on-failure"},
  webServer: {command: "npm run start", url: `http://127.0.0.1:${port}`, reuseExistingServer: false, timeout: 60000, env: {PORT: String(port), REPLAYFORGE_API_TOKEN: process.env.REPLAYFORGE_API_TOKEN ?? "synthetic-local-testing-token-12345678", REPLAYFORGE_API_URL: apiUrl}},
});
