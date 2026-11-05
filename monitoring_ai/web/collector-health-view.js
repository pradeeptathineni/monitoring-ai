// collector-health-view.js (ES module). Pure DOM: no network, timers, storage, settings or innerHTML.
const READ_SCHEMA = 'monitoring.collector-health-read.v1';
const HEALTH_SCHEMA = 'monitoring.collector-health.v1';
const SCOPE = 'explicit-collector-checkpoints';
const STATES = ['following', 'unavailable', 'stopped', 'unknown'];
const SOURCE_STATES = ['following', 'unavailable', 'unread'];
const FRESHNESS = ['current', 'stale', 'future', 'empty'];
const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$/;

const STATE_LABEL = { following: 'Following', unavailable: 'Unavailable', stopped: 'Stopped', unknown: 'Unknown' };
const SOURCE_LABEL = { following: 'Following', unavailable: 'Unavailable', unread: 'Unread' };

// Fixed reason codes only; anything else is dropped (never rendered).
const REASON_HELP = new Map([
  ['ok', 'The selected checkpoint reported no problem.'],
  ['collector-following', 'The collector reconciled its fixed selection. Coverage remains incomplete.'],
  ['not_configured', 'No checkpoint source is selected.'],
  ['no-checkpoints', 'No checkpoint has been recorded yet.'],
  ['collection-source-unavailable', 'The selected family was unavailable or could not be reconciled at that checkpoint.'],
  ['source_unread', 'The source had not been read at the last checkpoint.'],
  ['collector-stopped', 'The collector reported that it stopped.'],
  ['checkpoint-stale', 'The last checkpoint is older than the allowed age.'],
  ['checkpoint-in-future', 'The last checkpoint time is ahead of this clock.'],
  ['read_failed', 'The checkpoint read failed.'],
]);

let uid = 0;

const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
const nul = (v) => (v === undefined ? null : v);

function isoOrNull(v) {
  v = nul(v);
  if (v === null) return { ok: true, value: null };
  if (typeof v === 'string' && v.length <= 40 && ISO.test(v) && Number.isFinite(Date.parse(v))) {
    return { ok: true, value: v };
  }
  return { ok: false };
}

// Returns null when the envelope cannot be trusted; never throws.
function normalize(env) {
  if (!isObj(env) || env.schema !== READ_SCHEMA) return null;
  if (typeof env.identity !== 'string' || !env.identity || env.identity.length > 200) return null;
  const maxAge = env.max_age_seconds;
  if (!Number.isInteger(maxAge) || maxAge < 1 || maxAge > 3600) return null;
  if (env.read_status !== 'available' && env.read_status !== 'unavailable') return null;
  const out = { identity: env.identity, readStatus: env.read_status, maxAge, health: null };
  const h = nul(env.health);
  if (h === null) return out;
  if (!isObj(h) || h.schema !== HEALTH_SCHEMA || h.scope !== SCOPE || h.complete_coverage !== false) return null;
  if (!STATES.includes(h.state) || !FRESHNESS.includes(h.freshness)) return null;
  const src = nul(h.last_source_state);
  if (src !== null && !SOURCE_STATES.includes(src)) return null;
  if (typeof h.partial_tail !== 'boolean') return null;
  const count = nul(h.accepted_records);
  if (count !== null && !(Number.isSafeInteger(count) && count >= 0)) return null;
  const rs = nul(h.reported_state);
  if (rs !== null && typeof rs !== 'string') return null;
  const reason = nul(h.reason);
  if (reason !== null && typeof reason !== 'string') return null;
  const cp = isoOrNull(h.last_checkpoint_at);
  const ok = isoOrNull(h.last_success_at);
  const ev = isoOrNull(h.latest_event_at);
  if (!cp.ok || !ok.ok || !ev.ok) return null;
  out.health = {
    state: h.state,
    reportedState: rs !== null && STATES.includes(rs) ? rs : null, // unknown strings dropped
    source: src,
    checkpoint: cp.value,
    success: ok.value,
    event: ev.value,
    count,
    reason: reason !== null && REASON_HELP.has(reason) ? reason : null,
    partialTail: h.partial_tail,
  };
  return out;
}

