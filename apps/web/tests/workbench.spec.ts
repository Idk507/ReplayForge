import {test, expect} from "@playwright/test";
import {bridge} from "../lib/bridge";
import {parseEvents, researchSchema} from "../lib/contracts";
import {createHash} from "node:crypto";
import {readFileSync} from "node:fs";
import {resolve} from "node:path";

const runId = "12345678-1234-4234-8234-123456789abc";
const repo = "12345678-1234-4234-8234-123456789abd";
const synthetic = {run_id: runId, repository_id: repo, source_commit: "a".repeat(40), source_manifest_ref: null, current_stage: "INCONCLUSIVE", version: 2, trace_id: repo, evidence_manifest_ref: null, verification_ref: null, cancellation_requested: false, failure_reason: "INVALID_REPRODUCTION", remaining_budget: {model_calls: 14, sandbox_commands: 20, research_searches: 2, execution_seconds: 900, estimated_spend_microusd: 2000000}};

test("bridge rejects foreign origin, host, path and malformed query before network", async () => {
  for (const [url, headers, path, status] of [
    ["http://127.0.0.1:3000/api", {origin: "https://evil.example"}, ["runs"], 403],
    ["http://evil.example:3000/api", {}, ["runs"], 403],
    ["http://127.0.0.1:3000/api", {}, ["..","secrets"], 404],
    ["http://127.0.0.1:3000/api?after=-1", {}, ["runs",runId,"events"], 400],
  ] as const) {
    expect((await bridge(new Request(url, {headers}), [...path])).status).toBe(status);
  }
  expect((await bridge(new Request("http://127.0.0.1:3000/api", {method:"POST", body:"{}"}), ["runs"])).status).toBe(403);
});

test("local bridge allows only a digest-bound source-manifest read", async () => {
  const originalFetch = globalThis.fetch;
  const old = {
    mode: process.env.REPLAYFORGE_WEB_AUTH_MODE,
    url: process.env.REPLAYFORGE_API_URL,
    token: process.env.REPLAYFORGE_API_TOKEN,
  };
  delete process.env.REPLAYFORGE_WEB_AUTH_MODE;
  process.env.REPLAYFORGE_API_URL = "http://127.0.0.1:8109";
  process.env.REPLAYFORGE_API_TOKEN = "t".repeat(43);
  const calls: string[] = [];
  globalThis.fetch = async input => {
    calls.push(new URL(String(input)).pathname);
    return Response.json({source_commit:"a".repeat(40)});
  };
  try {
    const valid = await bridge(new Request("http://127.0.0.1:3000/api/backend"), ["runs",runId,"source-manifests","sha256:"+"b".repeat(64)]);
    expect(valid.status).toBe(200);
    const invalid = await bridge(new Request("http://127.0.0.1:3000/api/backend"), ["runs",runId,"source-manifests","sha256:"+"x".repeat(64)]);
    expect(invalid.status).toBe(404);
    expect(calls).toEqual([`/api/v1/runs/${runId}/source-manifests/sha256:${"b".repeat(64)}`]);
  } finally {
    globalThis.fetch = originalFetch;
    if (old.mode === undefined) delete process.env.REPLAYFORGE_WEB_AUTH_MODE; else process.env.REPLAYFORGE_WEB_AUTH_MODE = old.mode;
    if (old.url === undefined) delete process.env.REPLAYFORGE_API_URL; else process.env.REPLAYFORGE_API_URL = old.url;
    if (old.token === undefined) delete process.env.REPLAYFORGE_API_TOKEN; else process.env.REPLAYFORGE_API_TOKEN = old.token;
  }
});

test("SSE cursor ignores replayed IDs and rejects ID/body mismatch", () => {
  const frame = (id: number, sequence = id) => `id: ${id}\nevent: STAGE_CHANGED\ndata: ${JSON.stringify({sequence, event_type:"STAGE_CHANGED", stage:"INCONCLUSIVE", version:2, observed_at:"2026-10-20T10:00:00Z", trace_id:repo})}\n\n`;
  expect(parseEvents(frame(1)+frame(2), 1).map(e=>e.sequence)).toEqual([2]);
  expect(()=>parseEvents(frame(2,3),1)).toThrow();
  expect(()=>parseEvents(frame(3)+frame(2),1)).toThrow();
});

