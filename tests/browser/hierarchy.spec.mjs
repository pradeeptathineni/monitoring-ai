import { test, expect } from "@playwright/test";

const observedAt = "2026-11-03T12:00:00Z";

function entity(snapshot, { source, session, name, parent = null, parentObserved = true, ago = 10, type = "worker" }) {
  const row = structuredClone(snapshot.entities[0]);
  const statusAt = new Date(Date.parse(observedAt) - ago * 1000).toISOString();
  return { ...row, source, session, entity: name, parent, parent_observed: parentObserved,
    entity_type: type, state: "running", reported_state: "running", stale: false,
    status_at: statusAt, started_at: new Date(Date.parse(statusAt) - 120_000).toISOString(),
    last_event_at: statusAt, project: "fixture-project", provider: "fixture-provider",
    model: "fixture-model", surface: "fixture-surface" };
}

function hierarchyRows(snapshot) {
  return [
    entity(snapshot, { source: "source-a", session: "session-a", name: "leaf", parent: "child", ago: 30 }),
    entity(snapshot, { source: "source-b", session: "session-b", name: "child", parent: "root", ago: 40 }),
    entity(snapshot, { source: "source-a", session: "session-a", name: "child", parent: "root", ago: 20 }),
    entity(snapshot, { source: "source-z", session: "session-z", name: "cycle-a", parent: "cycle-b" }),
    entity(snapshot, { source: "source-a", session: "session-a", name: "root", type: "session", parent: null, ago: 10 }),
    entity(snapshot, { source: "source-b", session: "session-b", name: "root", type: "session", parent: null, ago: 15 }),
    entity(snapshot, { source: "source-a", session: "session-a", name: "missing", parent: "ghost", parentObserved: false }),
    entity(snapshot, { source: "source-a", session: "session-a", name: "unselected", parent: "hidden-parent", parentObserved: true }),
    entity(snapshot, { source: "source-z", session: "session-z", name: "cycle-b", parent: "cycle-a" }),
  ];
}

async function setup(page, { truncated = true } = {}) {
  const response = await page.request.get("/api/v1/snapshot");
  const snapshot = await response.json();
  const rows = hierarchyRows(snapshot);
  snapshot.identity = "hierarchy-fixture-owner";
  snapshot.observed_at = observedAt;
  snapshot.entities = rows;
  snapshot.summary.entities = rows.length;
  snapshot.summary.sessions = new Set(rows.map((row) => `${row.source}/${row.session}`)).size;
  snapshot.summary.active = rows.length;
  snapshot.coverage.entities_truncated = truncated;
  let current = snapshot;
  await page.route("**/api/v1/snapshot?**", (route) => route.fulfill({ json: current }));
  return { snapshot, rows, setCurrent: (value) => { current = value; } };
}

function rowFor(page, source, session, entityName) {
  const key = JSON.stringify([source, session, entityName]);
  return page.locator(`#entity-table tbody tr[data-entity-key='${key}']`);
}

test("entity hierarchy is deterministic, source-scoped, safely qualified and locally collapsible", async ({ page }) => {
  const { snapshot, rows, setCurrent } = await setup(page);
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/");
  await page.locator('.nav[data-view="sessions"]').click();
  await expect(page.locator("#entity-table tbody tr")).toHaveCount(rows.length);
  await expect(page.locator("#entity-hierarchy-note")).toContainText("truncated at 500");
  await expect(page.locator("#entity-hierarchy-note")).toContainText("cyclic parent link");

  const keyFor = (source, session, name) => JSON.stringify([source, session, name]);
  const tableOrder = await page.locator("#entity-table tbody tr").evaluateAll((elements) => elements.map((row) => JSON.parse(row.dataset.entityKey)));
  const position = (source, session, name) => tableOrder.findIndex((key) => JSON.stringify(key) === keyFor(source, session, name));
  expect(position("source-a", "session-a", "root")).toBeLessThan(position("source-a", "session-a", "child"));
  expect(position("source-a", "session-a", "child")).toBeLessThan(position("source-a", "session-a", "leaf"));
  expect(position("source-b", "session-b", "root")).toBeLessThan(position("source-b", "session-b", "child"));
  expect(position("source-a", "session-a", "child")).not.toBe(position("source-b", "session-b", "child"));
  await expect(rowFor(page, "source-a", "session-a", "missing")).toContainText("Parent ghost not observed in the available ledger");
  await expect(rowFor(page, "source-a", "session-a", "unselected")).toContainText("Parent hidden-parent was observed but is outside this selection or the truncated display");
  await expect(rowFor(page, "source-b", "session-b", "child")).toContainText("parent root");

  const toggleName = "Collapse descendants of root · source source-a · session session-a";
  const toggle = page.getByRole("button", { name: toggleName, exact: true });
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await toggle.click();
  await expect(page.getByRole("button", { name: toggleName.replace("Collapse", "Expand"), exact: true })).toHaveAttribute("aria-expanded", "false");
  await expect(rowFor(page, "source-a", "session-a", "child")).toHaveCount(0);
  await expect(rowFor(page, "source-b", "session-b", "child")).toHaveCount(1);
  await page.waitForTimeout(2200);
  const collapsed = page.getByRole("button", { name: toggleName.replace("Collapse", "Expand"), exact: true });
  await expect(collapsed).toHaveAttribute("aria-expanded", "false");
  await expect(rowFor(page, "source-a", "session-a", "child")).toHaveCount(0);

  setCurrent({ ...snapshot, identity: "replacement-owner" });
  await expect.poll(async () => page.getByRole("button", { name: toggleName, exact: true }).getAttribute("aria-expanded"), { timeout: 5000 }).toBe("true");
  await expect(rowFor(page, "source-a", "session-a", "child")).toHaveCount(1);
});

