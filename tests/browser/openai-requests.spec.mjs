import {test, expect} from '@playwright/test';

// Fictional caller receipts, independent from the demo usage ledger
async function fixture(page) {
  const {identity} = await (await page.request.get('/api/v1/snapshot')).json();
  const now = new Date().toISOString();
  const row = (status, index) => ({schema:'monitoring.openai-request-observation.v1',
    id:`response-${String(index).padStart(32, '0')}`,source:'fixture-caller',project:'fixture-project',session:'fixture-group',
    provider:'openai',model:'fixture-model',status,reported_status:status,observed_at:now,created_at:null,
    has_error:status === 'failed',complete_coverage:false,freshness:'current'});
  return {schema:'monitoring.openai-request-read.v1',identity,observed_at:now,max_age_seconds:125,read_status:'available',
    book:{schema:'monitoring.openai-request-observations.v1',scope:'explicit-caller-received-responses',complete_coverage:false,
      observed_at:now,truncated:false,rows:[row('in_progress', 1),row('completed', 2),row('failed', 3)]}};
}

test('dated reports age independently, retain focus and fit a phone', async ({page}) => {
  await page.clock.install();
  const value = await fixture(page);
  await page.route('**/api/v1/openai-requests?**', route => route.fulfill({json:value}));
  await page.goto('/');
  const panel = page.locator('#openai-request-panel');
  await expect(panel).toContainText('In progress (reported)');
  await expect(panel.locator('tbody tr')).toHaveCount(3);
  const filter = panel.getByLabel('Reported request status');
  await filter.selectOption('in_progress');
  await filter.focus();
  await page.clock.fastForward(128000);
  await expect(filter).toBeFocused();
  await expect(panel).toContainText('Stale receipt');
  await expect(panel).toContainText('current state unknown');
  await expect(panel.locator('tbody tr')).toHaveCount(1);
  await filter.selectOption('completed');
  await expect(panel).toContainText('Completed (reported)');
  await expect(panel.locator('tbody')).not.toContainText('current state unknown');
  await filter.selectOption('');
  await page.emulateMedia({colorScheme:'dark'});
  await panel.screenshot({path:'/tmp/monitoring-request-desktop.png'});
  await page.setViewportSize({width:390,height:844});
  await page.emulateMedia({colorScheme:'light'});
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  expect(await panel.locator('td').evaluateAll(cells => cells.every(cell => cell.scrollWidth <= cell.clientWidth + 1))).toBe(true);
  const region = panel.getByRole('region');
  await region.focus();
  await page.keyboard.press('ArrowRight');
  await expect(region).toBeFocused();
  await panel.screenshot({path:'/tmp/monitoring-request-phone.png'});
  await page.locator('#pause').click();
  await expect(panel).toContainText('Updates are paused');
});

test('failed reads retain facts without rendering error bodies', async ({page}) => {
  await page.clock.install();
  const value = await fixture(page);
  let failed = false;
  await page.route('**/api/v1/openai-requests?**', route => failed
    ? route.fulfill({status:503,json:{error:'PRIVATE-ERROR'}}) : route.fulfill({json:value}));
  await page.goto('/');
  const panel = page.locator('#openai-request-panel');
  await expect(panel).toContainText('In progress (reported)');
  failed = true;
  await page.clock.fastForward(14000);
  await expect(panel).toContainText('read is unavailable');
  await expect(panel).toContainText('current state unknown');
  await expect(panel).toContainText(value.book.rows[0].observed_at);
  await expect(panel).not.toContainText('PRIVATE-ERROR');
});

test('view clears owner filters, refuses malformed data and distinguishes empty evidence', async ({page}) => {
  await page.goto('/');
  const value = await fixture(page);
  await page.evaluate(async value => {
    const {mountOpenAIRequests} = await import('/openai-request-view.js');
    const root = document.createElement('div');
    root.id = 'request-isolated'; document.body.append(root);
    window.requestTestView = mountOpenAIRequests(root);
    window.requestTestView.update(value);
  }, value);
  const panel = page.locator('#request-isolated');
  const filter = panel.getByLabel('Reported request status');
  await filter.selectOption('failed');
  await page.evaluate(value => window.requestTestView.update({...value,identity:'replacement-owner'}), value);
  await expect(filter).toHaveValue('');
  await expect(panel.locator('tbody tr')).toHaveCount(3);
  await page.evaluate(value => {
    value.book.rows[0].source = '<img src=x onerror=alert(1)>';
    window.requestTestView.update(value);
  }, value);
  await expect(panel).toContainText('No interpretable');
  await expect(panel.locator('tbody tr')).toHaveCount(0);
  await expect(panel.locator('img')).toHaveCount(0);
  await page.evaluate(value => {value.book.rows=[]; window.requestTestView.update(value);}, value);
  await expect(panel).toContainText('None captured in the selected file. Account use is unknown.');
  await page.evaluate(() => window.requestTestView.clear());
  await expect(panel.locator('section')).toBeHidden();
});

test('absent route hides the optional panel and is not repeatedly polled', async ({page}) => {
  await page.clock.install();
  let reads = 0;
  await page.route('**/api/v1/openai-requests?**', route => { reads++; return route.fulfill({status:404,json:{error:'unconfigured'}}); });
  await page.goto('/');
  await expect.poll(async () => { await page.clock.runFor(2000); return reads; }).toBe(1);
  await page.clock.runFor(30000);
  expect(reads).toBe(1);
  await expect(page.locator('#openai-request-panel section')).toBeHidden();
});