test("timeline displays persisted events when execution status is unavailable", async ({page}) => {
  await page.route("**/api/backend/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/executions")) return route.fulfill({status:503, body:"{}"});
    if (path.endsWith("/events")) return route.fulfill({contentType:"text/event-stream",body:`id: 1\nevent: CREATED\ndata: ${JSON.stringify({sequence:1,event_type:"CREATED",stage:"CREATED",version:0,observed_at:"2026-10-20T10:00:00Z",trace_id:repo})}\n\n`});
    if (path.endsWith("/research")) return route.fulfill({json:[]});
    if (path.endsWith("/patch")) return route.fulfill({status:404,body:"{}"});
    return route.fulfill({json:path.endsWith("/runs") ? [synthetic] : synthetic});
  });
  await page.goto("/");
  await page.getByRole("button", {name:/Run 12345678/}).click();
  await expect(page.locator(".timeline li")).toHaveCount(1);
  await expect(page.locator(".timeline li")).toContainText("CREATED");
});

test("synthetic contract: reconnect preserves cursor, missing gates stay missing, mobile has no overflow", async ({page}) => {
  let eventCalls = 0;
  const cursors: string[] = [];
  await page.route("**/api/backend/**", async route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/events")) {
      cursors.push(url.searchParams.get("after") ?? ""); eventCalls++;
      if (eventCalls === 2) return route.fulfill({status:503,body:"{}"});
      const seq = eventCalls === 1 ? 1 : 2;
      return route.fulfill({contentType:"text/event-stream",body:`id: ${seq}\nevent: STAGE_CHANGED\ndata: ${JSON.stringify({sequence:seq,event_type:"STAGE_CHANGED",stage:"INCONCLUSIVE",version:2,observed_at:"2026-10-20T10:00:00Z",trace_id:repo})}\n\n`});
    }
    if (url.pathname.endsWith("/research")) return route.fulfill({json:[]});
    if (url.pathname.endsWith("/patch")) return route.fulfill({status:404,body:"{}"});
    if (url.pathname.endsWith("/executions")) return route.fulfill({json:{run_id:runId,phases:[]}});
    return route.fulfill({json:url.pathname.endsWith("/runs") ? [synthetic] : synthetic});
  });
  await page.goto("/");
  await page.getByRole("button", {name:/Run 12345678/}).click();
  await expect(page.getByText("Version 2",{exact:false}).first()).toBeVisible();
  await expect(page.getByText(/Events resume automatically/)).toBeVisible({timeout:15000});
  await expect.poll(()=>eventCalls,{timeout:20000}).toBeGreaterThanOrEqual(3);
  expect(cursors.slice(0,3)).toEqual(["0","1","1"]);
  await page.getByRole("tab",{name:"evidence"}).click();
  await expect(page.getByText("MISSING",{exact:true})).toHaveCount(7);
  await page.setViewportSize({width:390,height:844});
  await expect(page.getByRole("heading",{name:"Verification matrix"})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
});

