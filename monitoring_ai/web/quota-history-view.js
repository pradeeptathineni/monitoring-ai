/* quota-history-view.js — read-only quota history panel. No dependencies. */
(function () {
  'use strict';

  var ENDPOINT = '/api/v1/quota-history';
  var LIMIT = 200, MAX_SERIES = 100, MAX_POINTS = 200, TABLE_ROWS = 20;
  var TIMEOUT_MS = 8000, MAX_CURRENT_AGE = 300;
  var HEX24 = /^[0-9a-f]{24}$/;
  var ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$/;
  var SVG_NS = 'http://www.w3.org/2000/svg';
  var FRESHNESS = { current: 1, stale: 1, future: 1, 'reset-passed': 1 };
  var REASONS = {
    'collector-changed': 'Collector changed',
    'collection-gap': 'Collection stopped and later resumed',
    'observation-gap': 'More than 3 minutes since the previous observation',
    'reset-time-changed': 'Reported reset time changed',
    'percentage-unavailable': 'Percentage not reported here or in the previous observation',
    'reported-usage-decreased': 'Reported usage decreased (this does not prove a reset happened)',
    'future-source-time': 'Source time is later than the read time; not plotted'
  };
  var RECORDING = {
    recording: 'Recording new observations',
    'recorded-only': 'Recorded history only. No new observations are being collected',
    unread: 'Waiting for the first accepted source read',
    'waiting-source': 'Waiting for a successful source read. Earlier observations are retained',
    capacity: 'Capacity reached. Existing observations are kept; new ones are not recorded',
    unavailable: 'Recording is unavailable', 'read-only': 'Read-only. No new observations are recorded',
    paused: 'Recording is paused', idle: 'Not recording'
  };
  var SOURCE = {
    unconfigured: 'No current-window source configured', static: 'Supplied static quota book', reading: 'Reading the current source',
    following: 'Following the current source', paused: 'Source reads are paused',
    unavailable: 'Source is unavailable', stale: 'Source reading is stale'
  };
  var counter = 0;

  // Validate bounded scalar types before interpreting owner data
  function isStr(v) { return typeof v === 'string' && v.length > 0; }
  function isInt(v) { return typeof v === 'number' && Number.isInteger(v) && v >= 0; }
  function ms(v) { return Date.parse(String(v).replace(/(\.\d{3})\d+/, '$1')); }
  function isIso(v) { return typeof v === 'string' && ISO.test(v) && isFinite(ms(v)); }
  function isPct(v) { return typeof v === 'number' && isFinite(v) && v >= 0 && v <= 100; }
  function isMin(v) { return typeof v === 'number' && isFinite(v) && v > 0; }
  function nullOr(v, t) { return v === null || t(v); }
  function pad(n) { return n < 10 ? '0' + n : String(n); }
  // Format source times explicitly in UTC
  function utc(v) {
    var d = new Date(ms(v));
    return d.getUTCFullYear() + '-' + pad(d.getUTCMonth() + 1) + '-' + pad(d.getUTCDate()) + ' ' +
      pad(d.getUTCHours()) + ':' + pad(d.getUTCMinutes()) + ':' + pad(d.getUTCSeconds()) + ' UTC';
  }
  function pctText(v) { return v === null ? 'Not reported' : (Math.round(v * 10) / 10) + '%'; }
  // Describe the source-declared allowance window
  function duration(m) {
    if (m === null) return 'Not reported';
    if (m % 1440 === 0) return (m / 1440) + (m === 1440 ? ' day' : ' days');
    if (m % 60 === 0) return (m / 60) + (m === 60 ? ' hour' : ' hours');
    return m + ' minutes';
  }
  function clip(s, n) { return s.length > n ? s.slice(0, n - 1) + '…' : s; }
  function mapped(table, v) { return table[v] || ('Reported state: ' + v); }
  // Insert owner labels as text rather than HTML
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  // Create chart nodes without accepting markup from the response
  function svgEl(tag, attrs) {
    var n = document.createElementNS(SVG_NS, tag);
    Object.keys(attrs || {}).forEach(function (k) { n.setAttribute(k, String(attrs[k])); });
    return n;
  }
  function setText(n, t) { if (n.textContent !== t) n.textContent = t; }
  function show(n, on) { if (n.hidden === on) n.hidden = !on; }
  function keyOf(s) { return s.revision + '|' + s.state + '|' + s.source_state; }
  // Missing history counts cannot stand in for an empty database
  function summaryOk(s) {
    return !!s && s.schema === 'monitoring.quota-history-status.v1' && isInt(s.revision) &&
      isInt(s.observations) && isInt(s.series_count) && typeof s.state === 'string' &&
      typeof s.source_state === 'string';
  }

  // Refuse another owner, scope or page before replacing retained history
  function validateFrame(r, req) {
    function fail(m) { var e = new Error('Unsupported quota history response: ' + m); e.shape = true; throw e; }
    if (!r || typeof r !== 'object' || r.schema !== 'monitoring.quota-history.v1') fail('schema');
    if (!isStr(r.identity) || r.identity !== req.identity) fail('owner identity');
    if (!isInt(r.revision) || (req.revision !== null && r.revision !== req.revision)) fail('revision');
    if (!isIso(r.observed_at) || r.scope !== 'explicit-recorded-quota-observations' || r.complete_coverage !== false) fail('scope');
    if (!Array.isArray(r.series) || r.series.length > MAX_SERIES) fail('series list');
    var byKey = {};
    r.series.forEach(function (s) {
      if (!s || typeof s !== 'object' || !HEX24.test(s.key) || byKey[s.key]) fail('series key');
      ['source', 'provider', 'account', 'provenance', 'id', 'label'].forEach(function (k) {
        if (typeof s[k] !== 'string') fail('series ' + k);
      });
      if (!nullOr(s.window_minutes, isMin) || !isInt(s.observations) || s.observations < 1 ||
          !isIso(s.first_captured_at) || !isIso(s.last_captured_at)) fail('series values');
      byKey[s.key] = s;
    });
    if (!Array.isArray(r.points) || r.points.length > MAX_POINTS) fail('points');
    if (r.selected === null) { if (r.series.length || r.points.length) fail('selection'); }
    else if (!byKey[r.selected]) fail('selection');
    if (req.series !== null && r.selected !== req.series) fail('selected window');
    var last = -Infinity;
    r.points.forEach(function (p) {
      if (!p || !isIso(p.captured_at) || !isIso(p.recorded_at) || !nullOr(p.used_percent, isPct) ||
          !nullOr(p.remaining_percent, isPct) || !nullOr(p.resets_at, isIso) || !isInt(p.segment) ||
          !Array.isArray(p.break_reasons) || !p.break_reasons.every(isStr) || typeof p.future !== 'boolean') fail('point');
      if ((p.used_percent === null) !== (p.remaining_percent === null) ||
          (p.used_percent !== null && Math.abs(p.used_percent + p.remaining_percent - 100) > 0.01)) fail('percentages');
      if (ms(p.captured_at) <= last || p.future !== (ms(p.captured_at) > ms(r.observed_at))) fail('point order or future source time');
      last = ms(p.captured_at);
    });
    if (!nullOr(r.before, isIso) || !nullOr(r.next_before, isIso)) fail('paging');
    if (req.before ? (r.before === null || ms(r.before) !== ms(req.before)) : r.before !== null) fail('page pin');
    var rs = r.recording_status;
    if (!rs || typeof rs.state !== 'string' || !nullOr(rs.error, function (v) { return typeof v === 'string'; }) ||
        !nullOr(rs.last_saved_at, isIso)) fail('recording status');
    if (typeof r.source_read_state !== 'string' || typeof r.synthetic_selected !== 'boolean') fail('state');
    var c = r.current_window;
    if (c !== null) {
      var s = byKey[r.selected];
      if (!c || typeof c !== 'object' || !s) fail('current window');
      ['source', 'provider', 'account', 'provenance', 'id'].forEach(function (k) { if (c[k] !== s[k]) fail('current window scope'); });
      if (c.window_minutes !== s.window_minutes || typeof c.label !== 'string' || !isIso(c.captured_at) ||
          !nullOr(c.used_percent, isPct) || !nullOr(c.remaining_percent, isPct) || !nullOr(c.resets_at, isIso) ||
          (c.used_percent === null) !== (c.remaining_percent === null) ||
          (c.used_percent !== null && Math.abs(c.used_percent + c.remaining_percent - 100) > 0.01) ||
          !FRESHNESS[c.freshness] || typeof c.age_seconds !== 'number' || !isFinite(c.age_seconds)) fail('current window values');
    }
    return r;
  }

  // Own one panel and abort its reads when it leaves the active view
  function mount(root, opts) {
    if (!root || !opts || typeof opts.getContext !== 'function' || typeof opts.requestJSON !== 'function') {
      throw new Error('MonitoringQuotaHistory.mount needs a root element, getContext and requestJSON');
    }
    var id = 'qh' + (++counter);
    var st = { ctx: null, identity: null, frame: null, seriesIndex: [], series: null, mode: 'latest',
      loadedOnce: false, attempted: false, busy: false, error: null, unsupported: false, conflict: false,
      seenKey: null, gen: 0, ctl: null, timer: null, drawn: null, optionSig: '', destroyed: false };
    var ui = build();

    // Keep observations in Usage and costs while configuration remains in Settings
    function build() {
      var u = {};
      root.classList.add('quota-history-view');
      var h = el('h2', null, 'Quota history'); h.id = id + '-h';
      root.setAttribute('role', 'region'); root.setAttribute('aria-labelledby', h.id);
      u.synthetic = el('p', 'qh-banner', 'Synthetic example data. These observations do not come from a real account.');
      u.status = el('p', 'qh-status'); u.status.setAttribute('role', 'status');
      u.notice = el('p', 'qh-notice');
      u.message = el('p', 'qh-message');
      u.controls = el('div', 'qh-controls');
      var field = el('div', 'qh-field');
      var lab = el('label', null, 'Quota window'); lab.htmlFor = id + '-sel';
      u.select = el('select'); u.select.id = id + '-sel';
      field.append(lab, u.select);
      u.refresh = el('button', null, 'Refresh history');
      u.latest = el('button', null, 'Latest observations');
      u.older = el('button', null, 'Older observations');
      [u.refresh, u.latest, u.older].forEach(function (b) { b.type = 'button'; });
      var btns = el('div', 'qh-buttons'); btns.append(u.refresh, u.latest, u.older);
      u.controls.append(field, btns);
      u.body = el('div', 'qh-body');
      u.scope = el('dl', 'qh-scope');
      u.aliasNote = el('p', 'qh-note', 'Account aliases are labels declared by the source configuration. They do not verify an identity.');
      var curH = el('h3', null, 'Current reading');
      u.current = el('p', 'qh-current');
      var chartH = el('h3', null, 'Percent used over time (UTC)'); chartH.id = id + '-ch';
      u.chartDesc = el('p', 'qh-note'); u.chartDesc.id = id + '-cd';
      u.svg = svgEl('svg', { viewBox: '0 0 640 260', role: 'img', 'aria-labelledby': chartH.id,
        'aria-describedby': id + '-cd ' + id + '-ax', 'class': 'qh-chart', focusable: 'false' });
      var axis = el('p', 'qh-note', 'Vertical axis: percent used, fixed from 0 to 100. Horizontal axis: source capture time in UTC, spaced by elapsed time. Lines break at gaps, changes and unreported values. The table below lists the same data.');
      axis.id = id + '-ax';
      u.breaks = el('ul', 'qh-breaks');
      u.page = el('p', 'qh-page');
      var wrap = el('div', 'qh-table-wrap');
      u.table = el('table');
      u.table.append(el('caption', null, 'Latest ' + TABLE_ROWS + ' observations on this page, newest first'));
      var tr = el('tr');
      ['Captured (UTC)', 'Recorded (UTC)', 'Used', 'Remaining', 'Reset time (UTC)', 'Notes'].forEach(function (t) {
        var th = el('th', null, t); th.scope = 'col'; tr.append(th);
      });
      var thead = el('thead'); thead.append(tr);
      u.tbody = el('tbody'); u.table.append(thead, u.tbody); wrap.append(u.table);
      u.body.append(u.scope, u.aliasNote, curH, u.current, chartH, u.svg, u.chartDesc, axis, u.breaks, u.page, wrap);
      u.recording = el('p', 'qh-recording');
      root.textContent = '';
      root.append(h, u.synthetic, u.status, u.controls, u.notice, u.message, u.body, u.recording);
      u.refresh.addEventListener('click', onRefresh);
      u.latest.addEventListener('click', onLatest);
      u.older.addEventListener('click', onOlder);
      u.select.addEventListener('change', onSelect);
      return u;
    }

    function enabled(b) { return b.getAttribute('aria-disabled') !== 'true'; }
    function onRefresh() { if (enabled(ui.refresh)) request('refresh'); }
    function onLatest() { if (enabled(ui.latest)) request('latest'); }
    function onOlder() { if (enabled(ui.older)) request('older'); }
    // Selection changes clear the prior scope before reading another window
    function onSelect() {
      var v = ui.select.value;
      if (!HEX24.test(v) || v === st.series || !canRead()) { ui.select.value = st.series || ''; return; }
      st.series = v; st.frame = null; st.mode = 'latest'; st.error = null;
      request('latest');
    }

    // Only an active connected view may ask the existing owner for history
    function canRead() {
      var c = st.ctx;
      return !!c && c.active && !c.paused && !c.unavailable && isStr(c.identity) && summaryOk(c.summary) && !st.unsupported;
    }
    // Retain older pages without silently changing their revision
    function isStale() {
      var f = st.frame, s = st.ctx && st.ctx.summary;
      return st.mode === 'older' && !!f && (st.conflict || (summaryOk(s) && s.revision !== f.revision));
    }
    // Generation pins also discard replies whose transport ignored cancellation
    function abort() {
      st.gen++;
      if (st.ctl) { try { st.ctl.abort(); } catch (e) { /* ignore */ } }
      if (st.timer) clearTimeout(st.timer);
      st.ctl = null; st.timer = null; st.busy = false;
    }
    // An owner change invalidates all earlier observations and selection pins
    function resetData() {
      abort();
      st.frame = null; st.seriesIndex = []; st.series = null; st.mode = 'latest'; st.loadedOnce = false;
      st.attempted = false; st.error = null; st.unsupported = false; st.conflict = false; st.seenKey = null;
    }

    // Older pages pin both the selected window and its original history revision
    function request(kind) {
      if (st.destroyed || !canRead()) return;
      var ctx = st.ctx, f = st.frame, series = st.series, before = null, rev = null;
      if (kind === 'older') {
        if (!f || !f.next_before || isStale()) return;
        series = f.selected; before = f.next_before; rev = f.revision;
      } else if (kind === 'refresh' && st.mode === 'older' && f && f.before && !isStale()) {
        series = f.selected; before = f.before; rev = f.revision;
      }
      if (series !== null && !HEX24.test(series)) return;
      var q = new URLSearchParams();
      if (series) q.set('series', series);
      q.set('limit', String(LIMIT));
      if (before) { q.set('before', before); q.set('expected_revision', String(rev)); }
      q.set('expected_identity', ctx.identity);
      abort();
      var gen = ++st.gen, ctl = new AbortController(), timedOut = false;
      var req = { identity: ctx.identity, series: series, before: before, revision: rev };
      st.ctl = ctl; st.busy = true; st.attempted = true; st.seenKey = keyOf(ctx.summary);
      st.timer = setTimeout(function () { timedOut = true; ctl.abort(); }, TIMEOUT_MS);
      render();
      var call;
      try { call = Promise.resolve(opts.requestJSON(ENDPOINT + '?' + q.toString(), { signal: ctl.signal })); }
      catch (e) { call = Promise.reject(e); }
      call.then(function (r) {
        if (gen !== st.gen || st.destroyed) return;
        if (!st.ctx || st.ctx.identity !== req.identity) return;
        var frame = validateFrame(r, req);
        st.frame = frame; st.seriesIndex = frame.series; st.series = frame.selected;
        st.mode = req.before ? 'older' : 'latest';
        st.error = null; st.conflict = false; st.loadedOnce = true;
      }).catch(function (e) {
        if (gen !== st.gen || st.destroyed) return;
        st.error = describe(e || {}, timedOut, req);
      }).then(function () {
        if (gen !== st.gen || st.destroyed) return;
        if (st.timer) clearTimeout(st.timer);
        st.ctl = null; st.timer = null; st.busy = false;
        render();
      });
    }

    // Describe failures without converting them into missing or zero observations
    function describe(e, timedOut, req) {
      if (timedOut) return 'The history request timed out after ' + (TIMEOUT_MS / 1000) + ' seconds.';
      if (e.status === 409) {
        if (req.before) { st.conflict = true; return 'History changed since this older page was loaded. Choose Latest observations or Refresh history.'; }
        return 'The owner or the history changed during the read. Choose Refresh history.';
      }
      if (e.status === 503) return 'Quota history is temporarily unavailable from the owner.';
      if (e.shape || ((e.status === 404 || e.status === 405 || e.status === 501) && !st.frame)) {
        if (!st.frame) st.unsupported = true;
        return 'The owner returned a quota history response this view does not support.';
      }
      return 'Quota history could not be read.';
    }

    // A chart tail never becomes the current source reading
    function currentText(f) {
      var c = f.current_window, ctx = st.ctx, prefix = 'Current reading is not known: ';
      if (ctx.paused) return prefix + 'updates are paused.';
      if (ctx.unavailable) return prefix + 'the owner is unavailable.';
      if (st.error) return prefix + 'the last history read failed.';
      if (ctx.summary.source_state !== 'following' || f.source_read_state !== 'following') return prefix + 'the current-window source is not being followed.';
      if (!c) return prefix + 'no current reading matches this quota window.';
      if (c.freshness === 'stale') return prefix + 'the latest source reading is stale.';
      if (c.freshness === 'future') return prefix + 'the source reading has a time later than the read time.';
      if (c.freshness === 'reset-passed') return prefix + 'its reported reset time has passed. This does not show whether quota was replenished.';
      if ((Date.now() - ms(c.captured_at)) / 1000 < 0 || (Date.now() - ms(c.captured_at)) / 1000 > MAX_CURRENT_AGE || c.age_seconds < 0 || c.age_seconds > MAX_CURRENT_AGE || ms(c.captured_at) > ms(f.observed_at)) return prefix + 'the source reading is older than 5 minutes or has an invalid age.';
      if (c.resets_at !== null && ms(c.resets_at) <= Date.now()) return prefix + 'its reported reset time has passed. This does not show whether quota was replenished.';
      if (c.used_percent === null) return prefix + 'the source did not report a percentage.';
      var t = pctText(c.used_percent) + ' used, ' + pctText(c.remaining_percent) + ' remaining, captured ' +
        utc(c.captured_at) + ' (' + Math.round(c.age_seconds) + ' seconds before the read).';
      if (c.resets_at !== null) t += ' Reported reset time: ' + utc(c.resets_at) + '.';
      t += ' This reading comes from the current source, not from the history page.';
      if (st.mode === 'older') t += ' It is newer than the older page shown below.';
      return t;
    }

    // Display the exact source and account-alias scope next to its chart
    function renderScope(s) {
      ui.scope.textContent = '';
      [['Provider', s.provider], ['Source', s.source], ['Account alias', s.account],
        ['Window', s.label + ' (' + s.id + ')'], ['Window duration', duration(s.window_minutes)],
        ['Provenance', s.provenance], ['Observations recorded', String(s.observations)],
        ['First captured', utc(s.first_captured_at)], ['Last captured', utc(s.last_captured_at)]
      ].forEach(function (row) {
        var d = el('div'); d.append(el('dt', null, row[0]), el('dd', null, row[1] || 'Not reported'));
        ui.scope.append(d);
      });
    }

    // Keep elapsed time on the x axis and break lines at absent or discontinuous values
    function drawChart(points) {
      var W = 640, H = 260, L = 48, R = 16, T = 12, B = 34, svg = ui.svg;
      while (svg.firstChild) svg.removeChild(svg.firstChild);
      var y = function (v) { return T + (100 - v) / 100 * (H - T - B); };
      [0, 25, 50, 75, 100].forEach(function (v) {
        svg.append(svgEl('line', { x1: L, x2: W - R, y1: y(v), y2: y(v), 'class': 'qh-grid' }));
        var t = svgEl('text', { x: L - 6, y: y(v) + 5, 'text-anchor': 'end', 'class': 'qh-tick' });
        t.textContent = v + '%'; svg.append(t);
      });
      var plot = points.filter(function (p) { return !p.future && p.used_percent !== null; });
      if (!plot.length) return { plotted: 0, runs: 0 };
      var t0 = ms(plot[0].captured_at), t1 = ms(plot[plot.length - 1].captured_at), span = t1 - t0;
      var x = function (p) { return span ? L + (ms(p.captured_at) - t0) / span * (W - L - R) : L + (W - L - R) / 2; };
      var runs = [], cur = null, seg = null;
      points.forEach(function (p) {
        if (p.future || p.used_percent === null) { cur = null; return; }
        if (!cur || p.segment !== seg) { cur = []; runs.push(cur); }
        cur.push(p); seg = p.segment;
      });
      runs.forEach(function (run) {
        if (run.length > 1) {
          svg.append(svgEl('polyline', { 'class': 'qh-line', points: run.map(function (p) {
            return x(p).toFixed(1) + ',' + y(p.used_percent).toFixed(1); }).join(' ') }));
        }
        run.forEach(function (p) {
          svg.append(svgEl('circle', { cx: x(p).toFixed(1), cy: y(p.used_percent).toFixed(1), r: run.length > 1 ? 3 : 4.5, 'class': 'qh-dot' }));
        });
      });
      var a = svgEl('text', { x: L, y: H - 8, 'text-anchor': span ? 'start' : 'middle', 'class': 'qh-tick' });
      if (!span) a.setAttribute('x', L + (W - L - R) / 2);
      a.textContent = utc(plot[0].captured_at); svg.append(a);
      if (span) {
        var b = svgEl('text', { x: W - R, y: H - 8, 'text-anchor': 'end', 'class': 'qh-tick' });
        b.textContent = utc(plot[plot.length - 1].captured_at); svg.append(b);
      }
      return { plotted: plot.length, runs: runs.length };
    }

    // Pair the chart with dated rows and explicit missing-value notes
    function drawFrame(f) {
      var pts = f.points, drawn = drawChart(pts), counts = {};
      var nulls = pts.filter(function (p) { return p.used_percent === null; }).length;
      var future = pts.filter(function (p) { return p.future; }).length;
      setText(ui.chartDesc, pts.length ? drawn.plotted + ' of ' + pts.length + ' observations plotted in ' + drawn.runs +
        ' separate line section(s). ' + nulls + ' without a reported percentage and ' + future +
        ' with a future source time are not plotted. Missing values are not shown as 0%.' : 'No observations on this page.');
      ui.breaks.textContent = '';
      pts.forEach(function (p) { p.break_reasons.forEach(function (r) { counts[r] = (counts[r] || 0) + 1; }); });
      Object.keys(counts).forEach(function (r) {
        ui.breaks.append(el('li', null, (REASONS[r] || 'Other break reported by the owner') + ': ' + counts[r]));
      });
      if (pts.some(function (p) { return p.resets_at !== null && ms(p.resets_at) <= ms(f.observed_at); })) {
        ui.breaks.append(el('li', null, 'Some reported reset times have passed. A passed reset time does not show that quota was replenished.'));
      }
      ui.tbody.textContent = '';
      pts.slice(-TABLE_ROWS).reverse().forEach(function (p) {
        var notes = p.break_reasons.map(function (r) { return REASONS[r] || 'Other break reported by the owner'; });
        if (p.future && p.break_reasons.indexOf('future-source-time') < 0) notes.push('Future source time; not plotted');
        var tr = el('tr');
        [utc(p.captured_at), utc(p.recorded_at), pctText(p.used_percent), pctText(p.remaining_percent),
          p.resets_at ? utc(p.resets_at) : 'Not reported', notes.join('; ') || '—'
        ].forEach(function (t) { tr.append(el('td', null, t)); });
        ui.tbody.append(tr);
      });
      var page = pts.length ? 'This page has ' + pts.length + ' observations captured from ' + utc(pts[0].captured_at) +
        ' to ' + utc(pts[pts.length - 1].captured_at) + '. ' : '';
      if (f.before) page += 'Older page: observations captured before ' + utc(f.before) + ', pinned to history revision ' + f.revision + '. ';
      page += f.next_before ? 'Older observations exist; this page is limited to ' + LIMIT + '. ' : 'No older observations exist for this window. ';
      page += 'Coverage is incomplete: only observations recorded while collection ran are included. Periods without observations are gaps, not zero use.';
      setText(ui.page, page);
    }

    function setButton(b, on) { var v = on ? 'false' : 'true'; if (b.getAttribute('aria-disabled') !== v) b.setAttribute('aria-disabled', v); }

    // Status changes retain dated history while qualifying the current reading
    function render() {
      if (st.destroyed) return;
      var ctx = st.ctx, sum = ctx && ctx.summary;
      show(root, !!sum);
      if (!sum) return;
      root.setAttribute('aria-busy', st.busy ? 'true' : 'false');
      var f = st.frame, bad = !summaryOk(sum) || st.unsupported;
      var empty = !bad && ((f && f.series.length === 0) || (!f && !st.busy && sum.observations === 0));
      show(ui.synthetic, !!(f && f.synthetic_selected));
      var status;
      if (st.busy) status = 'Loading quota history…';
      else if (st.error) status = st.error + (f ? ' Points shown are from the read at ' + utc(f.observed_at) + '.' : '');
      else if (ctx.paused) status = 'Updates are paused.' + (f ? ' Points shown are from the read at ' + utc(f.observed_at) + '.' : '');
      else if (f) status = 'History revision ' + f.revision + ', read at ' + utc(f.observed_at) + '.';
      else status = '';
      setText(ui.status, status);
      var msg = bad ? 'Quota history is unavailable from this owner. This does not mean no observations exist.'
        : empty ? 'No quota observations were recorded in this history yet.'
        : ctx.unavailable ? 'The owner is unavailable. Points shown are dated and may be out of date.' : '';
      setText(ui.message, msg); show(ui.message, !!msg);
      var notice = '';
      if (f && isStale()) {
        notice = st.conflict ? 'History changed since this older page was loaded.'
          : 'History now has revision ' + sum.revision + '; this older page was loaded at revision ' + f.revision + '.';
        notice += ' The page shown stays unchanged. Choose Latest observations or Refresh history to load the newest page.';
      } else if (f && st.mode === 'latest' && !st.busy && sum.revision !== f.revision) {
        notice = 'History now has revision ' + sum.revision + '; this page shows revision ' + f.revision + '.';
      }
      setText(ui.notice, notice); show(ui.notice, !!notice);
      var sig = st.seriesIndex.map(function (s) { return s.key + s.label + s.account; }).join('|');
      if (sig !== st.optionSig) {
        st.optionSig = sig; ui.select.textContent = '';
        st.seriesIndex.forEach(function (s) {
          var o = el('option', null, clip(s.label + ' — ' + s.provider + ' / ' + s.account + ' (' + duration(s.window_minutes) + ')', 90));
          o.value = s.key; ui.select.append(o);
        });
      }
      if (st.series && ui.select.value !== st.series) ui.select.value = st.series;
      show(ui.controls, !bad);
      show(ui.select.parentElement, st.seriesIndex.length > 0);
      var read = canRead() && !st.busy;
      ui.select.disabled = !read && document.activeElement !== ui.select;
      setButton(ui.refresh, read);
      setButton(ui.latest, read && !!f && (st.mode === 'older' || isStale()));
      setButton(ui.older, read && !!f && !!f.next_before && !isStale());
      var series = f && f.series.filter(function (s) { return s.key === f.selected; })[0];
      show(ui.body, !bad && !empty && !!series);
      if (series && st.drawn !== f) { st.drawn = f; renderScope(series); drawFrame(f); }
      if (series) setText(ui.current, currentText(f));
      var rec = 'History recording: ';
      if (f) {
        rec += mapped(RECORDING, f.recording_status.state) + '.';
        if (f.recording_status.last_saved_at) rec += ' Last saved ' + utc(f.recording_status.last_saved_at) + '.';
        if (f.recording_status.error) rec += ' Owner message: ' + f.recording_status.error;
      } else rec += mapped(RECORDING, sum.state) + '.';
      if (summaryOk(sum)) {
        rec += ' Stored observations: ' + sum.observations + ' of ' + sum.capacity + '; windows: ' + sum.series_count +
          ' of ' + sum.series_capacity + '.' + (sum.read_only ? ' This history is open read-only.' : '') +
          ' Current-window source: ' + mapped(SOURCE, sum.source_state) + '.';
      }
      setText(ui.recording, rec); show(ui.recording, !bad);
    }

    // Snapshot polling drives updates; the component creates no polling timer
    function update() {
      if (st.destroyed) return;
      var ctx = null;
      try { ctx = opts.getContext(); } catch (e) { ctx = null; }
      st.ctx = ctx;
      var ident = ctx && ctx.summary ? (isStr(ctx.identity) ? ctx.identity : null) : undefined;
      if (ident !== st.identity) { resetData(); st.identity = ident; }
      if (!canRead() && st.busy) abort();
      if (canRead()) {
        if (!st.loadedOnce && !st.attempted && !st.busy) return request('latest');
        if (st.loadedOnce && !st.busy && keyOf(ctx.summary) !== st.seenKey && st.mode === 'latest') return request('latest');
      }
      render();
    }

    // Release this panel's requests and handlers without changing saved preferences
    function destroy() {
      if (st.destroyed) return;
      abort(); st.destroyed = true;
      ui.refresh.removeEventListener('click', onRefresh);
      ui.latest.removeEventListener('click', onLatest);
      ui.older.removeEventListener('click', onOlder);
      ui.select.removeEventListener('change', onSelect);
      root.textContent = '';
      root.classList.remove('quota-history-view');
      ['role', 'aria-labelledby', 'aria-busy'].forEach(function (a) { root.removeAttribute(a); });
      root.hidden = false;
    }

    update();
    return { update: update, destroy: destroy };
  }

  window.MonitoringQuotaHistory = { mount: mount };
})();
