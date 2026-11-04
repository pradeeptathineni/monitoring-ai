/* Read one exact normalized session; timeline marks never become execution targets. */
(function () {
  'use strict';
  const terminal = new Set(['completed', 'failed', 'cancelled']);
  const states = new Set(['running', 'waiting', 'blocked', 'completed', 'failed', 'cancelled', 'unknown']);
  const buckets = ['input', 'cache_read', 'cache_write', 'output'];
  const SVG = 'http://www.w3.org/2000/svg';
  const stamp = value => typeof value === 'string' && Number.isFinite(Date.parse(value));
  const count = value => Number.isSafeInteger(value) && value >= 0;
  const pairs = query => JSON.stringify(Object.entries(query || {}).sort(([a], [b]) => a.localeCompare(b)));

  // Owner strings enter the page only through textContent or fixed SVG attributes
  function node(tag, text, cls) { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; }
  function svg(tag, attrs) { const n = document.createElementNS(SVG, tag); for (const [key,value] of Object.entries(attrs)) n.setAttribute(key,String(value)); return n; }

  // Refuse mixed identities, accounting scope and malformed marker fields before display
  function validate(frame, expected) {
    if (!frame || frame.schema !== 'monitoring.session-timeline.v1' || frame.identity !== expected.selection.identity || frame.revision !== expected.revision || frame.source !== expected.selection.source || frame.session !== expected.selection.session || pairs(frame.filters) !== pairs(expected.query)) throw Error('Timeline owner, revision or exact scope did not match.');
    if (frame.scope !== 'exact-source-session-recorded-evidence' || frame.complete_coverage !== false || !stamp(frame.observed_at) || !frame.range || (frame.range.from !== null && !stamp(frame.range.from)) || !stamp(frame.range.until)) throw Error('Timeline range or coverage is unsupported.');
    if (!Array.isArray(frame.lanes) || frame.lanes.length > 100 || !Array.isArray(frame.markers) || frame.markers.length > 500 || !Array.isArray(frame.segments) || frame.segments.length > 600) throw Error('Timeline exceeds the supported display bounds.');
    const keys = new Set();
    for (const lane of frame.lanes) {
      if (typeof lane.entity !== 'string' || keys.has(lane.entity) || !count(lane.shown_records) || lane.shown_records < 1 || !lane.latest_status || !states.has(lane.latest_status.state)) throw Error('Timeline lane metadata is unsupported.');
      const status=lane.latest_status;
      if((status.reported_state!==null&&!states.has(status.reported_state))||(status.status_at!==null&&!stamp(status.status_at))||typeof status.stale!=='boolean'||typeof status.conflicting!=='boolean')throw Error('Timeline status evidence is unsupported.');
      keys.add(lane.entity);
    }
    const ids=new Set();
    for (const marker of frame.markers) {
      if (!keys.has(marker.entity) || typeof marker.id !== 'string' || ids.has(marker.id) || !stamp(marker.timestamp) || !['status','usage','tool'].includes(marker.kind)) throw Error('Timeline marker metadata is unsupported.');
      ids.add(marker.id);
      const allowed = new Set(['id','entity','kind','timestamp','state','tokens','tool','outcome','tool_category','duration_ms']);
      if (Object.keys(marker).some(key => !allowed.has(key))) throw Error('Timeline marker has unsupported fields.');
      if (marker.kind === 'status' && !states.has(marker.state)) throw Error('Timeline status is unsupported.');
      if (marker.kind === 'usage' && (!marker.tokens || !buckets.every(key => count(marker.tokens[key])))) throw Error('Timeline token buckets are unsupported.');
      if (marker.kind === 'tool' && (typeof marker.tool !== 'string' || !['ok','error','unknown'].includes(marker.outcome))) throw Error('Timeline tool evidence is unsupported.');
    }
    for (const segment of frame.segments) if (!keys.has(segment.entity) || !['running','waiting','blocked'].includes(segment.state) || !stamp(segment.from) || !stamp(segment.until) || !stamp(segment.source_at) || Date.parse(segment.until) <= Date.parse(segment.from) || Date.parse(segment.until)-Date.parse(segment.source_at)>60000) throw Error('Timeline status interval is unsupported.');
    for(const lane of frame.lanes)if(frame.markers.filter(marker=>marker.entity===lane.entity).length!==lane.shown_records)throw Error('Timeline lane counts do not reconcile.');
    const c = frame.coverage;
    if (!c || !['matching_records','shown_records','omitted_records','matching_lanes','shown_lanes','omitted_lanes','context_status_records','future_records_excluded'].every(key=>count(c[key])) || c.shown_records !== frame.markers.length || c.shown_lanes !== frame.lanes.length || c.matching_records !== c.shown_records+c.omitted_records || c.matching_lanes !== c.shown_lanes+c.omitted_lanes) throw Error('Timeline omission counts do not reconcile.');
    return frame;
  }

  // Keep one panel's requests, view-only follow choice and selected observation together
  function mount(root, options) {
    let drawnFrame = null, drawnLane = null, optionKey = '', lastEntity = null, unsupported = false;
    let frame = null, key = null, busy = false, controller = null, generation = 0, lastAttempt = 0, error = '', selected = null, lane = '', destroyed = false;
    root.classList.add('session-timeline-view'); root.setAttribute('role','region'); root.setAttribute('aria-label','Recorded session timeline');
    const title = node('h2','Recorded session timeline'), scope = node('p',null,'timeline-scope'), status = node('p',null,'timeline-read-status'); status.setAttribute('role','status');
    const controls = node('div',null,'timeline-controls'), refresh = node('button','Refresh timeline'), close = node('button','Close timeline'); refresh.type=close.type='button';
    const follow = node('input'); follow.type='checkbox'; follow.checked=true; const followLabel=node('label','Follow timeline'); followLabel.prepend(follow);
    const select=node('select'); select.setAttribute('aria-label','Timeline entity'); const selectLabel=node('label','Entity lane'); selectLabel.append(select); controls.append(refresh,followLabel,selectLabel,close);
    const latest=node('p',null,'timeline-lane-status');
    const note=node('p','Bars show at most 60 seconds of reported nonterminal state evidence. Gaps are unknown, not idle or continuous work. Usage and tools do not make an entity running. Terminal markers are recorded outcomes, not task acceptance or conversation closure.','timeline-note');
    const chart=node('div',null,'timeline-chart');chart.tabIndex=0;chart.setAttribute('role','region');chart.setAttribute('aria-label','Session evidence timeline, scrollable');
    const legend=node('p','◆ Status · ● Usage · ■ Tool · shaded intervals: bounded reported state evidence. Co-timed markers share one x position; the table keeps their separate records. The axis fits returned observations and bounded status evidence. All times UTC.','timeline-note');
    const tableWrap=node('div',null,'timeline-table-wrap'), table=node('table'), head=node('thead'), tr=node('tr'), body=node('tbody');
    for(const text of ['Time · UTC','Entity','Kind','Recorded details','Inspect']){const th=node('th',text);th.scope='col';tr.append(th);}head.append(tr);table.append(head,body);tableWrap.append(table);tableWrap.tabIndex=0;tableWrap.setAttribute('role','region');tableWrap.setAttribute('aria-label','Timeline observations, scrollable');
    const tableNote=node('p',null,'timeline-note'), detail=node('section',null,'timeline-detail'); detail.setAttribute('aria-label','Timeline observation');detail.tabIndex=-1;detail.hidden=true;
    root.replaceChildren(title,scope,controls,status,latest,note,chart,legend,tableNote,tableWrap,detail);

    // Selection identity stays pinned until the user explicitly reopens a current owner's row
    function context() { try { return options.getContext(); } catch { return null; } }
    function signature(ctx) { return ctx?.selection ? JSON.stringify([ctx.selection.identity,ctx.selection.source,ctx.selection.session,pairs(ctx.query)]) : null; }
    function blocked(ctx) { return !ctx?.selection || ctx.selection.identity !== ctx.identity || !ctx.active || ctx.paused || ctx.unavailable; }
    function abort() { generation++;controller?.abort();controller=null;busy=false; }

    // All reads stay on the same loopback owner and never refresh underlying producer evidence
    async function read() {
      const ctx=context(); if(blocked(ctx)||busy||destroyed)return;
      const expected={...ctx,query:{...ctx.query},selection:{...ctx.selection}}, id=++generation;
      controller=new AbortController();const active=controller;busy=true;lastAttempt=Date.now();error='';unsupported=false;render();
      const timer=setTimeout(()=>active.abort(),8000),params=new URLSearchParams(expected.query);
      params.set('expected_identity',expected.selection.identity);params.set('expected_revision',String(expected.revision));
      try {
        const value=await options.requestJSON('/api/v1/session-timeline?'+params,{signal:active.signal});
        const current=context();if(id!==generation||destroyed||blocked(current)||signature(current)!==signature(expected)||current.revision!==expected.revision)return;
        frame=validate(value,expected);if(selected&&!frame.markers.some(row=>row.id===selected.id))selected=null;
      } catch(cause) {
        if(id!==generation||destroyed)return;
        unsupported=cause.status===404;
        error=cause.status===404?'Session timelines are unsupported by this owner (404).':cause.status===409?'Owner or ledger changed. Refresh after reviewing the current selection.':cause.name==='AbortError'?'Timeline read timed out. Prior observations remain dated history.':cause.message||'Timeline read failed.';
      } finally { clearTimeout(timer);if(id===generation){busy=false;controller=null;render();} }
    }

    // Current labels age independently of repeated HTTP successes
    function stateText(row,ctx) {
      const s=row.latest_status;if(!s.status_at)return 'No supplied status';if(s.conflicting)return 'Conflicting status evidence';
      if(terminal.has(s.reported_state))return 'Recorded '+s.reported_state;
      const age=(Date.parse(ctx?.observed_at||new Date().toISOString())-Date.parse(s.status_at))/1000;
      if(error||ctx?.paused||ctx?.unavailable||frame?.source_status?.state==='unavailable'||age<0||age>60||s.state==='unknown')return 'Current status unknown · reported '+(s.reported_state||'unknown');
      return 'Reported '+s.reported_state+' · dated evidence';
    }

    // Position original timestamps on a shared axis without deriving tool starts or cost
    function draw(lanes) {
      chart.replaceChildren();if(!frame?.range.from||!lanes.length){chart.append(node('p','No matching retained observations. This does not establish idleness.'));return;}
      const entities=new Set(lanes.map(row=>row.entity)),times=frame.markers.filter(row=>entities.has(row.entity)).map(row=>Date.parse(row.timestamp));
      for(const segment of frame.segments)if(entities.has(segment.entity))times.push(Date.parse(segment.from),Date.parse(segment.until));
      const start=Math.max(Date.parse(frame.range.from),Math.min(...times)),end=Math.min(Date.parse(frame.range.until),Math.max(...times));
      const width=1200,left=190,right=20,top=44,rowHeight=38,height=top+lanes.length*rowHeight+18,span=Math.max(1,end-start);
      const x=time=>left+Math.min(1,Math.max(0,(Date.parse(time)-start)/span))*(width-left-right),canvas=svg('svg',{viewBox:`0 0 ${width} ${height}`,width,height,role:'img','aria-label':'Dated status evidence and usage/tool markers by normalized entity'});
      for(const fraction of [0,.5,1]){const px=left+fraction*(width-left-right),label=svg('text',{x:px,y:22,'text-anchor':fraction===0?'start':fraction===1?'end':'middle'});label.textContent=new Date(start+fraction*span).toISOString().slice(11,23);canvas.append(label);canvas.append(svg('line',{x1:px,y1:30,x2:px,y2:height-10,class:'timeline-grid'}));}
      const rowMap=new Map(lanes.map((row,index)=>[row.entity,top+index*rowHeight]));
      lanes.forEach(row=>{const y=rowMap.get(row.entity),label=svg('text',{x:8+Math.min(row.depth||0,7)*10,y:y+17});label.textContent=row.entity.length>20?row.entity.slice(0,19)+'…':row.entity;const title=svg('title',{});title.textContent=row.entity;label.append(title);canvas.append(label);canvas.append(svg('line',{x1:left,y1:y+24,x2:width-right,y2:y+24,class:'timeline-grid'}));});
      for(const band of frame.segments){if(!rowMap.has(band.entity))continue;const rect=svg('rect',{x:x(band.from),y:rowMap.get(band.entity)+3,width:Math.max(1,x(band.until)-x(band.from)),height:20,class:'timeline-band timeline-'+band.state});const tip=svg('title',{});tip.textContent=`${band.state} evidence ${band.from} to ${band.until}, source ${band.source_at}`;rect.append(tip);canvas.append(rect);}
      const coTimed=new Map(),positions=new Map();for(const mark of frame.markers){const key=mark.entity+'|'+mark.timestamp;coTimed.set(key,(coTimed.get(key)||0)+1);}
      for(const mark of frame.markers){if(!rowMap.has(mark.entity))continue;const key=mark.entity+'|'+mark.timestamp,index=positions.get(key)||0;positions.set(key,index+1);const offset=coTimed.get(key)>1?[-5,5,0][index%3]:0;const px=x(mark.timestamp),py=rowMap.get(mark.entity)+13+offset;const item=mark.kind==='usage'?svg('circle',{cx:px,cy:py,r:4,class:'timeline-mark timeline-usage'}):svg('rect',{x:px-4,y:py-4,width:8,height:8,class:'timeline-mark timeline-'+mark.kind,...(mark.kind==='status'?{transform:`rotate(45 ${px} ${py})`}:{})});const tip=svg('title',{});tip.textContent=`${mark.timestamp} · ${mark.entity} · ${mark.kind} · ${mark.state||mark.tool||'token observation'}`;item.append(tip);canvas.append(item);}
      chart.append(canvas);
    }

    // Inspect only projected metadata; raw inputs, result bodies and provider credentials never enter this panel
    function showDetail(mark,focus=false) {
      detail.replaceChildren();detail.hidden=!mark;if(!mark)return;
      detail.append(node('h3','Recorded observation'));
      const rows=[['Time',mark.timestamp],['Entity',mark.entity],['Record ID',mark.id],['Kind',mark.kind]];
      if(mark.kind==='status')rows.push(['Reported state',mark.state]);
      if(mark.kind==='usage')for(const field of buckets)rows.push([field,mark.tokens[field].toLocaleString('en-US')]);
      if(mark.kind==='tool')rows.push(['Tool',mark.tool],['Outcome',mark.outcome],['Category',mark.tool_category||'Unclassified']);
      if(mark.duration_ms!=null)rows.push(['Reported duration · ms',String(mark.duration_ms)]);
      const list=node('dl');for(const [name,value]of rows){const pair=node('div');pair.append(node('dt',name),node('dd',value));list.append(pair);}detail.append(list);
      if(options.onInspect){const open=node('button','Open entity history');open.type='button';open.disabled=blocked(context());open.addEventListener('click',()=>options.onInspect(mark));detail.append(open);}
      if(focus)detail.focus();
    }

    // Retain failed reads as history and explain every display bound
    function render() {
      if(destroyed)return;const ctx=context();root.hidden=!ctx?.selection;if(root.hidden)return;
      scope.textContent=`${ctx.selection.source} / ${ctx.selection.session}${ctx.selection.source.startsWith('synthetic')?' · SYNTHETIC DATA':''}`;
      refresh.disabled=blocked(ctx)||busy;follow.disabled=ctx.selection.identity!==ctx.identity;root.setAttribute('aria-busy',String(busy));
      let message=busy?'Reading selected session timeline…':error||(!frame?'Waiting for a selected-session read.':`Revision ${frame.revision} · observed ${frame.observed_at} · range ${frame.range.from||'empty'} through ${frame.range.until}.`);
      if(ctx.selection.identity!==ctx.identity)message='Owner changed. Close this timeline and reopen an entity from the current owner.';
      else if(ctx.paused||ctx.unavailable)message+=' Refresh is paused or unavailable; displayed data is retained history.';
      if(frame&&frame.revision!==ctx.revision)message+=' Ledger changed; this dated result has not been refreshed.';
      status.textContent=message;
      const entities=frame?.lanes.map(row=>row.entity).join('|')||'';
      if(entities!==optionKey){optionKey=entities;select.replaceChildren(new Option('All returned entities',''));for(const row of frame?.lanes||[])select.append(new Option(row.entity,row.entity));}
      for(const button of detail.querySelectorAll('button'))button.disabled=blocked(ctx);
      if(!frame){latest.textContent='No selected-entity status is loaded.';drawnFrame=null;drawnLane=null;body.replaceChildren();chart.replaceChildren();tableNote.textContent='No timeline page loaded.';detail.hidden=true;return;}
      if(lane&&!frame.lanes.some(row=>row.entity===lane))lane='';select.value=lane;
      const activeLane=frame.lanes.find(row=>row.entity===lane);
      latest.textContent=activeLane?`${stateText(activeLane,ctx)} · latest source status ${activeLane.latest_status.status_at||'not supplied'}. Parent ${activeLane.parent||'none'}: ${activeLane.parent_state}${activeLane.cycle?' · cycle cut for display':''}.`:`${frame.lanes.length} returned lanes; ${frame.lanes.filter(row=>row.parent_state==='unobserved').length} have unobserved parents. Choose a lane to inspect its latest accepted status.`;
      if(frame===drawnFrame&&lane===drawnLane)return;
      const focused=body.contains(document.activeElement)?document.activeElement.getAttribute('aria-label'):null;
      drawnFrame=frame;drawnLane=lane;body.replaceChildren();
      const rows=frame.lanes.filter(row=>!lane||row.entity===lane);draw(rows);
      const markers=frame.markers.filter(mark=>!lane||mark.entity===lane), c=frame.coverage;
      tableNote.textContent=`${c.shown_records} of ${c.matching_records} matching records shown; ${c.shown_lanes} of ${c.matching_lanes} entity lanes. ${c.omitted_records} records and ${c.omitted_lanes} lanes omitted by bounds; ${c.context_status_records} status-context records; ${c.future_records_excluded} future records excluded. Table lists latest ${Math.min(50,markers.length)} of ${markers.length} retained markers for this lane selection. Token counts describe shown usage only. Coverage remains incomplete.`;
      for(const mark of markers.slice(-50).reverse()){const tr=node('tr'),row=frame.lanes.find(row=>row.entity===mark.entity);let text=mark.kind==='status'?`Reported ${mark.state}`:mark.kind==='tool'?`${mark.tool} · ${mark.outcome}`:buckets.map(key=>`${key} ${mark.tokens[key].toLocaleString('en-US')}`).join(' · ');const inspect=node('button','Inspect');inspect.type='button';inspect.setAttribute('aria-label',`Inspect timeline record ${mark.id}`);inspect.addEventListener('click',()=>{selected=mark;showDetail(mark,true);});for(const value of [mark.timestamp,mark.entity,mark.kind,text])tr.append(node('td',value));const td=node('td');td.append(inspect);tr.append(td);body.append(tr);}
      showDetail(selected);
      if(focused){const match=[...body.querySelectorAll('button')].find(button=>button.getAttribute('aria-label')===focused);match?.focus({preventScroll:true});}
    }

    // Existing snapshot refresh drives read-only follow; no background timer or persisted setting is installed
    function update() {
      if(destroyed)return;const ctx=context(),next=signature(ctx);
      if(next!==key){abort();key=next;frame=null;selected=null;lane=ctx?.selection?.entity||'';error='';unsupported=false;lastAttempt=0;follow.checked=true;lastEntity=ctx?.selection?.entity||null;}
      else if(ctx?.selection?.entity!==lastEntity){lastEntity=ctx?.selection?.entity||null;lane=lastEntity||'';}
      if(ctx?.selection&&ctx.selection.identity!==ctx.identity&&(frame||busy)){abort();frame=null;selected=null;}
      if(blocked(ctx)&&busy)abort();render();
      if(!blocked(ctx)&&!busy&&!unsupported&&follow.checked&&(lastAttempt===0||(Date.now()-lastAttempt>=2000&&frame&&frame.revision!==ctx.revision)||Date.now()-lastAttempt>=10000))read();
    }
    const onRefresh=()=>read(),onSelect=()=>{lane=select.value;render();},onClose=()=>options.onClose?.(),onFollow=()=>{if(follow.checked)update();};
    refresh.addEventListener('click',onRefresh);select.addEventListener('change',onSelect);close.addEventListener('click',onClose);follow.addEventListener('change',onFollow);
    update();
    return {update,destroy(){destroyed=true;abort();refresh.removeEventListener('click',onRefresh);select.removeEventListener('change',onSelect);close.removeEventListener('click',onClose);follow.removeEventListener('change',onFollow);root.replaceChildren();}};
  }
  window.MonitoringSessionTimeline={mount};
})();