test("execution lifecycle labels known operations as unverified evidence", async ({page}) => {
  const operation = "00000000-0000-4000-8000-000000000031";
  await page.route("**/api/backend/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/executions")) return route.fulfill({json:{run_id:runId,phases:[
      {phase:"BASELINE",status:"PROVIDER_TERMINAL_UNVERIFIED",execution_id:"00000000-0000-4000-8000-000000000021",operation_id:"00000000-0000-4000-8000-000000000022",plan_ref:"sha256:"+"a".repeat(64),origin:"REMOTE_SERVICE",observed_at:"2026-10-28T10:00:00Z",can_authorize_execution:false,can_authorize_verification:false},
      {phase:"CANDIDATE",status:"OPERATION_PENDING",execution_id:"00000000-0000-4000-8000-000000000023",operation_id:operation,plan_ref:"sha256:"+"b".repeat(64),origin:"REMOTE_SERVICE",observed_at:"2026-10-28T10:01:00Z",can_authorize_execution:false,can_authorize_verification:false},
      {phase:"EXISTING_SUITE",status:"NOT_PLANNED",can_authorize_execution:false,can_authorize_verification:false},
      {phase:"CLEAN_REPLAY",status:"SUBMISSION_UNKNOWN",execution_id:"00000000-0000-4000-8000-000000000024",can_authorize_execution:false,can_authorize_verification:false},
    ]}});
    if (path.endsWith("/events")) return route.fulfill({contentType:"text/event-stream",body:""});
    if (path.endsWith("/research")) return route.fulfill({json:[]});
    if (path.endsWith("/patch")) return route.fulfill({status:404,json:{}});
    return route.fulfill({json:path.endsWith("/runs")?[synthetic]:synthetic});
  });
  await page.goto("/");
  await page.getByRole("button",{name:/Run 12345678/}).click();
  await page.getByRole("tab",{name:"evidence"}).click();
  await expect(page.getByRole("heading",{name:"Execution phases"})).toBeVisible();
  await expect(page.getByText("OPERATION PENDING",{exact:true})).toBeVisible();
  await expect(page.getByText("SUBMISSION UNKNOWN",{exact:true})).toBeVisible();
  await expect(page.getByText(/A known operation or retained receipt does not establish a verified result/)).toBeVisible();
  await page.getByText("Operation details").nth(1).click();
  await expect(page.getByText(operation)).toBeVisible();
  await expect(page.locator("details[open]").getByText("Can authorize verification")).toBeVisible();
});

test("synthetic contract: provider failure has a readable retry state", async ({page}) => {
  await page.route("**/api/backend/runs", route=>route.fulfill({status:503,body:"{}"}));
  await page.goto("/");
  await expect(page.getByRole("alert").filter({hasText:"Backend is not configured"})).toBeVisible();
  await expect(page.getByRole("button",{name:"Retry connection"})).toBeVisible();
});

test("real local API: create, inspect persisted events, cancel and reload", async ({page}) => {
  test.skip(!process.env.REPLAYFORGE_WEB_REPOSITORY_ID, "Run through scripts.test_web_acceptance for disposable real PostgreSQL/API acceptance");
  await page.goto("/");
  await page.getByRole("button",{name:"＋ New investigation"}).click();
  await page.getByLabel("Approved repository ID").fill(process.env.REPLAYFORGE_WEB_REPOSITORY_ID!);
  await page.getByLabel("Source commit").fill("a".repeat(40));
  await page.getByLabel("What happened?").fill("Tiny local acceptance incident. No source execution requested.");
  const delivered = page.waitForResponse(r => r.url().endsWith("/api/backend/runs") && r.request().method() === "POST");
  await page.getByRole("button",{name:"Create investigation →"}).click();
  const response = await delivered;
  expect(response.status(), await response.text()).toBe(202);
  await expect(page.getByRole("heading",{name:/Investigation [a-f0-9]{8}/})).toBeVisible();
  await expect(page.getByText("CREATED",{exact:true}).first()).toBeVisible();
  page.once("dialog", dialog=>dialog.accept());
  await page.getByRole("button",{name:"Cancel investigation"}).click();
  await expect(page.getByText("CANCELLED",{exact:true}).first()).toBeVisible();
  await page.reload();
  await page.getByRole("button",{name:/Run .*CANCELLED/}).click();
  await expect(page.locator(".run-title .badge")).toHaveText("CANCELLED");
  await page.getByRole("tab",{name:"evidence"}).click();
  await expect(page.getByText("MISSING",{exact:true})).toHaveCount(7);
  await page.screenshot({path:"test-results/real-run-desktop.png",fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:"test-results/real-run-mobile.png",fullPage:true});
});

