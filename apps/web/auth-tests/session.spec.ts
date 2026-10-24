import { test, expect } from "@playwright/test";
import { sessionBridge } from "../lib/session-bridge";

const id="12345678-1234-4123-8123-123456789abc";
const csrf="c".repeat(64);
const me={id,github_id:123,github_login:"demo-reviewer",avatar_url:"https://avatars.githubusercontent.com/u/123",roles:[],csrf_token:csrf};

test("session bridge forwards only session identity and never the local bearer", async () => {
  const before=globalThis.fetch;
  const old=process.env.REPLAYFORGE_API_URL;
  process.env.REPLAYFORGE_API_URL="http://127.0.0.1:8109";
  const captured: Headers[]=[];
  globalThis.fetch=async (_input,init) => {
    captured.push(new Headers(init?.headers));
    return Response.json(me);
  };
  try {
    const r=await sessionBridge(new Request("http://127.0.0.1:3000/api/backend/auth/me",{headers:{
      host:"127.0.0.1:3000",cookie:`other=private; rf_dev_session=${"s".repeat(43)}`,authorization:"Bearer ignored"}}),["auth","me"]);
    expect(r.status).toBe(200);
    expect(captured[0].get("cookie")).toBe(`rf_dev_session=${"s".repeat(43)}`);
    expect(captured[0].has("authorization")).toBeFalsy();
    const headers={host:"127.0.0.1:3000",cookie:`rf_dev_session=${"s".repeat(43)}`};
    expect((await sessionBridge(new Request("http://127.0.0.1:3000/api?installation_id=7&page=2",{headers}),["github","repositories"])).status).toBe(200);
    expect((await sessionBridge(new Request("http://127.0.0.1:3000/api?installation_id=7&repository_id=42&page=1",{headers}),["github","head"])).status).toBe(200);
    expect((await sessionBridge(new Request("http://127.0.0.1:3000/api?installation_id=7&page=0",{headers}),["github","repositories"])).status).toBe(400);
    expect((await sessionBridge(new Request("http://127.0.0.1:3000/api?installation_id=7&page=1&page=2",{headers}),["github","repositories"])).status).toBe(400);

    expect((await sessionBridge(new Request("http://127.0.0.1:3000/api",{method:"POST",body:"{}"}),["runs"])).status).toBe(403);
    expect((await sessionBridge(new Request("http://127.0.0.1:3000/api?token=x"),["auth","me"])).status).toBe(400);
  } finally {globalThis.fetch=before;if(old===undefined)delete process.env.REPLAYFORGE_API_URL;else process.env.REPLAYFORGE_API_URL=old;}
});

test("anonymous browser cannot render private workbench", async ({page}) => {
  const calls:string[]=[];
  await page.route("**/api/backend/**",r=>{calls.push(new URL(r.request().url()).pathname);return r.fulfill({status:401,json:{}});});
  await page.goto("/");
  await expect(page.getByRole("link",{name:"Continue with GitHub"})).toBeVisible();
  expect(calls.every(v=>v.endsWith("auth/me"))).toBeTruthy();
  await expect(page.getByRole("heading",{name:"Recent runs"})).toHaveCount(0);
  await page.screenshot({path:"test-results/auth-login.png",fullPage:true});
});