// Freshness is always recomputed from the checkpoint time, never from read time.
function computeFreshness(checkpoint, now, maxAge) {
  if (checkpoint === null) return 'empty';
  const t = Date.parse(checkpoint);
  if (t > now) return 'future';
  if (now - t > maxAge * 1000) return 'stale';
  return 'current';
}

export function mountCollectorHealth(root) {
  const doc = root.ownerDocument || globalThis.document;
  const el = (tag, cls) => {
    const n = doc.createElement(tag);
    if (cls) n.className = cls;
    return n;
  };

  const id = `collector-health-${++uid}`;
  const section = el('section', 'collector-health-view');
  section.setAttribute('aria-labelledby', `${id}-h`);
  const heading = el('h3', 'collector-health-heading');
  heading.setAttribute('id', `${id}-h`);
  heading.textContent = 'Collector checkpoints';
  const scope = el('p', 'collector-health-scope');
  scope.textContent =
    'Checkpoint reports from the selected collector, separate from event-file read health. A readable output is not an upstream heartbeat or worker evidence. Event filters do not apply.';
  const status = el('div', 'collector-health-status');
  status.setAttribute('aria-live', 'polite');
  status.setAttribute('aria-atomic', 'true');
  const notes = el('ul', 'collector-health-notes');
  const dl = el('dl', 'collector-health-facts');
  // Native disclosure keeps internal owner identity out of the primary flow
  const details = el('details', 'collector-health-details');
  const summary = el('summary');
  summary.textContent = 'Collector evidence';
  details.append(summary, dl);
  const recovery = el('p', 'collector-health-recovery');

  const ROWS = [
    ['owner', 'Owner'],
    ['state', 'Collector state'],
    ['source', 'Last source state'],
    ['freshness', 'Checkpoint freshness'],
    ['checkpoint', 'Last checkpoint'],
    ['success', 'Last successful reconciliation'],
    ['event', 'Latest source event'],
    ['count', 'Accepted records'],
  ];
  const dds = {};
  for (const [key, label] of ROWS) {
    const row = el('div', 'collector-health-row');
    const dt = el('dt');
    dt.textContent = label;
    const dd = el('dd');
    dds[key] = dd;
    row.appendChild(dt);
    row.appendChild(dd);
    dl.appendChild(row);
  }
  section.appendChild(heading);
  section.appendChild(scope);
  section.appendChild(status);
  section.appendChild(notes);
  section.appendChild(recovery);
  section.appendChild(details);
  section.hidden = true;
  root.appendChild(section);

  let identity = null;
  let noteItems = [];
  let statusText = '';

  const setText = (node, text) => {
    if (node.textContent !== text) node.textContent = text;
  };

  function setNotes(list) {
    const same = list.length === noteItems.length && list.every((t, i) => t === noteItems[i].text);
    if (same) return;
    notes.textContent = '';
    noteItems = list.map((text) => {
      const li = el('li');
      li.textContent = text;
      notes.appendChild(li);
      return { text };
    });
  }

  function reset() {
    identity = null;
    details.open = false;
    statusText = '';
    status.textContent = '';
    notes.textContent = '';
    noteItems = [];
    for (const k of Object.keys(dds)) dds[k].textContent = '';
    dl.hidden = true;
    details.hidden = true;
    recovery.textContent = '';
  }

  function render(statusMsg, noteList, facts, tone) {
    section.hidden = false;
    if (statusMsg !== statusText) {
      statusText = statusMsg;
      status.textContent = statusMsg;
    }
    setNotes(noteList);
    if (!facts) {
      dl.hidden = true;
      details.hidden = true;
      return;
    }
    dl.hidden = false;
    details.hidden = false;
    for (const [key] of ROWS) setText(dds[key], facts[key]);
    dds.state.setAttribute('data-tone', tone);
  }

  function update(envelope, opts = {}) {
    const o = isObj(opts) ? opts : {};
    const paused = o.paused === true;
    const unavailable = o.unavailable === true;
    const now = Number.isFinite(o.now) ? o.now : Date.now();

    if (envelope === null || envelope === undefined) {
      reset();
      const n = ['No selected checkpoint evidence is available, so no values are shown.'];
      if (paused) n.push('Updates are paused.');
      if (unavailable) n.push('The checkpoint read is unavailable.');
      render('Collector checkpoints are not configured.', n, null, 'muted');
      return;
    }

    const env = normalize(envelope);
    if (!env) {
      reset();
      render('Checkpoint data could not be interpreted.', ['No values are shown.'], null, 'muted');
      return;
    }
    if (identity !== null && identity !== env.identity) reset();
    identity = env.identity;

    const h = env.health;
    const degraded = paused || unavailable || env.readStatus === 'unavailable';
    setText(recovery, 'Review the original selected-source refusal before resuming. Restore supported input or deliberately select another source, preview against retained history, then restart the finite collector explicitly. Do not remove history declarations to make an input pass.');
    const notesList = [];
    if (paused) notesList.push('Updates are paused. Dated facts are from the last supplied read; current collection state is unknown.');
    if (unavailable || env.readStatus === 'unavailable') {
      notesList.push('The checkpoint read is unavailable. Dated facts are from the last supplied read; current collection state is unknown.');
    }

    if (!h) {
      notesList.push('No checkpoint facts were supplied.');
      render(
        degraded ? 'Current collection state is unknown.' : 'No checkpoint facts were supplied.',
        notesList,
        { owner: env.identity, state: 'Unknown', source: 'Unknown', freshness: 'Unknown', checkpoint: 'Unknown', success: 'Unknown', event: 'Unknown', count: 'Unknown' },
        'muted',
      );
      return;
    }

    const fresh = computeFreshness(h.checkpoint, now, env.maxAge);
    const stateLabel = STATE_LABEL[h.state];
    let stateText;
    let tone;
    if (degraded || fresh !== 'current') {
      stateText = `Unknown now. Last supplied: ${stateLabel}`;
      tone = 'muted';
    } else if (h.state === 'following') {
      if (fresh === 'current') {
        stateText = stateLabel;
        tone = 'brand';
      } else {
        stateText = 'Not confirmed current. Last supplied: Following';
        tone = 'danger';
      }
    } else {
      stateText = stateLabel;
      tone = h.state === 'unknown' ? 'muted' : 'danger';
    }

    const freshText = {
      current: `Current (within ${env.maxAge} s)`,
      stale: `Stale (older than ${env.maxAge} s)`,
      future: 'Checkpoint time is ahead of this clock',
      empty: 'No checkpoint time supplied',
    }[fresh];

    if (h.reportedState && h.reportedState !== h.state) {
      notesList.push(`Reported state: ${STATE_LABEL[h.reportedState]}.`);
    }
    if (h.reason) notesList.push(REASON_HELP.get(h.reason));
    if (h.partialTail) notesList.push('The latest incomplete checkpoint waits.');

    const statusMsg = degraded || fresh !== 'current'
      ? 'Current collection state is unknown.'
      : `Collector ${stateLabel.toLowerCase()}. Checkpoint ${fresh === 'empty' ? 'missing' : fresh}.`;

    render(
      statusMsg,
      notesList,
      {
        owner: env.identity,
        state: stateText,
        source: h.source ? SOURCE_LABEL[h.source] : 'Unknown',
        freshness: freshText,
        checkpoint: h.checkpoint || 'Unknown',
        success: h.success || 'Unknown',
        event: h.event || 'Unknown',
        count: h.count === null ? 'Unknown' : String(h.count),
      },
      tone,
    );
  }

  function clear() {
    reset();
    section.hidden = true;
  }

  return { update, clear };
}