test("synthetic contract: rejected gate, inert diff and hash-checked artifact preview", async ({page}) => {
  const content = "Small observed test log. No code execution.\n";
  const artifact = "sha256:" + createHash("sha256").update(content).digest("hex");
  const manifest = "sha256:" + "b".repeat(64);
  const names = ["BASELINE_REPRODUCED", "PATCHED_REGRESSION_PASS", "EXISTING_SUITE_PASS", "CLEAN_REPLAY_PASS", "PATCH_SCOPE_PASS", "ARTIFACT_INTEGRITY_PASS", "SECURITY_GATE_PASS"];
  const observed = {...synthetic, current_stage:"NOT_VERIFIED", verification_ref:"sha256:"+"c".repeat(64), evidence_manifest_ref:manifest};
  await page.route("**/api/backend/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/research")) return route.fulfill({json:[]});
    if (path.includes("/artifacts/")) return route.fulfill({body:content, contentType:"application/octet-stream"});
    if (path.includes("/evidence/")) return route.fulfill({json:{manifest_id:manifest,entries:[{name:"test.log",artifact_id:artifact,size:content.length}]}});
    if (path.endsWith("/executions")) return route.fulfill({json:{run_id:observed.run_id,phases:["BASELINE","CANDIDATE","EXISTING_SUITE","CLEAN_REPLAY"].map(phase=>({phase,status:"NOT_PLANNED",can_authorize_execution:false,can_authorize_verification:false}))}});
    if (path.endsWith("/verification")) return route.fulfill({json:{verification_ref:observed.verification_ref,verdict:{status:"NOT_VERIFIED",observed_at:"2026-10-20T10:00:00Z",can_authorize_publication:false,missing:[],failed:["SECURITY_GATE_PASS"]},gates:names.map((gate,index)=>({gate,phase:["BASELINE","CANDIDATE","EXISTING_SUITE","CLEAN_REPLAY","PATCH_SCOPE","ARTIFACT_INTEGRITY","SECURITY"][index],status:gate==="SECURITY_GATE_PASS"?"FAIL":"PASS",execution_ref:artifact,...(index===0?{execution_id:"00000000-0000-4000-8000-000000000001",operation_id:"00000000-0000-4000-8000-000000000002",image_uuid:"00000000-0000-4000-8000-000000000003",image_digest:artifact,configuration_ref:artifact,workspace_sha256:"a".repeat(64)}:{})}))}});
    if (path.endsWith("/patch")) return route.fulfill({json:{proposal:{unified_diff:"--- a/src/tiny.py\n+++ b/src/tiny.py\n- old\n+ <script>untrusted</script>",justification:"Synthetic proposal for display testing.",risk_notes:"Requires human review."}}});
    if (path.endsWith("/events")) return route.fulfill({contentType:"text/event-stream",body:": replay-page-complete\n\n"});
    return route.fulfill({json:path.endsWith("/runs") ? [observed] : observed});
  });
  await page.goto("/"); await page.getByRole("button",{name:/Run 12345678/}).click();
  await page.getByRole("tab",{name:"evidence"}).click();
  await expect(page.getByText("FAIL",{exact:true})).toHaveCount(1);
  await expect(page.getByText("Failed gates: SECURITY GATE PASS")).toBeVisible();
  const baselineDetails = page.locator(".gate-list").nth(1).locator(".gate-evidence").first();
  await baselineDetails.locator("summary").click();
  await expect(baselineDetails.getByText("Provider operation")).toBeVisible();
  await expect(page.getByText("00000000-0000-4000-8000-000000000002")).toBeVisible();
  await expect(page.getByText("00000000-0000-4000-8000-000000000003")).toBeVisible();
  await page.getByRole("button",{name:/test.log/}).click();
  await expect(page.locator("pre")).toContainText("Small observed test log");
  await page.getByRole("tab",{name:"Patch & risk"}).click();
  await expect(page.locator("pre")).toContainText("<script>untrusted</script>");
  await expect(page.locator("main script")).toHaveCount(0);
});

test("retained live research archive: timeline shows source, title and timestamp without a provider request", async ({page}) => {
  const bytes = readFileSync(resolve("../../docs/evidence/m3-live-research.json"));
  expect(createHash("sha256").update(bytes).digest("hex")).toBe("0272b42fec7ac4d270e256ae0fc6d5c56f2e5efd67ea7563e7f3766594202d0a");
  const archive: unknown = JSON.parse(bytes.toString("utf8"));
  const checked = researchSchema.parse([archive])[0];
  expect(checked.report.evidence_origin).toBe("REMOTE_SERVICE");
  expect(checked.report.sources).toHaveLength(2);
  await page.route("**/api/backend/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/research")) return route.fulfill({json:[archive]});
    if (path.endsWith("/executions")) return route.fulfill({json:{run_id:runId,phases:[]}});
    if (path.endsWith("/patch")) return route.fulfill({status:404,body:"{}"});
    if (path.endsWith("/events")) return route.fulfill({contentType:"text/event-stream",body:": replay-page-complete\n\n"});
    return route.fulfill({json:path.endsWith("/runs") ? [synthetic] : synthetic});
  });
  await page.goto("/");
  await page.getByRole("button",{name:/Run 12345678/}).click();
  const first = checked.report.sources[0];
  await expect(page.getByRole("link",{name:first.title + " ↗",exact:true})).toHaveAttribute("href",first.url);
  await expect(page.locator(`time[datetime="${first.retrieved_at}"]`).first()).toBeVisible();
  await expect(page.getByText(/External content is untrusted/)).toBeVisible();
});


