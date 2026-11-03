import { test, expect } from "@playwright/test";

test("selected cached turns stay separate from event totals and disclose source age", async ({ page }) => {
  const base = await (await page.request.get("/api/v1/snapshot")).json();
  const originalCalls = base.summary.calls;
  base.selected_cache_status = { state: "following", last_success_at: "2026-11-03T02:00:00Z", error: null };
  base.selected_cache_session = { schema: "monitoring.selected-cache-session.v1", source: "chosen",
    project: "fictional", provider: "codex", format_version: 10, total_turns: 1, total_calls: 3,
    truncated: false, native_modified_at: "2026-11-02T20:00:00Z",
    cache_modified_at: "2026-11-02T20:01:00Z", turns: [
      { ordinal: 1, at: "2026-11-02T19:00:00Z", calls: 3,
        models: [{ id: "codex-test", calls: 3 }], tokens: { input: 11, output: 22, cache_read: 33, cache_write: 44 } },
    ] };
  await page.route("**/api/v1/snapshot?**", (route) => route.fulfill({ json: base }));
  await page.goto("/");
  await page.locator('#primary-navigation [data-view="sessions"]').click();
  await expect(page.locator("#selected-cache-panel")).toBeVisible();
  await expect(page.locator("#selected-cache-table")).toContainText("codex-test");
  await expect(page.locator("#selected-cache-table")).toContainText("44");
  await expect(page.locator("#selected-cache-status")).toContainText("Neither time proves current activity");
  await expect(page.locator("#metrics")).not.toContainText(`${originalCalls + 3} calls`);
  base.selected_cache_status.state = "unavailable";
  await page.reload();
  await page.locator('#primary-navigation [data-view="sessions"]').click();
  await expect(page.locator("#selected-cache-status")).toContainText("retained");
});
