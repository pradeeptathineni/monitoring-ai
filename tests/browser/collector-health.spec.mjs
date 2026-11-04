import { test, expect } from "@playwright/test";

// Keep collector facts independent from the demo ledger's source health
async function envelope(page, state = "stopped") {
  const snapshot = await (await page.request.get("/api/v1/snapshot")).json();
  const now = new Date().toISOString();
  return {schema:"monitoring.collector-health-read.v1",identity:snapshot.identity,
    observed_at:now,max_age_seconds:125,read_status:"available",health:{
      schema:"monitoring.collector-health.v1",scope:"explicit-collector-checkpoints",complete_coverage:false,
      state,reported_state:null,last_source_state:"unavailable",freshness:"current",
      last_checkpoint_at:now,last_success_at:null,latest_event_at:null,accepted_records:4,
      reason:"collector-stopped",partial_tail:true}};
}

test("collector stop retains source failure and remains readable on phone", async ({page}) => {
  await page.emulateMedia({colorScheme:"dark"});
  const book = await envelope(page);
  await page.route("**/api/v1/collector-health?**", route => route.fulfill({json:book}));
  await page.goto("/");
  const panel = page.locator("#collector-health-panel");
  await expect(panel).toContainText("Stopped");
  await expect(panel).toContainText("Unavailable");
  await expect(panel).toContainText("The latest incomplete checkpoint waits");
  await expect(panel).toContainText("Unknown");
  await panel.screenshot({path:"/tmp/monitoring-collector-desktop.png"});
  await page.setViewportSize({width:390,height:844});
  await page.emulateMedia({colorScheme:"light"});
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  await panel.screenshot({path:"/tmp/monitoring-collector-phone.png"});
  await page.locator("#pause").click();
  await expect(panel).toContainText("Updates are paused");
  await expect(panel).toContainText("current collection state is unknown");
});

test("failed reads retain dates and stay unknown on later redraw", async ({page}) => {
  await page.clock.install();
  const book = await envelope(page,"following");
  let failed = false;
  await page.route("**/api/v1/collector-health?**", route => failed ? route.fulfill({status:503,json:{error:"PRIVATE-ERROR"}}) : route.fulfill({json:book}));
  await page.goto("/");
  const panel = page.locator("#collector-health-panel");
  await expect(panel).toContainText("Following");
  failed = true;
  await page.clock.fastForward(12000);
  await expect(panel).toContainText("checkpoint read is unavailable");
  await page.clock.fastForward(2000);
  await expect(panel).toContainText("Current collection state is unknown");
  await expect(panel).toContainText(book.health.last_checkpoint_at);
  await expect(panel).not.toContainText("PRIVATE-ERROR");
});
