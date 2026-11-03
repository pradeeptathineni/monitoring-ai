import { test, expect } from "@playwright/test";

const task = (alias, observed) => ({ alias, host: "local", updated_at: observed, observed_at: observed, has_more: true,
  turns: [{ alias: "turn-fixture", status: "inProgress", started_at: observed, completed_at: null, duration_ms: null, error_present: false }] });

test("selected Codex turns remain separate from ledger sessions and focus an authoritative app alias", async ({ page }) => {
  const base = await (await page.request.get("/api/v1/snapshot")).json(), captured = new Date().toISOString();
  base.codex_app_turns = { schema: "monitoring.codex-app-turns.v1", captured_at: captured, read_state: "following", tasks: [task("task-fixture-a", captured)] };
  base.app_threads = { schema: "monitoring.app-threads.v1", source: "codex-app-task-history", scope: "codex-history-page",
    captured_at: captured, read_state: "following", threads: [
      { alias: "task-fixture-a", kind: "codex", status: "active", updated_at: captured, host: "local" },
      { alias: "task-fixture-b", kind: "codex", status: "idle", updated_at: captured, host: "local" }] };
  await page.route("**/api/v1/snapshot?**", route => route.fulfill({ json: base }));
  await page.goto("/#sessions");
  await expect(page.getByRole("heading", { name: "Selected Codex app turns", exact: true })).toBeVisible();
  await expect(page.locator("#app-turns-status")).toContainText("1 of 1 selected tasks have recent metadata");
  await expect(page.locator("#app-turns-table")).toContainText("in progress");
  await expect(page.locator("#app-turns-panel")).toContainText("does not prove model inference");
  await page.getByRole("button", { name: "Show app task", exact: true }).click();
  await expect(page.locator("#overview")).toBeVisible();
  await expect(page.locator("#app-alias-filter")).toHaveValue("task-fixture-a");
  await expect(page.locator("#app-thread-table tbody tr")).toHaveCount(1);
  await page.goto("/#sessions");
  await page.locator("#app-turn-state-filter").selectOption("inProgress");
  await expect(page.locator("#app-turns-table tbody tr")).toHaveCount(1);
  await page.locator("#pause").click();
  await expect(page.locator("#app-turns-table tbody tr")).toHaveCount(0);
  await page.locator("#app-turn-state-filter").selectOption("unknown");
  await expect(page.locator("#app-turns-table")).toContainText("Previously in progress · current unknown");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
});

test("a new batch does not renew stale per-task observations and read failure makes current status unknown", async ({ page }) => {
  const base = await (await page.request.get("/api/v1/snapshot")).json();
  const captured = new Date().toISOString(), old = new Date(Date.now() - 60000).toISOString();
  const book = { schema: "monitoring.codex-app-turns.v1", captured_at: captured, read_state: "following", tasks: [task("task-fresh", captured), task("task-old", old)] };
  await page.route("**/api/v1/snapshot?**", route => route.fulfill({ json: { ...base, codex_app_turns: book } }));
  await page.goto("/#sessions");
  await expect(page.locator("#app-turns-status")).toContainText("1 of 2 selected tasks have recent metadata");
  await page.locator("#app-turn-state-filter").selectOption("inProgress");
  await expect(page.locator("#app-turns-table tbody tr")).toHaveCount(1);
  await page.locator("#app-turn-alias-filter").fill("task-old");
  await expect(page.locator("#app-turns-table tbody tr")).toHaveCount(0);
  await page.locator("#app-turn-state-filter").selectOption("unknown");
  await expect(page.locator("#app-turns-table")).toContainText("task-old");
  book.read_state = "unavailable";
  await expect(page.locator("#app-turns-status")).toContainText("0 of 2 selected tasks have recent metadata");
  await page.locator("#app-turn-alias-filter").fill("");
  await expect(page.locator("#app-turns-table tbody tr")).toHaveCount(2);
  await expect(page.locator("#app-turns-panel")).toContainText("current status is unknown");
});
