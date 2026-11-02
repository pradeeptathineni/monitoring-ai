import { test, expect } from "@playwright/test";

async function prepare(page, mutate = () => {}) {
  const response = await page.request.get("/api/v1/snapshot");
  const snapshot = await response.json();
  snapshot.events = [];
  mutate(snapshot);
  let current = snapshot;
  await page.route("**/api/v1/snapshot?**", (route) => route.fulfill({ json: current }));
  await page.goto("/");
  await expect(page.locator("#metrics .metric")).toHaveCount(4);
  return { snapshot, setCurrent: (value) => { current = value; } };
}

function historyRow(entity, index, withCost = false) {
  return {
    schema: "monitoring.event.v1", id: `history-${index}`, source: entity.source,
    session: entity.session, entity: entity.entity, parent: entity.parent ?? null,
    entity_type: entity.entity_type, timestamp: new Date(Date.UTC(2026, 9, 1, 0, 0, index)).toISOString(),
    kind: "usage", provider: "anthropic", model: "fixture-model", surface: "fixture",
    project: "fixture-project", tokens: { input: 1, cache_read: 0, cache_write: 0, output: 0 },
    ...(withCost ? { cost: { amount: "0.0123", currency: "EUR", basis: "billed", provenance: "fixture-declared" } } : {}),
  };
}

async function selectFirstEntity(page) {
  await page.locator('.nav[data-view="sessions"]').click();
  const button = page.locator("#entity-table button.entity-button").first();
  await expect(button).toBeVisible();
  const entityName = (await button.innerText()).replace(/^↳\s*/, "");
  await button.click();
  await expect(page.locator("#events")).toBeVisible();
  return entityName;
}

test("entity history reads oldest-first pages beyond the snapshot and pins identity/revision", async ({ page }) => {
  const { snapshot } = await prepare(page);
  const entity = snapshot.entities[0];
  await page.selectOption('[name="provider"]', "anthropic");
  await page.locator('.nav[data-view="sessions"]').click();
  await page.selectOption("#state-filter", "running");
  await page.locator('[name="since"]').fill("2026-10-01");
  await page.locator('[name="until"]').fill("2026-11-02");
  const requests = [];
  await page.route("**/api/v1/events**", async (route) => {
    const url = new URL(route.request().url());
    requests.push(url);
    const offset = Number(url.searchParams.get("after") || 0);
    const all = Array.from({ length: 150 }, (_, index) => historyRow(entity, index, index === 0));
    return route.fulfill({ json: {
      schema: "monitoring.events.v1", identity: snapshot.identity, revision: snapshot.revision,
      events: all.slice(offset, offset + 100), total: all.length,
      next_offset: offset + 100 < all.length ? offset + 100 : null,
    } });
  });

  await page.locator("#entity-table button.entity-button").first().click();
  await expect(page.locator("#event-table tbody tr")).toHaveCount(100);
  await expect(page.locator("#subtitle")).toContainText("Paged history for this exact entity");
  await expect(page.locator("#event-description")).toContainText("Paged history for this exact entity");
  await expect(page.locator("#entity-history-count")).toContainText("100 of 150");
  await expect(page.locator("#entity-history-count")).not.toContainText(snapshot.identity);
  await expect(page.locator("#event-table thead")).toContainText("Recorded cost · original currency");
  await expect(page.locator("#event-table")).toContainText("0.0123 EUR · billed · fixture-declared");
  await expect(page.locator("#event-table")).toContainText("No recorded cost");
  await expect(page.locator("#entity-history-scope")).toContainText("no price book or conversion is applied");
  const first = requests[0];
  expect(first.searchParams.get("source")).toBe(entity.source);
  expect(first.searchParams.get("session")).toBe(entity.session);
  expect(first.searchParams.get("entity")).toBe(entity.entity);
  expect(first.searchParams.get("limit")).toBe("100");
  expect(first.searchParams.get("provider")).toBe("anthropic");
  expect(first.searchParams.get("since")).toBe("2026-10-01");
  expect(first.searchParams.get("until")).toBe("2026-11-02");
  expect(first.searchParams.has("state")).toBe(false);
  expect(first.searchParams.has("expected_revision")).toBe(false);

  await page.locator("#load-more-events").click();
  await expect(page.locator("#event-table tbody tr")).toHaveCount(150);
  await expect(page.locator("#entity-history-count")).toContainText("150 of 150");
  expect(requests[1].searchParams.get("after")).toBe("100");
  expect(requests[1].searchParams.get("expected_revision")).toBe(String(snapshot.revision));
  await expect(page.locator("#load-more-events")).toBeHidden();
  await page.locator("#clear-entity").click();
  await expect(page.locator("#event-description")).toContainText("Latest 100 matching records");
  await expect(page.locator("#subtitle")).toContainText("Latest 100 matching usage, status and tool records");
  await expect(page.locator("#entity-history-panel")).toBeHidden();
});

