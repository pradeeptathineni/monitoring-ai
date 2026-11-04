import { test, expect } from '@playwright/test';

// Ask the actual owner for the exact revision/rate scope before comparing UI evidence
async function readScenario(page) {
  const snapshot=await(await page.request.get('/api/v1/snapshot')).json();
  const params=new URLSearchParams({expected_identity:snapshot.identity,expected_revision:String(snapshot.revision),expected_scenario:snapshot.cost_scenario_status.id});
  return (await page.request.get('/api/v1/cost-scenario?'+params)).json();
}

test('explicit scenario shows separate cost evidence, shared-intersection differences and Settings setup',async({page})=>{
  const expected=await readScenario(page),requests=[];
  page.on('request',request=>{if(request.url().includes('/api/v1/cost-scenario?'))requests.push(new URL(request.url()));});
  await page.goto('/#report');const root=page.locator('#cost-scenario-panel');
  await expect(root.getByRole('heading',{name:'Cost scenario',exact:true})).toBeVisible();
  expect(requests.length).toBe(0);
  await root.getByRole('button',{name:'Load cost scenario',exact:true}).click();
  await expect(root).toContainText('COUNTERFACTUAL ESTIMATE');
  await expect(root.locator('tbody tr')).toHaveCount(expected.groups.length);
  await expect(root).toContainText(expected.alternative.amount);
  await expect(root).toContainText(expected.comparable.difference);
  await expect(root).toContainText(/billed/i);
  expect(requests[0].searchParams.get('expected_scenario')).toBe(expected.scenario.id);
  await page.waitForTimeout(2200);expect(requests.length).toBe(1);
  await page.locator('#filters select[name="provider"]').selectOption('anthropic');
  await expect(root.locator('tbody tr')).toHaveCount(0);
  await root.getByRole('button',{name:'Load cost scenario',exact:true}).click();
  await expect(root.locator('tbody tr')).toHaveCount(1);
  expect(requests.at(-1).searchParams.get('provider')).toBe('anthropic');
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
  await root.getByText('Alternative rates (2)',{exact:true}).click();
  await expect(root.getByRole('region',{name:'Alternative price book, scrollable'})).toContainText('fictional-alternative');
  await expect(root).toContainText('Rates per 1,000,000 disjoint tokens');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
  await root.getByRole('link',{name:'Review rate-book setup',exact:true}).click();
  await expect(page.locator('#settings')).toBeVisible();
  await page.getByText('Compare a counterfactual price book',{exact:true}).click();
  await expect(page.locator('#settings')).toContainText('does not write files or fetch prices');
});

test('a failed recalculation retains dated results and a changed owner cannot reuse them',async({page})=>{
  let failed=false,ownerChanged=false;
  await page.route('**/api/v1/snapshot*',async route=>{
    const response=await route.fetch(),value=await response.json();
    if(ownerChanged)value.identity='another-owner';await route.fulfill({json:value});
  });
  await page.route('**/api/v1/cost-scenario?**',async route=>{
    if(failed)return route.fulfill({status:503,json:{error:'Synthetic unavailable'}});
    await route.continue();
  });
  await page.goto('/#report');const root=page.locator('#cost-scenario-panel');
  await root.getByRole('button',{name:'Load cost scenario',exact:true}).click();
  await expect(root.locator('tbody tr')).not.toHaveCount(0);
  const rows=await root.locator('tbody').textContent();
  failed=true;await root.getByRole('button',{name:/Recalculate/,exact:true}).click();
  await expect(root).toContainText(/unavailable|failed|could not/i);
  expect(await root.locator('tbody').textContent()).toBe(rows);
  await page.locator('#pause').click();
  await expect(root.getByRole('button',{name:/Recalculate/,exact:true})).toBeDisabled();
  await page.locator('#pause').click();ownerChanged=true;
  await expect(root.locator('tbody tr')).toHaveCount(0);
  await root.getByRole('button',{name:'Load cost scenario',exact:true}).click();
  await expect(root).toContainText(/changed|mismatch|409/i);
  await expect(root.locator('tbody tr')).toHaveCount(0);
});
