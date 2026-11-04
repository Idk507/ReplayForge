import assert from "node:assert/strict";
import { test } from "node:test";
import { sessionBridge } from "../lib/session-bridge.ts";
import { publicOrigin } from "../lib/public-origin.mjs";
import { loginUrl } from "../lib/auth.ts";

test("public login points at same-origin OAuth route and fails closed on bad configuration", () => {
  const oldMode = process.env.REPLAYFORGE_DEPLOYMENT_MODE;
  const oldOrigin = process.env.REPLAYFORGE_PUBLIC_ORIGIN;
  try {
    process.env.REPLAYFORGE_DEPLOYMENT_MODE = "PUBLIC_DEPLOYMENT";
    process.env.REPLAYFORGE_PUBLIC_ORIGIN = "https://demo.example.com";
    assert.equal(loginUrl(), "https://demo.example.com/api/v1/auth/github/start");
    process.env.REPLAYFORGE_PUBLIC_ORIGIN = "https://*.example.com";
    assert.equal(loginUrl(), null);
    process.env.REPLAYFORGE_DEPLOYMENT_MODE = "bad";
    assert.equal(loginUrl(), null);
  } finally {
    if (oldMode === undefined) delete process.env.REPLAYFORGE_DEPLOYMENT_MODE;
    else process.env.REPLAYFORGE_DEPLOYMENT_MODE = oldMode;
    if (oldOrigin === undefined) delete process.env.REPLAYFORGE_PUBLIC_ORIGIN;
    else process.env.REPLAYFORGE_PUBLIC_ORIGIN = oldOrigin;
  }
});

test("public origin rejects wildcard and IP hosts", () => {
  for (const origin of ["https://*.example.com", "https://127.0.0.2", "https://[::1]", "https://example.com/path", "http://example.com"]) {
    assert.equal(publicOrigin(origin), null);
  }
  assert.equal(publicOrigin("https://demo.example.com")?.origin, "https://demo.example.com");
});

test("public session bridge accepts only the configured origin and secure session cookie", async () => {
  const previous = { ...process.env };
  const originalFetch = globalThis.fetch;
  let forwarded;
  try {
    process.env.REPLAYFORGE_DEPLOYMENT_MODE = "PUBLIC_DEPLOYMENT";
    process.env.REPLAYFORGE_PUBLIC_ORIGIN = "https://demo.example.com";
    process.env.REPLAYFORGE_API_URL = "http://127.0.0.1:8109";
    globalThis.fetch = async (_url, options) => {
      forwarded = new Headers(options.headers);
      return Response.json({ ok: true });
    };
    const cookie = `__Host-rf_session=${"s".repeat(43)}`;
    const request = (host, extra = {}) => new Request("http://127.0.0.1/api", {
      headers: { host, cookie, ...extra },
    });
    assert.equal((await sessionBridge(request("demo.example.com"), ["auth", "me"])).status, 200);
    assert.equal(forwarded.get("cookie"), cookie);
    assert.equal(forwarded.get("host"), "demo.example.com");
    assert.equal(forwarded.get("x-forwarded-proto"), "https");
    await sessionBridge(request("demo.example.com", { "x-forwarded-proto": "http", "x-forwarded-host": "evil.example.com" }), ["auth", "me"]);
    assert.equal(forwarded.get("x-forwarded-proto"), "https");
    assert.equal(forwarded.has("x-forwarded-host"), false);
    assert.equal((await sessionBridge(request("evil.example.com"), ["auth", "me"])).status, 503);
    assert.equal((await sessionBridge(request("demo.example.com", { origin: "https://evil.example.com" }), ["auth", "me"])).status, 403);
    assert.equal((await sessionBridge(request("demo.example.com", { cookie: `rf_dev_session=${"s".repeat(43)}` }), ["auth", "me"])).status, 401);
    assert.equal((await sessionBridge(new Request("http://127.0.0.1/api", { method: "POST", headers: { host: "demo.example.com", cookie }, body: "{}" }), ["runs"])).status, 403);
    process.env.REPLAYFORGE_PUBLIC_ORIGIN = "http://demo.example.com";
    assert.equal((await sessionBridge(request("demo.example.com"), ["auth", "me"])).status, 503);
    process.env.REPLAYFORGE_PUBLIC_ORIGIN = "https://*.example.com";
    assert.equal((await sessionBridge(request("demo.example.com"), ["auth", "me"])).status, 503);
    process.env.REPLAYFORGE_DEPLOYMENT_MODE = "invalid";
    assert.equal((await sessionBridge(request("demo.example.com"), ["auth", "me"])).status, 503);
    delete process.env.REPLAYFORGE_DEPLOYMENT_MODE;
    const local = new Request("http://127.0.0.1:3000/api", { headers: {
      host: "127.0.0.1:3000", cookie: `rf_dev_session=${"d".repeat(43)}`,
    } });
    assert.equal((await sessionBridge(local, ["auth", "me"])).status, 200);
    assert.equal(forwarded.get("cookie"), `rf_dev_session=${"d".repeat(43)}`);
    assert.equal(forwarded.has("x-forwarded-proto"), false);
  } finally {
    globalThis.fetch = originalFetch;
    for (const key of ["REPLAYFORGE_DEPLOYMENT_MODE", "REPLAYFORGE_PUBLIC_ORIGIN", "REPLAYFORGE_API_URL"]) {
      if (previous[key] === undefined) delete process.env[key];
      else process.env[key] = previous[key];
    }
  }
});
