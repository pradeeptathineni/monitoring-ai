import { test, expect } from "@playwright/test";

test("app-reported statuses stay separate from ledger activity and become unknown when paused", async ({ page }) => {
  const base = await (await page.request.get("/api/v1/snapshot")).json();
  const book = { schema: "monitoring.app-threads.v1", captured_at: new Date().toISOString(),
    read_state: "following", threads: [
      { alias: "task-fixture-a", kind: "codex", status: "active", host: "local", updated_at: new Date().toISOString() },
      { alias: "task-fixture-b", kind: "chatgpt", status: "idle", host: "unknown", updated_at: new Date().toISOString() },
    ] };
  await page.route("**/api/v1/snapshot?**", (route) => route.fulfill({ json: { ...base, app_threads: book } }));
  await page.goto("/");
  await expect(page.locator("#app-thread-status")).toContainText("recent app inventory");
  await expect(page.locator("#app-thread-table tbody tr")).toHaveCount(2);
  await expect(page.locator("#app-thread-table")).toContainText("chatgpt");
  await page.locator("#pause").click();
  await expect(page.locator("#app-thread-table")).toContainText("Previously active · current unknown");
  await expect(page.locator("#app-thread-status")).toContainText("current app status unknown");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
});


test("selected Codex host metadata stays distinct from app inventory and worker activity", async ({ page }) => {
  const base = await (await page.request.get("/api/v1/snapshot")).json();
  const book = { schema: "monitoring.app-threads.v1", source: "codex-local-app-server",
    scope: "selected-cwd-page", complete_coverage: false, captured_at: new Date().toISOString(),
    read_state: "following", threads: [
      { alias: "task-runtime-fixture", kind: "codex", status: "active", host: "local", updated_at: new Date().toISOString() }
    ] };
  await page.route("**/api/v1/snapshot?**", route => route.fulfill({ json: { ...base, app_threads: book } }));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Codex runtime observations", exact: true })).toBeVisible();
  await expect(page.locator("#app-thread-description")).toContainText("database-only");
  await expect(page.locator("#app-thread-limit")).toContainText("Other hosts and ChatGPT conversations are unobserved");
  await expect(page.locator("#app-thread-status")).toContainText("recent Codex runtime snapshot");
  await page.locator("#pause").click();
  await expect(page.locator("#app-thread-status")).toContainText("current Codex runtime status unknown");
  await expect(page.locator("#app-thread-table")).toContainText("Previously active · current unknown");
});


test("Codex history browsing pages bounded rows and stops treating stale active rows as current", async ({ page }) => {
  const base = await (await page.request.get("/api/v1/snapshot")).json();
  const captured = new Date().toISOString();
  base.app_threads = { schema: "monitoring.app-threads.v1", source: "codex-app-task-history", scope: "codex-history-page",
    captured_at: captured, read_state: "following", complete_coverage: false,
    threads: Array.from({length:50},(_,i)=>({alias:`task-fixture-${String(i).padStart(2,"0")}`,kind:"codex",status:i<3?"active":"idle",host:"local",updated_at:captured})) };
  await page.route("**/api/v1/snapshot?**",route=>route.fulfill({json:base}));
  await page.goto("/");
  await expect(page.getByRole("heading",{name:"Codex app tasks",exact:true})).toBeVisible();
  await expect(page.locator("#app-thread-limit")).toContainText("ChatGPT history are unobserved");
  await expect(page.locator("#app-thread-table tbody tr")).toHaveCount(20);
  await expect(page.locator("#app-page-status")).toContainText("Page 1 of 3");
  await page.getByRole("button",{name:"Next app tasks",exact:true}).click();
  await expect(page.locator("#app-page-status")).toContainText("Page 2 of 3");
  await page.locator("#app-status-filter").selectOption("active");
  await expect(page.locator("#app-thread-table tbody tr")).toHaveCount(3);
  await page.locator("#pause").click();
  await expect(page.locator("#app-thread-table tbody tr")).toHaveCount(0);
  await expect(page.locator("#app-thread-status")).toContainText("50 unknown");
  await page.locator("#app-status-filter").selectOption("unknown");
  await expect(page.locator("#app-thread-table tbody tr")).toHaveCount(20);
  await expect(page.locator("#app-thread-table")).toContainText("Previously active · current unknown");
  await page.locator("#app-alias-filter").fill("fixture-49");
  await expect(page.locator("#app-thread-table tbody tr")).toHaveCount(1);
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
});