test("409 retains loaded pages and restart adopts the current snapshot revision", async ({ page }) => {
  const { snapshot, setCurrent } = await prepare(page);
  const entity = snapshot.entities[0];
  const nextRevision = snapshot.revision + 1;
  let firstPageCount = 0;
  await page.route("**/api/v1/events**", async (route) => {
    const url = new URL(route.request().url()), offset = Number(url.searchParams.get("after") || 0);
    if (offset > 0 && firstPageCount === 1) {
      setCurrent({ ...snapshot, revision: nextRevision, events: [] });
      return route.fulfill({ status: 409, json: { error: "changed", revision: nextRevision } });
    }
    if (offset === 0) firstPageCount += 1;
    const rows = Array.from({ length: 100 }, (_, index) => historyRow(entity, index));
    return route.fulfill({ json: { schema: "monitoring.events.v1", identity: snapshot.identity, revision: nextRevision, events: rows, total: 145, next_offset: 100 } });
  });
  await selectFirstEntity(page);
  await expect(page.locator("#event-table tbody tr")).toHaveCount(100);
  await page.locator("#load-more-events").click();
  await expect(page.locator("#entity-history-status")).toContainText("ledger changed");
  await expect(page.locator("#event-table tbody tr")).toHaveCount(100);
  await expect(page.locator("#restart-entity-history")).toBeVisible();
  await expect(page.locator("#load-more-events")).toBeDisabled();
  await expect(page.locator("#footnote")).toContainText(`revision ${nextRevision}`);

  await page.locator("#restart-entity-history").click();
  await expect(page.locator("#event-table tbody tr")).toHaveCount(100);
  await expect(page.locator("#entity-history-count")).toContainText(`owner revision ${nextRevision}`);
});

test("identity mismatch refuses the page and keeps only a user-triggered restart", async ({ page }) => {
  const { snapshot } = await prepare(page);
  const entity = snapshot.entities[0];
  let wrong = true, calls = 0;
  await page.route("**/api/v1/events**", async (route) => {
    calls += 1;
    return route.fulfill({ json: {
      schema: "monitoring.events.v1", identity: wrong ? "other-owner" : snapshot.identity,
      revision: snapshot.revision, events: [historyRow(entity, 0)], total: 1, next_offset: null,
    } });
  });
  await selectFirstEntity(page);
  await expect(page.locator("#entity-history-count")).toContainText("No history page has been loaded yet.");
  await expect(page.locator("#entity-history-status")).toContainText("did not match the owner identity and revision");
  await expect(page.locator("#event-table tbody tr")).toHaveCount(0);
  expect(calls).toBe(1);
  wrong = false;
  await page.locator("#restart-entity-history").click();
  await expect(page.locator("#event-table tbody tr")).toHaveCount(1);
  expect(calls).toBe(2);
});

