import {expect, test} from "@playwright/test";

test("real workbench keeps anonymous visitors at the GitHub sign-in boundary", async ({page}) => {
  await page.goto("/");
  await expect(page.getByRole("heading", {name: "Sign in to ReplayForge"})).toBeVisible();
  await expect(page.getByRole("link", {name: "Continue with GitHub"})).toHaveAttribute(
    "href",
    /\/api\/v1\/auth\/github\/start$/,
  );
  await expect(
    page.getByText("Signing in does not authorize repository execution or pull requests."),
  ).toBeVisible();
});

test("evaluation page labels inventory separately from execution evidence", async ({page}) => {
  await page.goto("/evaluation");
  await expect(page.getByRole("heading", {name: "Evaluation status"})).toBeVisible();
  await expect(
    page.getByText("Inventory and accounting only. This report does not authenticate execution, oracle results, or verification gates."),
  ).toBeVisible();
});

test("anonymous API requests cannot read private run history", async ({request}) => {
  const response = await request.get("/api/backend/runs");
  expect(response.status()).toBe(401);
});

test("readiness reports both production database roles are connected", async ({request}) => {
  const response = await request.get("/health/ready");
  expect(response.status()).toBe(200);
  await expect(response).toBeOK();
  expect(await response.json()).toEqual({status: "ready"});
});

test("evaluation remains usable at a mobile viewport", async ({page}) => {
  await page.setViewportSize({width: 390, height: 844});
  await page.goto("/evaluation");
  await expect(page.getByRole("heading", {name: "Evaluation status"})).toBeVisible();
  const dimensions = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    content: document.documentElement.scrollWidth,
  }));
  expect(dimensions.content).toBeLessThanOrEqual(dimensions.viewport);
});
