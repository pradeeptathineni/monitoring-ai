import { test, expect } from '@playwright/test';

// Use synthetic quota frames over the real dashboard, without native provider reads
async function fixture(page, options = {}) {
  const base = await (await page.request.get('/api/v1/snapshot')).json();
  const key='a'.repeat(24), other='b'.repeat(24), now=new Date(), at=(seconds)=>new Date(now.getTime()+seconds*1000).toISOString();
  const scope={key,source:'synthetic-quota',provider:'example',account:'synthetic-account',provenance:'synthetic',id:'weekly',label:'Weekly',window_minutes:10080,observations:210,first_captured_at:at(-12600),last_captured_at:at(-10)};
  const second={...scope,key:other,id:'five-hour',label:'Five hours',window_minutes:300};
  const point=(seconds,used,segment=0,reasons=[])=>({captured_at:at(seconds),recorded_at:at(seconds+1),used_percent:used,remaining_percent:used===null?null:100-used,resets_at:at(86400),segment,break_reasons:reasons,future:false});
  const current={...scope,captured_at:at(-10),used_percent:48,remaining_percent:52,resets_at:at(86400),freshness:'current',age_seconds:10};delete current.key;delete current.observations;delete current.first_captured_at;delete current.last_captured_at;
  const state={revision:210,failed:false,slow:false,requests:[],...options};
  await page.route('**/api/v1/snapshot*',route=>route.fulfill({json:{...base,observed_at:new Date().toISOString(),quota_history_status:{schema:'monitoring.quota-history-status.v1',revision:state.revision,observations:210,series_count:2,capacity:10000,series_capacity:100,read_only:false,state:'recording',source_state:'following'}}}));
  await page.route('**/api/v1/quota-history?**',async route=>{
    const url=new URL(route.request().url());state.requests.push(url.searchParams);
    if(state.slow)await new Promise(resolve=>setTimeout(resolve,400));
    if(state.failed)return route.fulfill({status:503,json:{error:'Synthetic unavailable'}});
    const older=url.searchParams.get('before')!==null,selected=url.searchParams.get('series')||key;
    const points=older?[point(-12500,30),point(-12400,31)]:[point(-180,45),point(-120,46),point(-100,null),point(-60,47,1,['percentage-unavailable']),point(-10,48,1)];
    await route.fulfill({json:{schema:'monitoring.quota-history.v1',identity:base.identity,revision:state.revision,observed_at:new Date().toISOString(),scope:'explicit-recorded-quota-observations',complete_coverage:false,series:[scope,second],selected,points,before:url.searchParams.get('before'),next_before:older?null:points[0].captured_at,recording_status:{state:'recording',error:null,last_saved_at:at(-9)},source_read_state:'following',current_window:selected===key?current:{...current,id:'five-hour',label:'Five hours',window_minutes:300},synthetic_selected:true}});
  });
  return state;
}

test('quota history keeps current quota separate from older pages, scopes windows and preserves gaps',async({page})=>{
  const state=await fixture(page);await page.goto('/#usage');
  const root=page.locator('#quota-history-panel');
  await expect(root.locator('.qh-current')).toContainText('48% used, 52% remaining');
  await expect(root.locator('.qh-banner')).toBeVisible();
  await expect(root.locator('polyline')).toHaveCount(2);
  await expect(root.locator('circle')).toHaveCount(4);
  await expect(root.locator('.qh-breaks')).toContainText('Percentage not reported');
  await root.getByRole('button',{name:'Older observations',exact:true}).click();
  await expect(root.locator('.qh-page')).toContainText('Older page');
  await expect(root.locator('.qh-current')).toContainText('48% used');
  await expect(root.locator('tbody tr')).toHaveCount(2);
  const pinned=state.requests.at(-1);expect(pinned.get('expected_revision')).toBe('210');expect(pinned.get('expected_identity')).toBeTruthy();
  state.revision=211;
  await expect(root.locator('.qh-notice')).toContainText('stays unchanged');
  await expect(root.locator('tbody tr')).toHaveCount(2);
  await root.getByRole('button',{name:'Latest observations',exact:true}).click();
  await expect(root.locator('tbody tr')).toHaveCount(5);
  await root.getByLabel('Quota window',{exact:true}).selectOption('b'.repeat(24));
  await expect(root.locator('.qh-scope')).toContainText('Five hours');
  expect(state.requests.at(-1).get('series')).toBe('b'.repeat(24));
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
  await page.evaluate(()=>{const clock=Date.now;Date.now=()=>clock()+301000;});
  await expect(root.locator('.qh-current')).toContainText('not known');
  await page.locator('#pause').click();
  await expect(root.locator('.qh-current')).toContainText('not known: updates are paused');
  await expect(root.getByRole('button',{name:'Refresh history',exact:true})).toHaveAttribute('aria-disabled','true');
});

test('failed history reads retain dated points and cancel obsolete reads when leaving the view',async({page})=>{
  const state=await fixture(page,{failed:true});await page.goto('/#usage');const root=page.locator('#quota-history-panel');
  await expect(root.locator('.qh-status')).toContainText('temporarily unavailable');
  state.failed=false;await root.getByRole('button',{name:'Refresh history',exact:true}).click();
  await expect(root.locator('tbody tr')).toHaveCount(5);
  state.failed=true;await root.getByRole('button',{name:'Refresh history',exact:true}).click();
  await expect(root.locator('.qh-status')).toContainText('temporarily unavailable');
  await expect(root.locator('tbody tr')).toHaveCount(5);
  await expect(root.locator('.qh-current')).toContainText('not known');
  state.failed=false;state.slow=true;await root.getByRole('button',{name:'Refresh history',exact:true}).click();
  await page.getByRole('button',{name:'Overview',exact:true}).click();
  await page.waitForTimeout(500);
  await page.getByRole('button',{name:'Usage and costs',exact:true}).click();
  await root.getByRole('button',{name:'Refresh history',exact:true}).click();
  await expect(root.locator('.qh-status')).toContainText('History revision 210');
});
