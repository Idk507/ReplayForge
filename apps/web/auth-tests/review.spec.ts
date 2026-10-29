import {test,expect} from "@playwright/test";
import {createHash} from "node:crypto";

const runId="12345678-1234-4123-8123-123456789abc",repo="12345678-1234-4123-8123-123456789abd";
const diff="--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-x=1\n+x=2\n";
const binding={run_id:runId,repository_id:repo,owner_id:runId,source_commit:"a".repeat(40),patch_ref:"sha256:"+createHash("sha256").update(diff).digest("hex"),verification_ref:"sha256:"+"b".repeat(64),target_branch:"main",policy_version:"repository-v1"};
const run={run_id:runId,repository_id:repo,source_commit:binding.source_commit,source_manifest_ref:null,current_stage:"VERIFIED",version:3,trace_id:repo,evidence_manifest_ref:null,verification_ref:binding.verification_ref,cancellation_requested:false,failure_reason:null,remaining_budget:{model_calls:10,sandbox_commands:10,research_searches:2,execution_seconds:600,estimated_spend_microusd:1000000}};

for(const tamper of [false,true]) test(`synthetic exact review, tampered hash=${tamper}`,async({page})=>{
  let receipt:Record<string,unknown>|null=null,approvals=0,published=0;
  await page.route("**/api/backend/**",async route=>{
    const path=new URL(route.request().url()).pathname;
    if(path.endsWith("auth/me")) return route.fulfill({json:{id:runId,github_id:123,github_login:"demo-reviewer",avatar_url:"https://avatars.githubusercontent.com/u/123",roles:[],csrf_token:"c".repeat(64)}});
    if(path.endsWith("/review")) return route.fulfill({json:{binding:{...binding,patch_ref:tamper?"sha256:"+"f".repeat(64):binding.patch_ref},expected_version:receipt?4:3,receipt,publication_version:receipt?4:null}});
    if(path.endsWith("/approval")) {
      approvals++;const body=route.request().postDataJSON();
      expect(route.request().headers()["x-csrf-token"]).toBe("c".repeat(64));
      expect(body.binding).toEqual(binding);expect(body.expected_version).toBe(3);
      expect(body.reviewer_id).toBeUndefined();
      receipt={binding,decision:body.decision,reviewer_id:runId,nonce:body.nonce,issued_at:"2026-10-25T00:00:00Z",expires_at:"2099-10-25T00:15:00Z",can_authorize_publication:false};
      return route.fulfill({json:receipt});
    }
    if(path.endsWith("/publication")) {
      published++;const body=route.request().postDataJSON();
      expect(body.nonce).toBe(receipt?.nonce);expect(body.expected_version).toBe(4);
      return route.fulfill({json:{html_url:"https://github.com/example/demo/pull/1",origin:"REMOTE_SERVICE"}});
    }
    if(path.endsWith("/executions")) return route.fulfill({json:{run_id:runId,phases:[]}});
    if(path.endsWith("/events")) return route.fulfill({contentType:"text/event-stream",body:""});
    if(path.endsWith("/research")) return route.fulfill({json:[]});
    if(path.endsWith("/patch")) return route.fulfill({json:{proposal:{unified_diff:diff,justification:"Synthetic test patch",risk_notes:"Synthetic browser contract; no execution or PR."}}});
    if(path.endsWith("/verification")) return route.fulfill({json:{verification_ref:binding.verification_ref,verdict:{status:"VERIFIED",observed_at:"2026-10-25T00:00:00Z",can_authorize_publication:false},gates:[]}});
    return route.fulfill({json:path.endsWith("/runs")?[run]:run});
  });
  await page.goto("/");await page.getByRole("button",{name:/Run 12345678/}).click();
  await page.getByRole("tab",{name:"Patch & risk"}).click();
  expect(approvals).toBe(0);expect(published).toBe(0);
  await page.getByRole("button",{name:"Load exact review"}).click();
  if(tamper) {
    await expect(page.getByRole("region",{name:"Human review"}).getByRole("alert")).toBeVisible();
    await expect(page.getByRole("button",{name:"Approve exact patch"})).toHaveCount(0);
    expect(approvals).toBe(0);return;
  }
  await expect(page.getByRole("button",{name:"Approve exact patch"})).toBeDisabled();
  await page.setViewportSize({width:390,height:844});
  await expect(page.getByText(binding.patch_ref,{exact:true})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBeTruthy();
  await page.screenshot({path:"test-results/exact-review-mobile.png",fullPage:true});
  await page.getByRole("checkbox",{name:/I reviewed this exact patch/}).check();
  await page.getByRole("button",{name:"Approve exact patch"}).click();
  await expect(page.getByText(/Decision recorded: approve/)).toBeVisible();
  expect(approvals).toBe(1);expect(published).toBe(0);
  await expect(page.getByRole("button",{name:"Create approved draft PR"})).toBeDisabled();
  await page.getByRole("checkbox",{name:/I reviewed this exact patch/}).check();
  await page.getByRole("button",{name:"Create approved draft PR"}).click();
  await expect(page.getByRole("link",{name:/Open recorded draft PR/})).toHaveAttribute("href","https://github.com/example/demo/pull/1");
  expect(published).toBe(1);
});
