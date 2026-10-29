import {test, expect} from "@playwright/test";

test("evaluation page shows exact available and unavailable denominators as untrusted", async ({page}) => {
  await page.goto("/evaluation");
  await expect(page.getByText("UNAUTHENTICATED AGGREGATE", {exact:true})).toBeVisible();
  await expect(page.getByText("0 attempts · 0 live", {exact:true})).toBeVisible();
  await expect(page.getByRole("heading", {name:"Evaluation status"})).toBeVisible();
  await expect(page.getByRole("row", {name:/checkout-idempotency checkout_retry Available/})).toBeVisible();
  await expect(page.getByRole("row", {name:/etl-aggregation-drift etl_aggregation_drift Unavailable/})).toBeVisible();
  await expect(page.getByText(/No execution outcomes are available/)).toBeVisible();
  await page.screenshot({path:"test-results/evaluation-status-desktop.png",fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await expect(page.locator("body")).toContainText("UNAUTHENTICATED AGGREGATE");
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBeTruthy();
  await expect(page.locator(".mobile-field-label").filter({hasText:"Attempts"}).first()).toBeVisible();
  await expect(page.locator(".mobile-field-label").filter({hasText:"Note"}).first()).toBeVisible();
  await page.screenshot({path:"test-results/evaluation-status-mobile.png",fullPage:true});
});

test("evaluation panel preserves trace, error category, origin and available measurements", async ({page}) => {
  const response=await page.request.get("/evidence/m4-evaluation-status.json");
  const report=await response.json();
  report.totals.attempted_cases=1;
  report.totals.test_transport_attempts=1;
  report.totals.patch_scope_rejections=1;
  report.cases[0].attempted_cases=1;
  report.cases[0].test_transport_attempts=1;
  report.cases[0].failure_categories={PATCH_SCOPE:1};
  report.attempts=[{
    case_id:"checkout-idempotency", origin:"TEST_TRANSPORT", status:"REJECTED",
    attempted:true, trace_id:"00000000-0000-4000-8000-000000000099",
    reproduction:"UNKNOWN", oracle_status:"UNAVAILABLE", failure_category:"PATCH_SCOPE",
    evidence_refs:["sha256:"+"a".repeat(64)], model:null, provider:null,
    provider_estimated_cost_usd:0.17, latency_seconds:1.25,
  }];
  await page.addInitScript((mockReport)=>{
    const originalFetch=window.fetch.bind(window);
    window.fetch=async(input,init)=>{
      const requestUrl=input instanceof Request?input.url:String(input);
      if(new URL(requestUrl,window.location.href).pathname==="/evidence/m4-evaluation-status.json"){
        return new Response(JSON.stringify(mockReport),{status:200,headers:{"content-type":"application/json"}});
      }
      return originalFetch(input,init);
    };
  },report);
  await page.goto("/evaluation");

  const attemptRow=page.getByRole("row").filter({hasText:"TEST_TRANSPORT"});
  await expect(attemptRow).toContainText("checkout-idempotency");
  await expect(attemptRow).toContainText("00000000-0000-4000-8000-000000000099");
  await expect(attemptRow).toContainText("PATCH_SCOPE");
  await expect(attemptRow).toContainText("$0.1700");
  await expect(attemptRow).toContainText("1.25 s");
  await expect(page.getByText("UNAUTHENTICATED AGGREGATE",{exact:true})).toBeVisible();
});
