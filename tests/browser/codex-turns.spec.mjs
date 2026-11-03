import { test, expect } from "@playwright/test";

test("Codex task markers show turn chronology without asserting session closure", async ({ page }) => {
  const base = await (await page.request.get("/api/v1/snapshot")).json();
  base.codex_turns = { schema: "monitoring.codex-turns.v1", configured_sources: 1,
    unavailable_sources: 0, ambiguous_turns: 0, truncated: false, rows: [
      { source: "chosen", project: "fictional", session: `s-${"a".repeat(32)}`,
        turn: `t-${"b".repeat(32)}`, start_at: "2026-11-02T19:00:00.000Z",
        complete_at: "2026-11-02T19:00:04.000Z", duration_ms: 4100,
        time_to_first_token_ms: 700, evidence: "paired", source_state: "following" },
    ] };
  await page.route("**/api/v1/snapshot?**", (route) => route.fulfill({ json: base }));
  await page.goto("/");
  await page.locator('#primary-navigation [data-view="sessions"]').click();
  await expect(page.locator("#codex-turn-panel")).toBeVisible();
  await expect(page.locator("#codex-turn-table")).toContainText("Both markers");
  await expect(page.locator("#codex-turn-table")).toContainText("4,100 ms");
  expect(await page.locator("#codex-turn-table").evaluate((element) => element.scrollWidth <= element.clientWidth)).toBeTruthy();
  await expect(page.locator("#codex-turn-status")).toContainText("Current session and process activity are unknown");
  await expect(page.locator("#entity-table")).not.toContainText(`s-${"a".repeat(32)}`);
  await page.locator("#pause").click();
  await expect(page.locator("#codex-turn-status")).toContainText("markers are retained history");
});

test("older owners without turn markers hide the optional panel", async ({ page }) => {
  await page.goto("/");
  await page.locator('#primary-navigation [data-view="sessions"]').click();
  await expect(page.locator("#codex-turn-panel")).toBeHidden();
});
