import { test, expect } from "@playwright/test";

test("usage records page beyond latest events and explain unpriced records before opening entity history", async ({ page }) => {
  const expected = await (await page.request.get("/api/v1/usage-records?limit=50")).json();
  await page.goto("/#usage");
  await page.getByRole("button", { name: "Load usage records", exact: true }).click();
  await expect(page.locator("#usage-record-table tbody tr")).toHaveCount(Math.min(50,expected.total));
  await expect(page.locator("#usage-record-status")).toContainText(`of ${expected.total} matching records`);
  await expect(page.locator("#usage-record-status")).toContainText("SYNTHETIC DATA PRESENT");
  if(expected.next_offset!==null){
    await page.getByRole("button",{name:"Next usage records",exact:true}).click();
    await expect(page.locator("#usage-record-status")).toContainText(`51–${expected.total}`);
    await page.getByRole("button",{name:"Previous usage records",exact:true}).click();
    await expect(page.locator("#usage-record-table tbody tr")).toHaveCount(50);
  }
  await page.locator("#usage-cost-filter").selectOption("unpriced");
  await expect(page.locator("#usage-record-table tbody tr")).toHaveCount(0);
  await page.getByRole("button",{name:"Load usage records",exact:true}).click();
  await expect(page.locator("#usage-record-table")).toContainText("Unknown");
  await page.getByRole("button",{name:/^Inspect usage record /}).first().click();
  await expect(page.getByRole("region",{name:"Usage record detail",exact:true})).toBeVisible();
  await expect(page.locator("#usage-record-detail")).toContainText("No recorded cost or matching dated model price");
  await expect(page.locator("#usage-record-detail")).toContainText("Cache read tokens");
  await expect(page.locator("#usage-record-detail")).toContainText("Unknown");
  await page.getByRole("link",{name:"Review price and currency setup",exact:true}).click();
  await expect(page.locator("#settings")).toBeVisible();
  await page.getByText("Prices and currency conversion",{exact:true}).click();
  await expect(page.locator("#settings")).toContainText("This page does not write files");
  await page.getByRole("button",{name:"Usage and costs",exact:true}).click();
  await page.getByRole("button",{name:"Open entity history",exact:true}).click();
  await expect(page.locator("#events")).toBeVisible();
  await expect(page.locator("#entity-history-panel")).toBeVisible();
  await page.getByRole("button",{name:"Usage and costs",exact:true}).click();
  await expect(page.locator("#usage-record-table tbody tr")).toHaveCount(expected.cost_coverage.unpriced);
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
});

test("usage paging refuses another owner or filter scope and retains the prior valid page on failure", async ({ page }) => {
  let wrongOwner=false,wrongScope=false;
  await page.route("**/api/v1/usage-records?**",async route=>{
    const response=await route.fetch(),value=await response.json();
    if(wrongOwner)value.identity="another-owner";
    if(wrongScope)value.filters={};
    await route.fulfill({json:value});
  });
  await page.goto("/#usage");
  await page.getByRole("button",{name:"Load usage records",exact:true}).click();
  await expect(page.locator("#usage-record-table tbody tr")).toHaveCount(50);
  const before=await page.locator("#usage-record-table").textContent();
  wrongOwner=true;
  await page.getByRole("button",{name:"Next usage records",exact:true}).click();
  await expect(page.locator("#usage-record-status")).toContainText("did not match");
  expect(await page.locator("#usage-record-table").textContent()).toBe(before);
  wrongOwner=false;wrongScope=true;
  await page.locator('#filters select[name="provider"]').selectOption("anthropic");
  await expect(page.locator("#usage-record-table tbody tr")).toHaveCount(0);
  await page.getByRole("button",{name:"Load usage records",exact:true}).click();
  await expect(page.locator("#usage-record-status")).toContainText("did not match");
  await expect(page.locator("#usage-record-table tbody tr")).toHaveCount(0);
  wrongScope=false;
  await page.getByRole("button",{name:"Load usage records",exact:true}).click();
  await expect(page.locator("#usage-record-table tbody tr")).not.toHaveCount(0);
  await page.locator("#pause").click();
  await expect(page.getByRole("button",{name:"Reload usage records",exact:true})).toBeDisabled();
  await expect(page.locator("#usage-record-status")).toContainText("Reads are paused or unavailable");
});

test("a dated estimate shows disjoint bucket calculations without claiming a recorded bill",async({page})=>{
 const base=await(await page.request.get('/api/v1/snapshot')).json(),at='2026-11-03T10:00:00.000Z';
 const amount={amount:'0.00003',currency:'USD',basis:'estimated',provenance:'synthetic-price'};
 const event={schema:'monitoring.event.v1',source:'synthetic',id:'fixture-'+ 'a'.repeat(150),session:'fixture-'+ 'b'.repeat(150),entity:'fixture-'+ 'c'.repeat(150),entity_type:'session',parent:null,kind:'usage',timestamp:at,provider:'example',model:'priced',surface:'test',project:'fixture',tokens:{input:1,cache_read:2,cache_write:3,output:4}};
 const record={event,pricing_method:'price-book',cost_state:'estimated',explanation:'Estimate from the matching dated model price.',native_cost:amount,display_cost:amount,rate:{provider:'example',model:'priced',currency:'USD',from:'2026-11-03T00:00:00.000Z',until:'2026-11-04T00:00:00.000Z',provenance:'synthetic-price',input:'1',cache_read:'2',cache_write:'3',output:'4'},bucket_costs:{input:'0.000001',cache_read:'0.000004',cache_write:'0.000009',output:'0.000016'}};
 await page.route('**/api/v1/usage-records?**',route=>route.fulfill({json:{schema:'monitoring.usage-records.v1',identity:base.identity,revision:base.revision,observed_at:new Date().toISOString(),currency:'USD',fx:null,filters:{},cost_state:'',scope:'matching-accepted-ledger-usage',complete_coverage:false,records:[record],total:1,matching_usage_records:1,cost_coverage:{estimated:1,billed:0,unpriced:0,unconverted:0},contains_synthetic_records:true,future_dated_records:0,offset:0,limit:50,next_offset:null}}));
 await page.goto('/#usage');await page.getByRole('button',{name:'Load usage records',exact:true}).click();
 await page.getByRole('button',{name:/^Inspect usage record /}).click();
 await expect(page.locator('#usage-record-detail')).toContainText('3 tokens × 3 per 1,000,000 = 0.000009 USD');
 await expect(page.locator('#usage-record-detail')).toContainText('end excluded');
 await expect(page.locator('#usage-record-detail')).toContainText('0.00003 USD · estimated');
 const recorded=page.locator('#usage-record-detail .confidence-row').filter({hasText:'Recorded cost'});
 await expect(recorded).toContainText('Unknown');
 await expect(page.locator('#usage-record-status')).toContainText('FICTIONAL COSTS');
 expect(await page.locator('#usage-record-detail').evaluate(element=>getComputedStyle(element).outlineStyle)).toBe('solid');
 await page.setViewportSize({width:390,height:844});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
});
