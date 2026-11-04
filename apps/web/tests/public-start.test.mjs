import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { test } from "node:test";

test("public web startup fails closed when its trust configuration is missing", () => {
  const env = { ...process.env, REPLAYFORGE_DEPLOYMENT_MODE: "PUBLIC_DEPLOYMENT",
    REPLAYFORGE_WEB_AUTH_MODE: "github", REPLAYFORGE_PUBLIC_ORIGIN: "https://demo.example.com",
    REPLAYFORGE_API_URL: "http://127.0.0.1:8000" };
  for (const overrides of [
    { REPLAYFORGE_WEB_AUTH_MODE: "local" },
    { REPLAYFORGE_PUBLIC_ORIGIN: "https://*.example.com" },
    { REPLAYFORGE_API_URL: "https://remote.example.com" },
    { REPLAYFORGE_DEPLOYMENT_MODE: "LOCAL_DEVELOPMENT" },
  ]) {
    const result = spawnSync(process.execPath, ["scripts/start-public.mjs"], {
      cwd: process.cwd(), env: { ...env, ...overrides }, encoding: "utf8", timeout: 5000,
    });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /PUBLIC_WEB_CONFIG_INVALID/);
  }
});
