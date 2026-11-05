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
  const disclosure = panel.locator('summary', {hasText:'Collector evidence'});
  await expect(panel.locator('details')).not.toHaveAttribute('open');
  await expect(panel).toContainText('A readable output is not an upstream heartbeat');
  await expect(panel).toContainText('Do not remove history declarations');
  await disclosure.focus();
  await page.keyboard.press('Enter');
  await expect(panel.locator('details')).toHaveAttribute('open', '');
  await expect(panel.locator('dl')).toBeVisible();
  await expect(disclosure).toBeFocused();
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

// A replaced owner may return headers before its old JSON body finishes
test("replacement owner rejects a delayed prior-owner body", async ({page}) => {
  await page.clock.install();
  const book = await envelope(page);
  const snapshot = await (await page.request.get('/api/v1/snapshot')).json();
  let replaced = false;
  await page.route('**/api/v1/snapshot?**', route => route.fulfill({json:{...snapshot, identity:replaced ? 'replacement-owner' : snapshot.identity}}));
  await page.route('**/api/v1/collector-health?**', route => route.fulfill({json:{...book,
    identity:new URL(route.request().url()).searchParams.get('expected_identity')}}));
  await page.addInitScript(() => {
    const fetchOriginal = window.fetch;
    let delayed = false;
    window.fetch = async (...args) => {
      const response = await fetchOriginal(...args);
      if (String(args[0]).includes('/api/v1/snapshot?')) {
        const json = response.json.bind(response);
        response.json = async () => {
          const value = await json();
          window.latestSnapshotIdentity = value.identity;
          return value;
        };
      }
      if (String(args[0]).includes('/api/v1/collector-health?') && !delayed) {
        delayed = true;
        const json = response.json.bind(response);
        response.json = async () => {
          const value = await json();
          window.oldCollectorWaiting = true;
          await new Promise(resolve => { window.releaseOldCollector = resolve; });
          window.oldCollectorBodyReturned = true;
          return value;
        };
      }
      return response;
    };
  });
  await page.goto('/');
  await expect.poll(() => page.evaluate(() => window.oldCollectorWaiting)).toBe(true);
  replaced = true;
  await expect.poll(async () => {
    await page.clock.runFor(2000);
    return page.evaluate(() => window.latestSnapshotIdentity);
  }).toBe('replacement-owner');
  await page.evaluate(() => window.releaseOldCollector());
  await expect.poll(() => page.evaluate(() => window.oldCollectorBodyReturned)).toBe(true);
  await expect(page.locator('#collector-health-panel')).not.toContainText(book.identity);
  await page.clock.runFor(2000);
  await expect(page.locator('#collector-health-panel')).toContainText('replacement-owner');
});