test("lifecycle ages follow reordered rows and hierarchy controls fit mobile", async ({ page }) => {
  await page.addInitScript(() => {
    let offset = 0;
    const nativeNow = Date.now.bind(Date);
    Date.now = () => nativeNow() + offset;
    window.advanceHierarchyClock = (milliseconds) => { offset += milliseconds; };
  });
  const { rows } = await setup(page, { truncated: false });
  const terminal = rows.find((row) => row.source === "source-b" && row.session === "session-b" && row.entity === "root");
  terminal.state = "completed"; terminal.reported_state = "completed"; terminal.stale = false;
  terminal.status_at = new Date(Date.parse(observedAt) - 55_000).toISOString();
  terminal.last_event_at = terminal.status_at;
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.locator('.mobile-nav [data-view="sessions"]').click();
  const root = rowFor(page, "source-a", "session-a", "root");
  const child = rowFor(page, "source-a", "session-a", "child");
  await expect(root.locator(".state-evidence-age")).toContainText("Lifecycle evidence 10 seconds old");
  await expect(child.locator(".state-evidence-age")).toContainText("Lifecycle evidence 20 seconds old");
  const rootToggle = root.locator(".hierarchy-toggle");
  expect(await rootToggle.evaluate((button) => button.getBoundingClientRect().height)).toBeGreaterThanOrEqual(44);
  expect(await root.locator(".entity-button").evaluate((button) => button.getBoundingClientRect().height)).toBeGreaterThanOrEqual(44);
  await rootToggle.click();
  await expect(root.locator(".hierarchy-toggle")).toHaveAttribute("aria-expanded", "false");
  await expect(child).toHaveCount(0);
  const terminalRow = rowFor(page, "source-b", "session-b", "root");
  await expect(terminalRow.locator(".last-evidence-cell")).toHaveText("11:59:05 UTC");

  await page.locator("#pause").click();
  await page.evaluate(() => window.advanceHierarchyClock(10_000));
  await expect.poll(async () => {
    const text = await terminalRow.locator(".last-evidence-cell").innerText();
    return text.includes("stale");
  }, { timeout: 4000 }).toBe(true);
  await expect(terminalRow.locator(".state")).toHaveClass(/completed/);
  await expect(terminalRow.locator(".last-evidence-cell")).toContainText("11:59:05 UTC · stale");
  await expect(terminalRow.locator(".state-evidence-age")).toContainText("stale");
  await expect.poll(async () => {
    const text = await root.locator(".state-evidence-age").innerText();
    return Number(text.match(/Lifecycle evidence ([\d,]+)/)?.[1].replaceAll(",", ""));
  }, { timeout: 4000 }).toBeGreaterThanOrEqual(20);
  const rowAges = await page.locator("#entity-table tbody tr").evaluateAll((elements) => elements.map((row) => ({
    key: JSON.parse(row.dataset.entityKey),
    seconds: Number(row.querySelector(".state-evidence-age")?.textContent.match(/Lifecycle evidence ([\d,]+)/)?.[1].replaceAll(",", "")),
  })));
  const rootAge = rowAges.find((item) => item.key.join("/") === "source-a/session-a/root")?.seconds;
  const terminalAge = rowAges.find((item) => item.key.join("/") === "source-b/session-b/root")?.seconds;
  expect(rootAge).toBeGreaterThanOrEqual(15);
  expect(terminalAge).toBeGreaterThanOrEqual(65);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  // Timeline remains a chronology view of the original input order, not the hierarchy sort.
  await page.locator('.mobile-nav [data-view="overview"]').click();
  await expect(page.locator("#timeline .timeline-name").first()).toContainText("leaf");
  expect(rows[0].entity).toBe("leaf");
});
