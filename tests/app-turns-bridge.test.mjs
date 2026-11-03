import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
const source=await readFile(new URL("../integrations/codex-app/selected-turns.js",import.meta.url),"utf8");
const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
test("selected-turn bridge sends only metadata and uses fixed bounded reads",async()=>{
 const calls=[],commands=[];
 const tools={mcp__codex_app__read_thread:async args=>{calls.push(args);return{structuredContent:{schemaVersion:1,thread:{id:"fixture-task",kind:"codex",hostId:"local",updatedAt:1793746800,title:"private title",preview:"private prompt",cwd:"/private/path"},page:{hasMore:true,order:"newest_first"},turns:[{id:"fixture-turn",status:"inProgress",startedAt:1793746801,completedAt:null,durationMs:null,error:null,items:[{text:"private output",arguments:"private arguments"}]}]}}},clock__curr_time:async()=>({current_time:"2026-11-03 23:00:03 UTC"}),exec_command:async args=>{commands.push(args.cmd);return{exit_code:0,output:JSON.stringify({tasks:1,turns:1})}}};
 const run=new AsyncFunction("tools","text","setTimeout",source+'; return captureSelectedCodexTurns({projectRoot:"/project",output:"/private/turns.json",targets:[{threadId:"fixture-task",hostId:"local"}],samples:2});');
 await run(tools,()=>{},callback=>callback());
 assert.deepEqual(calls,Array(2).fill({threadId:"fixture-task",hostId:"local",turnLimit:4,includeOutputs:false,maxOutputCharsPerItem:1}));
 for(const command of commands)for(const privateValue of ["private title","private prompt","/private/path","private output","private arguments"])
  assert.equal(command.includes(privateValue),false);
 assert.equal(commands[0].includes("--replace"),false);assert.equal(commands[1].includes("--replace"),true);
 tools.mcp__codex_app__read_thread=async()=>({isError:true});
 await assert.rejects(run(tools,()=>{},callback=>callback()),/read failed/);assert.equal(commands.length,2);
});
test("selected-turn bridge refuses invalid selections before source access",async()=>{
 for(const targets of [[],Array(5).fill({threadId:"fixture"}),[{threadId:"fixture"},{threadId:"fixture"}],[{threadId:"fixture",extra:"value"}]]){
  const run=new AsyncFunction("tools","text","setTimeout","targets",source+';return captureSelectedCodexTurns({projectRoot:"/project",output:"/private/turns.json",targets});');
  await assert.rejects(run({},()=>{},setTimeout,targets));
 }
});