test("queued incident explains worker handoff without claiming execution", async ({page})=>{
  const queued={...synthetic,current_stage:"CREATED",version:0,failure_reason:null};
  await page.route("**/api/backend/**",route=>{
    const path=new URL(route.request().url()).pathname;
    if(path.endsWith("/events"))return route.fulfill({contentType:"text/event-stream",body:""});
    if(path.endsWith("/executions"))return route.fulfill({json:{run_id:runId,phases:[]}});
    if(path.endsWith("/research"))return route.fulfill({json:[]});
    if(path.endsWith("/verification")||path.endsWith("/patch"))return route.fulfill({status:404,json:{}});
    return route.fulfill({json:path.endsWith("/runs")?[queued]:queued});
  });
  await page.goto("/");
  await page.getByRole("button",{name:/Run 12345678/}).click();
  await expect(page.getByText("Queued: waiting for a worker",{exact:true})).toBeVisible();
  await expect(page.getByText(/background worker must start collection/)).toBeVisible();
});

test("source acquisition is inspectable without exposing source bytes or changing missing gates", async ({page}) => {
  const sourceRef = "sha256:"+"d".repeat(64);
  const manifest = {
    schema_version:"1.0", repository_id:repo, source_commit:"a".repeat(40), root_tree:"b".repeat(40),
    evidence_origin:"REMOTE_SERVICE", commit_identity:"GITHUB_HTTPS_METADATA", raw_commit_verified:false,
    observed_at:"2026-10-26T12:00:00Z", trace_id:repo, policy_version:"repository-v1",
    files:[{path:"pyproject.toml",blob_id:"c".repeat(40),sha256:"e".repeat(64),size_bytes:2117}],
  };
  const collected = {...synthetic,current_stage:"TRIAGING",failure_reason:null,source_manifest_ref:sourceRef};
  await page.route("**/api/backend/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if(path.endsWith("/events")) return route.fulfill({contentType:"text/event-stream",body:""});
    if(path.endsWith("/executions")) return route.fulfill({json:{run_id:runId,phases:[]}});
    if(path.endsWith("/research")) return route.fulfill({json:[]});
    if(path.endsWith("/patch")) return route.fulfill({status:404,json:{}});
    if(path.includes("/source-manifests/")) return route.fulfill({json:manifest});
    return route.fulfill({json:path.endsWith("/runs")?[collected]:collected});
  });
  await page.goto("/");
  await page.getByRole("button",{name:/Run 12345678/}).click();
  await page.getByRole("tab",{name:"evidence"}).click();
  await expect(page.getByRole("heading",{name:"Source acquisition"})).toBeVisible();
  await expect(page.getByText("REMOTE_SERVICE",{exact:true})).toBeVisible();
  await expect(page.getByText("pyproject.toml",{exact:true})).toBeVisible();
  await expect(page.getByText("2,117 bytes",{exact:true})).toBeVisible();
  await expect(page.locator("body")).toContainText("e".repeat(64));
  await expect(page.getByText("No retained verdict",{exact:true})).toBeVisible();
  await expect(page.getByText("MISSING",{exact:true})).toHaveCount(7);
  await expect(page.locator("body")).not.toContainText("source file body");
});

