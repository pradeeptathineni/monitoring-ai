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
