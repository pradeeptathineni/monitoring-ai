import { test, expect } from "@playwright/test";

test("browser change list distinguishes new records, corrections and retained history", async ({ page }) => {
  const response = await page.request.get("/api/v1/snapshot");
  const base = await response.json();
  const usage = structuredClone(base.events.find((row) => row.kind === "usage"));
  expect(usage).toBeTruthy();
  let current = structuredClone(base), unavailable = false;
  await page.route("**/api/v1/snapshot?**", (route) => unavailable
    ? route.abort() : route.fulfill({ json: current }));
  await page.goto("/");
  await expect(page.locator("#ledger-changes-status")).toContainText("Watching from the first successful snapshot");
  await expect(page.locator("#ledger-changes .ledger-change")).toHaveCount(0);

  usage.id = "fictional-browser-update";
  current = structuredClone(current);
  current.revision += 1;
  current.events.unshift(usage);
  await expect(page.locator("#ledger-changes .ledger-change")).toHaveCount(1, { timeout: 5_000 });
  await expect(page.locator("#ledger-changes")).toContainText("Added usage");
  await expect(page.locator("#ledger-changes")).toContainText("source time");
  await expect(page.locator("#ledger-changes")).toContainText("observed");

  current = structuredClone(current);
  current.revision += 1;
  current.events[0].tokens.input += 5;
  await expect(page.locator("#ledger-changes .ledger-change")).toHaveCount(2, { timeout: 5_000 });
  await expect(page.locator("#ledger-changes .ledger-change").first()).toContainText("Revised usage");

  unavailable = true;
  await expect(page.locator("#connection")).toContainText("Disconnected", { timeout: 5_000 });
  await expect(page.locator("#ledger-changes-status")).toContainText("Prior changes are retained; new activity is unknown");
  await expect(page.locator("#ledger-changes .ledger-change")).toHaveCount(2);

  unavailable = false;
  await page.selectOption('[name="provider"]', base.facets.provider[0]);
  await expect(page.locator("#ledger-changes-status")).toContainText("Filter scope or ledger revision changed");
  await expect(page.locator("#ledger-changes .ledger-change")).toHaveCount(0);
});

test("a newly observed record opens its exact paged entity history", async ({ page }) => {
  const base = await (await page.request.get("/api/v1/snapshot")).json();
  const usage = structuredClone(base.events.find((row) => row.kind === "usage"));
  expect(usage).toBeTruthy();
  usage.id = "fictional-history-entry";
  let current = structuredClone(base);
  const historyRequests = [];
  await page.route("**/api/v1/snapshot?**", (route) => route.fulfill({ json: current }));
  await page.route("**/api/v1/events?**", (route) => {
    historyRequests.push(new URL(route.request().url()));
    return route.fulfill({ json: { schema: "monitoring.events.v1",
      identity: current.identity, revision: current.revision,
      events: [usage], total: 1, next_offset: null } });
  });
  await page.goto("/");
  current = { ...current, revision: current.revision + 1, events: [usage, ...current.events] };
  const change = page.locator("#ledger-changes .ledger-change").first();
  await expect(change).toContainText("Added usage", { timeout: 5_000 });
  await change.getByRole("button", { name: `Inspect history for ${usage.entity} in ${usage.session}` }).click();
  await expect(page.locator("#events")).toBeVisible();
  await expect(page.locator("#entity-history-scope")).toContainText(`entity ${usage.entity}`);
  await expect(page.locator("#event-table tbody tr")).toHaveCount(1);
  expect(historyRequests).toHaveLength(1);
  const params = historyRequests[0].searchParams;
  expect(params.get("source")).toBe(usage.source);
  expect(params.get("session")).toBe(usage.session);
  expect(params.get("entity")).toBe(usage.entity);
  expect(params.has("expected_revision")).toBe(false);
  await page.goBack();
  await expect(page.locator("#overview")).toBeVisible();
});
