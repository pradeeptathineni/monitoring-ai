import { test, expect } from "@playwright/test";

test("recent transcript writes stay separate from recorded sessions and age into unknown visibility", async ({ page }) => {
  const response = await page.request.get("/api/v1/snapshot");
  const baseline = await response.json();
  const now = new Date().toISOString();
  let activity = {
    schema: "monitoring.codeburn-activity.v1", coverage: "recent-transcript-write-not-process-liveness",
    generated_at: now, sampled_at: now, file_modified_at: now,
    source_age_seconds: 10, file_age_seconds: 10, window_seconds: 600,
    sessions: [{ alias: "s-fictional", provider: "claude", model: "claude-sonnet-4", started_at: now,
      last_activity_at: now, context_tokens: 1200, context_window: 200000, project: "/private/path" }],
  };
  let state = "following";
  await page.route("**/api/v1/snapshot?**", (route) => route.fulfill({ json: {
    ...baseline, codeburn_activity_status: { state, last_success_at: now, error: null },
    codeburn_activity: activity,
  } }));
  await page.goto("/#sessions");
  const panel = page.locator("#codeburn-activity-panel");
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("1 session reported with transcript writes");
  await expect(panel).toContainText("Source observation is recent; transcript writes still do not prove process liveness.");
  await expect(panel).toContainText("s-fictional");
  await expect(panel).not.toContainText("/private/path");
  await expect(page.locator("#entity-table")).not.toContainText("s-fictional");

  activity = { ...activity, source_age_seconds: 1800, generated_at: "2026-11-02T20:00:00Z", sessions: [] };
  await page.reload();
  await expect(panel).toContainText("Current activity is unknown.");
  await expect(panel).toContainText("0 sessions reported with transcript writes");
  await expect(panel).toContainText("does not establish the number running now");

  activity = { ...activity, window_seconds: null, sessions: null };
  await page.reload();
  await expect(panel).toContainText("did not include a live-session block. Coverage is unknown.");

  state = "unavailable";
  await page.reload();
  await expect(panel).toContainText("Showing the last accepted observation.");

  state = "following";
  activity = { ...activity, generated_at: now, source_age_seconds: 10,
    window_seconds: 600, sessions: [{ alias: "s-fictional", provider: "claude", model: "claude-sonnet-4",
      started_at: now, last_activity_at: now, context_tokens: 1200, context_window: 200000 }] };
  await page.setViewportSize({ width: 390, height: 800 });
  await page.reload();
  await expect(panel).toBeVisible();
  await expect(panel.locator("table tbody tr")).toHaveCount(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
});
