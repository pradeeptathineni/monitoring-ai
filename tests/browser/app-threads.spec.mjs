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

test("app changes compare capture metadata without turning a missing row into completion", async ({ page }) => {
  const base = await (await page.request.get("/api/v1/snapshot")).json();
  const time = () => new Date().toISOString();
  const row = (alias, status) => ({ alias, kind: "codex", status, host: "local", updated_at: time() });
  let book = { schema: "monitoring.app-threads.v1", source: "codex-app-task-history", scope: "codex-history-page",
    captured_at: time(), read_state: "following", threads: [row("task-a", "active"), row("task-b", "idle")] };
  await page.route("**/api/v1/snapshot?**", route => route.fulfill({ json: { ...base, app_threads: book } }));
  await page.goto("/");
  await page.locator("#app-change-panel summary").click();
  await expect(page.locator("#app-change-status")).toContainText("First capture is the baseline");
  book = { ...book, captured_at: time(), threads: [row("task-a", "idle"), row("task-c", "active")] };
  await expect(page.locator("#app-change-table tbody tr")).toHaveCount(3);
  await expect(page.locator("#app-change-table")).toContainText("Reported active → idle");
  await expect(page.locator("#app-change-table")).toContainText("First seen in captured page");
  await expect(page.locator("#app-change-table")).toContainText("Absent from latest captured page · current status unknown");
  await page.locator("#app-status-filter").selectOption("active");
  await expect(page.locator("#app-change-table tbody tr")).toHaveCount(3);
  await page.locator("#pause").click();
  await expect(page.locator("#app-change-status")).toContainText("Current app visibility is unknown");
  book = { ...book, captured_at: time(), threads: [row("task-a", "active")] };
  await page.locator("#pause").click();
  await expect(page.locator("#app-change-status")).toContainText("fresh baseline was established after a gap");
  await expect(page.locator("#app-change-table tbody tr")).toHaveCount(3);
  await page.getByRole("button", { name: "Clear app observations", exact: true }).click();
  await expect(page.locator("#app-change-table")).toContainText("No app changes observed");
  await expect(page.getByRole("button", { name: "Clear app observations", exact: true })).toBeDisabled();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
});

test("app observation history does not bridge failed reads or a different owner", async ({ page }) => {
  const base = await (await page.request.get("/api/v1/snapshot")).json();
  let fail = false, owner = base.identity;
  let book = { schema: "monitoring.app-threads.v1", source: "codex-app-task-history", scope: "codex-history-page",
    captured_at: new Date().toISOString(), read_state: "following",
    threads: [{ alias: "task-a", kind: "codex", status: "active", host: "local", updated_at: new Date().toISOString() }] };
  await page.route("**/api/v1/snapshot?**", route => route.fulfill(fail
    ? { status: 503, body: "Unavailable" } : { json: { ...base, identity: owner, app_threads: book } }));
  await page.goto("/");
  await page.locator("#app-change-panel summary").click();
  await expect(page.locator("#app-change-status")).toContainText("First capture is the baseline");
  fail = true;
  await expect(page.locator("#app-change-status")).toContainText("Current app visibility is unknown");
  book = { ...book, captured_at: new Date().toISOString(), threads: [{ ...book.threads[0], status: "idle" }] };
  fail = false;
  await expect(page.locator("#app-change-status")).toContainText("fresh baseline was established after a gap");
  await expect(page.locator("#app-change-table tbody tr")).toHaveCount(0);
  book = { ...book, captured_at: new Date().toISOString(), threads: [{ ...book.threads[0], status: "active" }] };
  await expect(page.locator("#app-change-table")).toContainText("Reported idle → active");
  owner = "different-owner-fixture";
  await expect(page.locator("#app-change-table tbody tr")).toHaveCount(0);
  await expect(page.locator("#app-change-status")).toContainText("First capture is the baseline");
});