for (const [stage, title] of [
  ["TRIAGING", "Context recorded: triage is not complete"],
  ["REPRODUCING", "Reproduction proposed: baseline not accepted"],
] as const) {
  test(`synthetic ${stage} checkpoint explains missing execution evidence`, async ({page}) => {
    const pending = {...synthetic, current_stage:stage, failure_reason:null};
    await page.route("**/api/backend/**", route => {
      const path = new URL(route.request().url()).pathname;
      if(path.endsWith("/events")) return route.fulfill({contentType:"text/event-stream",body:""});
      if(path.endsWith("/executions")) return route.fulfill({json:{run_id:runId,phases:[]}});
      if(path.endsWith("/research")) return route.fulfill({json:[]});
      if(path.endsWith("/patch")) return route.fulfill({status:404,json:{}});
      return route.fulfill({json:path.endsWith("/runs")?[pending]:pending});
    });
    await page.goto("/");
    await page.getByRole("button",{name:/Run 12345678/}).click();
    await expect(page.getByText(title,{exact:true})).toBeVisible();
    if(stage === "REPRODUCING") {
      await page.setViewportSize({width:390,height:844});
      expect(await page.evaluate(()=>document.body.scrollWidth<=window.innerWidth)).toBe(true);
      await page.screenshot({path:resolve("test-results/baseline-pending-mobile.png"),fullPage:true});
    }
    await page.getByRole("tab",{name:"evidence",exact:false}).click();
    await expect(page.getByText("No retained verdict")).toBeVisible();
  });
}

for (const [code, extra, title, body] of [
  ["WORKER_NOT_CLAIMING", {job_status: "queued", attempt: 0, age_seconds: 600, lease_seconds_left: null, dispatch_state: null, operator_action_required: false}, "Queued: no worker has claimed this run", /no progress is implied/],
  ["NEEDS_RECONCILIATION", {job_status: "leased", attempt: 1, age_seconds: 900, lease_seconds_left: 0, dispatch_state: "SENT_UNCERTAIN", operator_action_required: true}, "Collection needs operator reconciliation", /will not resend automatically/],
  ["COLLECTING", {job_status: "leased", attempt: 1, age_seconds: 20, lease_seconds_left: 25, dispatch_state: "SENT_UNCERTAIN", operator_action_required: false}, "Collecting source", /no files are shown until it is retained/],
] as const) {
  test(`diagnostics explain ${code} without implying progress`, async ({page}) => {
    const queued = {...synthetic, current_stage: "CREATED", version: 0, failure_reason: null};
    await page.route("**/api/backend/**", route => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith("/events")) return route.fulfill({contentType: "text/event-stream", body: ""});
      if (path.endsWith("/diagnostics")) return route.fulfill({json: {run_id: runId, stage: "CREATED", code, ...extra}});
      if (path.endsWith("/executions")) return route.fulfill({json: {run_id: runId, phases: []}});
      if (path.endsWith("/research")) return route.fulfill({json: []});
      if (path.endsWith("/verification") || path.endsWith("/patch")) return route.fulfill({status: 404, json: {}});
      return route.fulfill({json: path.endsWith("/runs") ? [queued] : queued});
    });
    await page.goto("/");
    await page.getByRole("button", {name: /Run 12345678/}).click();
    await expect(page.getByText(title, {exact: true})).toBeVisible();
    await expect(page.getByText(body)).toBeVisible();
    await expect(page.getByText("Verified", {exact: true})).toHaveCount(0);
  });
}

test("bridge allows only a GET for the read-only diagnostics path", async () => {
  const headers = {host: "127.0.0.1:3000"};
  const ok = await bridge(new Request("http://127.0.0.1:3000/api", {headers}), ["runs", runId, "diagnostics"]);
  // A backend 404 for an unknown run is fine; only the bridge's own denial is not.
  expect(((await ok.json().catch(() => ({}))) as {error_class?: string}).error_class).not.toBe("PATH_DENIED");
  const post = await bridge(new Request("http://127.0.0.1:3000/api", {method: "POST", headers: {...headers, origin: "http://127.0.0.1:3000"}, body: "{}"}), ["runs", runId, "diagnostics"]);
  expect(post.status).toBe(404);
});

