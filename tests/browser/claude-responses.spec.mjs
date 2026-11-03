import { test, expect } from "@playwright/test";

test("native response markers do not assert session or process lifecycle", async ({ page }) => {
  const base = await (await page.request.get("/api/v1/snapshot")).json();
  base.claude_responses = { schema: "monitoring.claude-responses.v1", configured_sources: 1,
    unavailable_sources: 0, truncated: false, rows: [
      { source: "chosen", project: "fictional", session: "s-fictional", prompt_at: "2026-11-03T04:00:00.000Z",
        response_end_at: "2026-11-03T04:00:03.000Z", stop_hook_at: null,
        evidence: "response_end_observed", source_state: "following" },
    ] };
  await page.route("**/api/v1/snapshot?**", (route) => route.fulfill({ json: base }));
  await page.goto("/");
  await page.locator('#primary-navigation [data-view="sessions"]').click();
  await expect(page.locator("#claude-response-panel")).toBeVisible();
  await expect(page.locator("#claude-response-table")).toContainText("Response ended");
  await expect(page.locator("#claude-response-status")).toContainText("session closure are unknown");
  await expect(page.locator("#entity-table")).not.toContainText("s-fictional");
  await page.locator("#pause").click();
  await expect(page.locator("#claude-response-status")).toContainText("rows are retained history");
});