test("leaving Events cancels a pending page and returning does not retry it", async ({ page }) => {
  const { snapshot } = await prepare(page);
  const entity = snapshot.entities[0];
  let calls = 0;
  await page.route("**/api/v1/events**", async (route) => {
    calls += 1;
    await new Promise((resolve) => setTimeout(resolve, 500));
    try {
      await route.fulfill({ json: { schema: "monitoring.events.v1", identity: snapshot.identity, revision: snapshot.revision, events: [historyRow(entity, 0)], total: 1, next_offset: null } });
    } catch { /* The browser canceled the request when the user left the view. */ }
  });
  await selectFirstEntity(page);
  await expect(page.locator("#entity-history-status")).toContainText("Loading");
  await page.locator('.nav[data-view="usage"]').click();
  await page.locator('.nav[data-view="events"]').click();
  await expect(page.locator("#entity-history-status")).toContainText("canceled because you left the Event stream");
  await page.waitForTimeout(600);
  await expect(page.locator("#event-table tbody tr")).toHaveCount(0);
  expect(calls).toBe(1);
});

test("report preset form changes invalidate entity pages and remain qualified through pause and resume", async ({ page }) => {
  const { snapshot } = await prepare(page);
  const entity = snapshot.entities[0];
  let calls = 0;
  await page.route("**/api/v1/events**", (route) => {
    calls += 1;
    return route.fulfill({ json: {
      schema: "monitoring.events.v1", identity: snapshot.identity, revision: snapshot.revision,
      events: Array.from({ length: 100 }, (_, index) => historyRow(entity, index)), total: 150, next_offset: 100,
    } });
  });
  await selectFirstEntity(page);
  await page.locator('#filters [name="since"]').evaluate((input) => {
    input.value = "2026-10-31";
    input.form.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await expect(page.locator("#entity-history-status")).toContainText("Filters changed");
  await expect(page.locator("#load-more-events")).toBeDisabled();
  await page.locator("#pause").click();
  await expect(page.locator("#entity-history-status")).toContainText("Filters changed");
  await page.locator("#pause").click();
  await expect(page.locator("#entity-history-status")).toContainText("Filters changed");
  expect(calls).toBe(1);
  await page.locator("#restart-entity-history").click();
  await expect(page.locator("#event-table tbody tr")).toHaveCount(100);
  expect(calls).toBe(2);
});

test("a history page click detects filter drift even when no change event was emitted", async ({ page }) => {
  const { snapshot } = await prepare(page);
  const entity = snapshot.entities[0];
  let calls = 0;
  await page.route("**/api/v1/events**", (route) => {
    calls += 1;
    return route.fulfill({ json: {
      schema: "monitoring.events.v1", identity: snapshot.identity, revision: snapshot.revision,
      events: Array.from({ length: 100 }, (_, index) => historyRow(entity, index)), total: 150, next_offset: 100,
    } });
  });
  await selectFirstEntity(page);
  await expect(page.locator("#event-table tbody tr")).toHaveCount(100);
  await page.locator('#filters [name="since"]').evaluate((input) => { input.value = "2026-10-31"; });
  await page.locator("#load-more-events").click();
  await expect(page.locator("#entity-history-status")).toContainText("Filters changed");
  await expect(page.locator("#load-more-events")).toBeDisabled();
  expect(calls).toBe(1);
});

test("paused lifecycle evidence ages without activating entities and history waits for an explicit retry", async ({ page }) => {
  await page.addInitScript(() => {
    let offset = 0;
    const nativeNow = Date.now.bind(Date);
    Date.now = () => nativeNow() + offset;
    window.advanceHistoryClock = (milliseconds) => { offset += milliseconds; };
  });
  const { snapshot } = await prepare(page, (value) => {
    const row = value.entities[0];
    row.state = "running"; row.reported_state = "running"; row.stale = false; row.status_at = value.observed_at;
  });
  let eventReads = 0;
  await page.route("**/api/v1/events**", async (route) => {
    eventReads += 1;
    const entity = snapshot.entities[0];
    return route.fulfill({ json: { schema: "monitoring.events.v1", identity: snapshot.identity, revision: snapshot.revision, events: [historyRow(entity, 0)], total: 1, next_offset: null } });
  });
  await page.locator("#pause").click();
  await expect(page.locator("#connection")).toHaveText("Refresh paused");
  await page.locator('.nav[data-view="sessions"]').click();
  const firstRow = page.locator("#entity-table tbody tr").first();
  const before = await firstRow.locator(".state-evidence-age").innerText();
  expect(await firstRow.locator(".state").getAttribute("class")).toContain("unknown");
  expect(await page.locator("#entity-table .state.running")).toHaveCount(0);
  await page.evaluate(() => window.advanceHistoryClock(25_000));
  await expect.poll(() => firstRow.locator(".state-evidence-age").innerText()).not.toBe(before);
  const label = await firstRow.locator(".state-evidence-age").innerText();
  expect(Number(label.match(/Lifecycle evidence ([\d,]+)/)?.[1].replaceAll(",", ""))).toBeGreaterThan(20);

  await firstRow.locator("button.entity-button").click();
  await expect(page.locator("#entity-history-status")).toContainText("Refresh is paused");
  expect(eventReads).toBe(0);
  await expect(page.locator("#restart-entity-history")).toBeDisabled();
  await page.locator("#pause").click();
  await expect(page.locator("#connection")).toContainText("Ledger available");
  expect(eventReads).toBe(0);
  await page.locator("#restart-entity-history").click();
  await expect(page.locator("#event-table tbody tr")).toHaveCount(1);
  expect(eventReads).toBe(1);
});

test("terminal lifecycle and timeline evidence gain a stale qualifier as retained time advances", async ({ page }) => {
  await page.addInitScript(() => {
    let offset = 0;
    const nativeNow = Date.now.bind(Date);
    Date.now = () => nativeNow() + offset;
    window.advanceHistoryClock = (milliseconds) => { offset += milliseconds; };
  });
  const { snapshot } = await prepare(page, (value) => {
    const row = value.entities[0];
    row.state = "completed"; row.reported_state = "completed"; row.stale = false;
    row.status_at = value.observed_at; row.last_event_at = value.observed_at;
  });
  await page.locator("#pause").click();
  await page.locator('.nav[data-view="sessions"]').click();
  const status = page.locator("#entity-table .state.completed").first();
  await expect(status.locator(".state-evidence-age")).not.toContainText("stale");
  await page.evaluate(() => window.advanceHistoryClock(61_000));
  await expect.poll(() => status.locator(".state-evidence-age").innerText()).toContain("stale");
  await expect(status).toHaveClass(/completed/);
  await expect(status).toHaveAttribute("title", /Stale: true/);
  await page.locator('.nav[data-view="overview"]').click();
  const timelineState = page.locator("#timeline .state.completed").first();
  await expect(timelineState.locator(".state-evidence-age")).toContainText("stale");
  await expect(page.locator("#timeline .timeline-name").first()).toHaveAttribute("title", /stale/);
  await expect(page.locator("#timeline .timeline-track").first()).toHaveAttribute("title", /stale/);
});

test("entity history controls remain touch-usable on narrow screens", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 844 });
  const { snapshot } = await prepare(page);
  const entity = snapshot.entities[0];
  await page.route("**/api/v1/events**", (route) => route.fulfill({ json: {
    schema: "monitoring.events.v1", identity: snapshot.identity, revision: snapshot.revision,
    events: [historyRow(entity, 0)], total: 1, next_offset: null,
  } }));
  await page.locator('.mobile-nav [data-view="sessions"]').click();
  await page.locator("#entity-table button.entity-button").first().click();
  const restart = page.locator("#restart-entity-history");
  await expect(restart).toBeVisible();
  expect(await restart.evaluate((button) => button.getBoundingClientRect().height)).toBeGreaterThanOrEqual(44);
  expect(await page.locator("#clear-entity").evaluate((button) => button.getBoundingClientRect().height)).toBeGreaterThanOrEqual(44);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
});