const indexFixture = {
  source_commit: "a".repeat(40), evidence_origin: "TEST_TRANSPORT",
  files: [
    {path: "checkout/payments.py", blob_id: "c".repeat(40), sha256: "e".repeat(64), size_bytes: 480, language: "python", role: "SOURCE", parse_status: "PARSED", symbols: [{name: "PaymentService", kind: "class", line: 5, end_line: 9}, {name: "retry_charge", kind: "function", line: 12, end_line: 13}], imports: ["checkout.ledger", "uuid"], entry_point: false},
    {path: "tests/test_payments.py", blob_id: "d".repeat(40), sha256: "f".repeat(64), size_bytes: 90, language: "python", role: "TEST", parse_status: "PARSED", symbols: [], imports: ["checkout.payments"], entry_point: false},
  ],
  languages: {python: 2}, frameworks: ["fastapi", "pytest"], test_map: {"tests/test_payments.py": ["checkout/payments.py"]}, omissions: [],
  digest: "9".repeat(64), trust: "UNTRUSTED_REPOSITORY_DATA", can_authorize_execution: false,
};

async function routeCollected(page: import("@playwright/test").Page, index: unknown | null) {
  const collected = {...synthetic, current_stage: "TRIAGING", failure_reason: null, source_manifest_ref: "sha256:" + "d".repeat(64)};
  await page.route("**/api/backend/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/events")) return route.fulfill({contentType: "text/event-stream", body: ""});
    if (path.endsWith("/executions")) return route.fulfill({json: {run_id: runId, phases: []}});
    if (path.endsWith("/research")) return route.fulfill({json: []});
    if (path.endsWith("/repository-index")) return index ? route.fulfill({json: index}) : route.fulfill({status: 404, json: {}});
    if (path.includes("/source-manifests/")) return route.fulfill({json: {schema_version: "1.0", repository_id: repo, source_commit: "a".repeat(40), root_tree: "b".repeat(40), evidence_origin: "TEST_TRANSPORT", commit_identity: "GITHUB_HTTPS_METADATA", raw_commit_verified: false, observed_at: "2026-10-26T12:00:00Z", trace_id: repo, policy_version: "repository-v1", files: [{path: "README.md", blob_id: "c".repeat(40), sha256: "e".repeat(64), size_bytes: 10}]}});
    if (path.endsWith("/patch")) return route.fulfill({status: 404, json: {}});
    return route.fulfill({json: path.endsWith("/runs") ? [collected] : collected});
  });
}

test("analysis tab shows the retained index as untrusted static data", async ({page}) => {
  await routeCollected(page, indexFixture);
  await page.goto("/");
  await page.getByRole("button", {name: /Run 12345678/}).click();
  await page.getByRole("tab", {name: "analysis"}).click();
  await expect(page.getByRole("heading", {name: "Repository analysis"})).toBeVisible();
  await expect(page.getByText(/not a diagnosis/)).toBeVisible();
  await expect(page.getByText("checkout/payments.py", {exact: true}).first()).toBeVisible();
  await expect(page.getByText("fastapi, pytest")).toBeVisible();
  await expect(page.locator("body")).toContainText("9".repeat(64));
  await page.getByText("2 symbols · parsed").click();
  await expect(page.getByText(/PaymentService \(class, lines 5-9\)/)).toBeVisible();
  await expect(page.getByText("tests/test_payments.py").first()).toBeVisible();
  await expect(page.getByText("Verified", {exact: true})).toHaveCount(0);
});

test("analysis tab explains a missing index without inventing results", async ({page}) => {
  await routeCollected(page, null);
  await page.goto("/");
  await page.getByRole("button", {name: /Run 12345678/}).click();
  await page.getByRole("tab", {name: "analysis"}).click();
  await expect(page.getByText("No retained analysis is available for this run.")).toBeVisible();
});

test("bridge allows only GET for the read-only repository-index path", async () => {
  const headers = {host: "127.0.0.1:3000"};
  const ok = await bridge(new Request("http://127.0.0.1:3000/api", {headers}), ["runs", runId, "repository-index"]);
  expect(((await ok.json().catch(() => ({}))) as {error_class?: string}).error_class).not.toBe("PATH_DENIED");
  const post = await bridge(new Request("http://127.0.0.1:3000/api", {method: "POST", headers: {...headers, origin: "http://127.0.0.1:3000"}, body: "{}"}), ["runs", runId, "repository-index"]);
  expect(post.status).toBe(404);
});
