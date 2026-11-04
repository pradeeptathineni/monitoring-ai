import { test, expect } from '@playwright/test';

// Open an explicit observed entity, keeping runtime controls outside Monitoring
async function openTimeline(page) {
  await page.goto('/#sessions');
  await page.getByRole('button',{name:'Open timeline for atlas in synthetic / atlas',exact:true}).click();
  const root=page.locator('#session-timeline-panel');
  await expect(root.locator('tbody tr')).not.toHaveCount(0);
  return root;
}

test('session timeline combines lanes, markers and exact entity history with bounded evidence',async({page})=>{
  const root=await openTimeline(page);
  await expect(root).toContainText('of 57 matching records');
  await root.getByLabel('Timeline entity',{exact:true}).selectOption('');
  await expect(root.locator('tbody tr')).toHaveCount(50);
  await expect(root.locator('.timeline-mark')).toHaveCount(57);
  await expect(root).toContainText('60 seconds');
  await root.getByLabel('Follow timeline',{exact:true}).uncheck();
  const button=root.getByRole('button',{name:/Inspect timeline record/}).first();
  await button.focus();await page.waitForTimeout(2200);await expect(button).toBeFocused();
  await root.locator('tbody tr').filter({has:page.locator('td:nth-child(3)',{hasText:'tool'})}).first().getByRole('button',{name:/Inspect timeline record/}).click();
  await expect(root.getByRole('region',{name:'Timeline observation',exact:true})).toContainText('Outcome');
  await root.getByRole('button',{name:'Open entity history',exact:true}).click();
  await expect(page.locator('#events')).toBeVisible();await expect(page.locator('#entity-history-panel')).toBeVisible();
  await page.getByRole('button',{name:'Sessions and workers',exact:true}).click();
  await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
  await root.getByRole('button',{name:'Close timeline',exact:true}).click();await expect(root).toBeHidden();
});

test('timeline failure retains dated markers and owner changes require reopening',async({page})=>{
  let failed=false,changed=false;
  await page.route('**/api/v1/session-timeline?**',async route=>{if(failed)return route.fulfill({status:503,json:{error:'Synthetic failure'}});await route.continue();});
  await page.route('**/api/v1/snapshot*',async route=>{const response=await route.fetch(),value=await response.json();if(changed)value.identity='other-owner';await route.fulfill({json:value});});
  const root=await openTimeline(page);await root.getByLabel('Follow timeline',{exact:true}).uncheck();
  const rows=await root.locator('tbody').textContent();failed=true;await root.getByRole('button',{name:'Refresh timeline',exact:true}).click();
  await expect(root.locator('.timeline-read-status')).toContainText('failed');expect(await root.locator('tbody').textContent()).toBe(rows);
  await page.locator('#pause').click();await expect(root.getByRole('button',{name:'Refresh timeline',exact:true})).toBeDisabled();
  await expect(root.locator('.timeline-read-status')).toContainText('paused or unavailable');
  await page.locator('#pause').click();changed=true;
  await expect(root.locator('.timeline-read-status')).toContainText('Owner changed');await expect(root.locator('tbody tr')).toHaveCount(0);
});
