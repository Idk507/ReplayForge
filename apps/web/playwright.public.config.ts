import {defineConfig, devices} from "@playwright/test";

const configuredUrl = process.env.REPLAYFORGE_PUBLIC_TEST_URL ?? "";
let publicOrigin: string;

try {
  const parsed = new URL(configuredUrl);
  if (
    parsed.protocol !== "https:" ||
    parsed.username ||
    parsed.password ||
    parsed.port ||
    parsed.pathname !== "/" ||
    parsed.search ||
    parsed.hash ||
    !parsed.hostname.endsWith(".azurecontainerapps.io")
  ) {
    throw new Error("URL is outside the HTTPS Azure Container Apps origin contract");
  }
  publicOrigin = parsed.origin;
} catch {
  throw new Error("REPLAYFORGE_PUBLIC_TEST_URL must be an HTTPS Azure Container Apps origin");
}

export default defineConfig({
  testDir: "./tests",
  testMatch: "public-deployment.spec.ts",
  timeout: 30_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"], ["junit", {outputFile: "test-results/public-results.xml"}]],
  use: {
    baseURL: publicOrigin,
    ...devices["Desktop Chrome"],
    channel: "msedge",
    screenshot: "only-on-failure",
  },
});