test("synthetic session can connect an allowed repository and sign out", async ({page}) => {
  let logout=false,connected=false;
  await page.route("**/api/backend/**",async r=>{
    const path=new URL(r.request().url()).pathname;
    if(path.endsWith("auth/me"))return logout?r.fulfill({status:401,json:{}}):r.fulfill({json:me});
    if(path.endsWith("auth/logout")){
      expect(r.request().headers()["x-csrf-token"]).toBe(csrf);logout=true;
      return r.fulfill({status:204,body:""});
    }
    if(path.endsWith("github/installations"))return r.fulfill({json:[{id:7,account:{id:9,login:"example"},permissions:{contents:"read",pull_requests:"none"},suspended_at:null}]});
    if(path.endsWith("github/head"))return r.fulfill({json:{sha:"a".repeat(40)}});
    if(path.endsWith("github/repositories"))return r.fulfill({json:[{id:42,full_name:"example/demo",private:false,permissions:{pull:true,push:false},archived:false,disabled:false}]});
    if(path.endsWith("repositories/42/connect")){
      expect(r.request().headers()["x-csrf-token"]).toBe(csrf);connected=true;
      return r.fulfill({json:{repository_id:id,commits:["a".repeat(40)]}});
    }
    return r.fulfill({json:[]});
  });
  await page.goto("/settings/integrations");
  await expect(page.getByLabel("GitHub installation")).toHaveValue("7");
  await page.getByLabel("Repository").selectOption("42");
  await expect(page.getByText("Source pinned to aaaaaaaaaaaa.")).toBeVisible();
  await page.getByLabel(/I authorize/).check();
  await page.getByRole("button",{name:"Connect repository"}).click();
  await expect(page.getByText("Repository connected",{exact:true})).toBeVisible();
  expect(connected).toBeTruthy();
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  expect(await page.evaluate(()=>[localStorage.length,sessionStorage.length])).toEqual([0,0]);
  await page.screenshot({path:"test-results/auth-connected-mobile.png",fullPage:true});
  await page.getByRole("link",{name:"Start an investigation",exact:true}).click();
  await expect(page.getByRole("heading",{name:"Start an investigation",exact:true})).toBeVisible();
  await expect(page.getByLabel("Approved repository ID")).toHaveValue(id);
  await expect(page.getByLabel("Source commit")).toHaveValue("a".repeat(40));
  await page.getByRole("button",{name:"Sign out"}).click();
  await expect(page.getByRole("link",{name:"Continue with GitHub"})).toBeVisible();
  expect(logout).toBeTruthy();
});


test("large-account pages and discovery failures preserve identity and allow refresh", async ({page})=>{
  let fail=false;
  const repo=(n:number)=>({id:n,full_name:`example/repo${n}`,private:false,permissions:{pull:true,push:false}});
  await page.route("**/api/backend/**",r=>{
    const url=new URL(r.request().url()),path=url.pathname;
    if(path.endsWith("auth/me"))return r.fulfill({json:me});
    if(path.endsWith("github/installations"))return r.fulfill({json:[{id:7,account:{login:"example"}}]});
    if(path.endsWith("github/head"))return r.fulfill({json:{sha:"a".repeat(40)}});
    if(path.endsWith("github/repositories")){
      if(fail)return r.fulfill({status:502,json:{error_class:"BACKEND_DENIED"}});
      return r.fulfill({json:url.searchParams.get("page")==="2"?[repo(51)]:Array.from({length:50},(_,i)=>repo(i+1))});
    }
    return r.fulfill({json:[]});
  });
  await page.goto("/settings/integrations");
  await expect(page.getByLabel("GitHub installation")).toHaveValue("7");
  await expect(page.getByRole("button",{name:"Next",exact:true})).toBeEnabled();
  await page.getByRole("button",{name:"Next",exact:true}).click();
  await expect(page.getByRole("option",{name:"example/repo51",exact:true})).toHaveCount(1);
  await expect(page.getByRole("button",{name:"Next",exact:true})).toBeDisabled();
  fail=true;
  await page.getByRole("button",{name:"Refresh repositories"}).click();
  await expect(page.getByRole("alert").filter({hasText:"session is still signed in"})).toBeVisible();
  await expect(page.getByText("demo-reviewer",{exact:true})).toBeVisible();
  await expect(page.getByRole("link",{name:"Continue with GitHub"})).toHaveCount(0);
  fail=false;
  await page.getByRole("button",{name:"Refresh repositories"}).click();
  await expect(page.getByRole("option",{name:"example/repo51",exact:true})).toHaveCount(1);
});
