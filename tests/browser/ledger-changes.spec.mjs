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
