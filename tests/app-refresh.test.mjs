import { test } from 'node:test';import assert from 'node:assert/strict';import { readFile } from 'node:fs/promises';
const source=await readFile(new URL('../integrations/codex-app/finite-refresh.js',import.meta.url),'utf8');
const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
test('finite supported bridge selects metadata and deduplicates without forwarding private fields',async()=>{
 const commands=[],receipts=[];const row={id:'task-id',kind:'codex',hostId:'local',status:'active',updatedAt:1793730000,title:'private title',summary:'private prompt',cwd:'/private/location'};
 const tools={mcp__codex_app__list_threads:async()=>({content:[{type:'text',text:JSON.stringify({pinnedThreads:[row],threads:[row]})}]}),clock__curr_time:async()=>({current_time:'2026-11-03 19:00:00 UTC'}),exec_command:async(args)=>{commands.push(args.cmd);return{exit_code:0,output:JSON.stringify({captured_at:'2026-11-03T19:00:00.000Z',tasks:1})}}};
 const run=new AsyncFunction('tools','text','setTimeout',source+';return refreshAppInventory({projectRoot:"/project",output:"/private/export.json",samples:3})');
 const result=await run(tools,x=>receipts.push(x),callback=>callback());assert.equal(result.samples,3);assert.equal(commands.length,3);assert.equal(receipts.length,3);
 for(const command of commands){assert.equal(command.includes('private prompt'),false);assert.equal(command.includes('private title'),false);assert.equal(command.includes('/private/location'),false);assert.equal((command.match(/"id":"task-id"/g)||[]).length,1)}
 tools.mcp__codex_app__list_threads=async()=>({content:[{type:'text',text:JSON.stringify({pinnedThreads:[row],threads:[{...row,status:'idle'}]})}]});
 await assert.rejects(run(tools,()=>{},callback=>callback()),/conflicting metadata/);assert.equal(commands.length,3);
});
test('bridge rejects an unbounded sample request before any source access',async()=>{
 const run=new AsyncFunction('tools','text','setTimeout',source+';return refreshAppInventory({projectRoot:"/project",output:"/private/export.json",samples:100})');await assert.rejects(run({},()=>{},setTimeout),/finite samples/);
});
