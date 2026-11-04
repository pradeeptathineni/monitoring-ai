// Render selected caller receipts without IO, persistence or usage accounting
const STATUSES = ['queued', 'in_progress', 'completed', 'failed', 'cancelled', 'incomplete'];
const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'incomplete']);
const LABELS = {queued:'Queued', in_progress:'In progress', completed:'Completed', failed:'Failed', cancelled:'Cancelled', incomplete:'Incomplete'};
const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$/;
let sequence = 0;

// Admit only bounded metadata needed for presentation; never display unknown fields
function normalize(value) {
  const time = v => typeof v === 'string' && v.length <= 40 && ISO.test(v) && Number.isFinite(Date.parse(v));
  const alias = v => typeof v === 'string' && /^[A-Za-z0-9_.:@-]{1,160}$/.test(v);
  if (!value || value.schema !== 'monitoring.openai-request-read.v1' || !alias(value.identity)
      || !time(value.observed_at) || !Number.isInteger(value.max_age_seconds)
      || value.max_age_seconds < 1 || value.max_age_seconds > 3600
      || !['available', 'unavailable'].includes(value.read_status)) return null;
  if (value.book === null) return value;
  const b = value.book;
  if (!b || b.schema !== 'monitoring.openai-request-observations.v1'
      || b.scope !== 'explicit-caller-received-responses' || b.complete_coverage !== false
      || b.truncated !== false || !time(b.observed_at) || !Array.isArray(b.rows) || b.rows.length > 100) return null;
  const seen = new Set();
  for (const r of b.rows) {
    if (!r || r.schema !== 'monitoring.openai-request-observation.v1'
        || !/^response-[0-9a-f]{32}$/.test(r.id) || r.provider !== 'openai'
        || !['source', 'project', 'session', 'model'].every(k => alias(r[k]))
        || !STATUSES.includes(r.status) || r.reported_status !== r.status
        || !['current', 'stale', 'future'].includes(r.freshness)
        || !time(r.observed_at) || !(r.created_at === null || time(r.created_at))
        || typeof r.has_error !== 'boolean' || r.complete_coverage !== false) return null;
    const key = `${r.source}/${r.id}`;
    if (seen.has(key)) return null;
    seen.add(key);
  }
  return value;
}

// Keep the filter and scroll region mounted so refreshes preserve keyboard focus
export function mountOpenAIRequests(root) {
  const doc = root.ownerDocument;
  const el = (tag, text) => {
    const node = doc.createElement(tag);
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const id = `openai-requests-${++sequence}`;
  const section = el('section');
  section.className = 'openai-request-view';
  const heading = el('h3', 'API request observations');
  const scope = el('p', 'Status reports received by the selected caller. Receipt freshness is separate from reported status. These rows do not establish worker or session lifecycle, tokens, cost or complete account coverage. Event filters do not apply.');
  const notice = el('p');
  notice.setAttribute('role', 'status');
  const label = el('label', 'Reported request status');
  label.htmlFor = `${id}-filter`;
  const filter = el('select');
  filter.id = label.htmlFor;
  for (const s of ['', ...STATUSES]) {
    const option = el('option', s ? LABELS[s] : 'All reported statuses');
    option.value = s;
    filter.append(option);
  }
  const region = el('div');
  region.className = 'request-table-scroll';
  region.tabIndex = 0;
  region.setAttribute('role', 'region');
  region.setAttribute('aria-labelledby', `${id}-caption`);
  const table = el('table');
  const caption = el('caption', 'Latest explicit receipt per selected source and response identity');
  caption.id = `${id}-caption`;
  const thead = el('thead'), head = el('tr'), body = el('tbody');
  for (const name of ['Reported status', 'Receipt freshness', 'Received UTC', 'Provider created UTC', 'Model', 'Source / project / grouping alias', 'Has error object', 'Hashed response ID']) {
    const cell = el('th', name);
    cell.scope = 'col';
    head.append(cell);
  }
  thead.append(head);
  table.append(caption, thead, body);
  region.append(table);
  section.append(heading, scope, notice, label, filter, region);
  section.hidden = true;
  root.append(section);
  let identity = null, envelope = null, options = {};

  // Recompute receipt age even when the same dated book is supplied again
  function render() {
    section.hidden = false;
    body.replaceChildren();
    const b = envelope?.book;
    const degraded = options.paused || options.unavailable || envelope?.read_status === 'unavailable';
    const notes = [];
    if (options.paused) notes.push('Updates are paused. Current request state is unknown.');
    if (options.unavailable || envelope?.read_status === 'unavailable') notes.push('The request observation read is unavailable. Current request state is unknown.');
    if (!envelope) notes.push('No interpretable request observations are supplied.');
    else if (!b) notes.push('No request receipt facts are available.');
    else if (!b.rows.length) notes.push('None captured in the selected file. Account use is unknown.');
    else notes.push('Dated provider reports only. Error presence records an error object, without its body or diagnosis.');
    const message = notes.join(' ');
    if (notice.textContent !== message) notice.textContent = message;
    filter.hidden = label.hidden = !b;
    region.hidden = !b?.rows.length;
    if (!b) return;
    const now = Number.isFinite(options.now) ? options.now : Date.now();
    let matched = false;
    for (const r of b.rows) {
      if (filter.value && r.status !== filter.value) continue;
      matched = true;
      const age = now - Date.parse(r.observed_at);
      const freshness = age < 0 ? 'Future receipt' : age > envelope.max_age_seconds * 1000 ? 'Stale receipt' : 'Current receipt';
      const unknown = degraded || age < 0 || age > envelope.max_age_seconds * 1000;
      const status = `${LABELS[r.status]} (reported)${unknown && !TERMINAL.has(r.status) ? '; current state unknown' : ''}`;
      const tr = el('tr');
      for (const text of [status, freshness, r.observed_at, r.created_at ?? 'Unknown', r.model,
        `${r.source} / ${r.project} / ${r.session}`, r.has_error ? 'Yes' : 'No', r.id]) tr.append(el('td', text));
      body.append(tr);
    }
    if (!matched && b.rows.length) {
      const row = el('tr'), cell = el('td', 'No selected receipts match this status.');
      cell.colSpan = 8;
      row.append(cell);
      body.append(row);
    }
  }

  // Reset contextual selection when the owning server changes
  function update(value, opts = {}) {
    options = opts;
    const next = normalize(value);
    if (!next || identity !== next.identity) filter.value = '';
    identity = next?.identity ?? null;
    envelope = next;
    render();
  }

  // Remove evidence when the route is absent or its owner is replaced
  function clear() {
    envelope = null;
    identity = null;
    filter.value = '';
    body.replaceChildren();
    notice.textContent = '';
    section.hidden = true;
  }
  filter.addEventListener('change', render);
  return {update, clear};
}
