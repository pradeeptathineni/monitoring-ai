/* Monitoring AI: counterfactual cost-scenario report panel. Classic script, no dependencies. */
(function () {
  'use strict';

  // Contract constants shared with the root API frame.
  var ENDPOINT = '/api/v1/cost-scenario';
  var FRAME_SCHEMA = 'monitoring.cost-scenario.v1';
  var STATUS_SCHEMA = 'monitoring.cost-scenario-status.v1';
  var SCOPE = 'matching-accepted-usage-counterfactual';
  var TIMEOUT_MS = 8000;
  var MAX_GROUPS = 100;
  var MAX_RATES = 1000;
  var BUCKETS = ['input', 'cache_read', 'cache_write', 'output'];
  var BUCKET_LABELS = ['Input', 'Cache read', 'Cache write', 'Output'];
  var COUNT_KEYS = [
    'matched_usage_records', 'eligible_usage_records', 'excluded_future_records',
    'excluded_cumulative_records', 'baseline_estimated_records', 'baseline_billed_records',
    'baseline_unknown_records', 'baseline_unconverted_records', 'scenario_priced_records',
    'scenario_unpriced_records', 'scenario_unconverted_records', 'comparable_estimated_records'
  ];
  var HEX64 = /^[0-9a-f]{64}$/;
  var DECIMAL = /^-?\d{1,60}(?:\.\d{1,60})?$/;
  var TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;
  var TEN = BigInt(10);
  var ZERO = { units: BigInt(0), scale: 0 };

  // Exact decimal arithmetic: BigInt units with a scale, never floats.
  function parseDecimal(text) {
    if (typeof text !== 'string' || !DECIMAL.test(text)) return null;
    var negative = text.charAt(0) === '-';
    var parts = (negative ? text.slice(1) : text).split('.');
    var fraction = parts.length > 1 ? parts[1] : '';
    var digits = BigInt(parts[0] + fraction);
    return { units: negative ? -digits : digits, scale: fraction.length };
  }

  function rescale(value, scale) {
    var units = value.units;
    for (var i = value.scale; i < scale; i += 1) units *= TEN;
    return units;
  }

  function addDecimal(a, b) {
    var scale = Math.max(a.scale, b.scale);
    return { units: rescale(a, scale) + rescale(b, scale), scale: scale };
  }

  function negate(value) {
    return { units: -value.units, scale: value.scale };
  }

  function sameDecimal(a, b) {
    var scale = Math.max(a.scale, b.scale);
    return rescale(a, scale) === rescale(b, scale);
  }

  // Basic shape predicates.
  function isObject(value) {
    return value !== null && typeof value === 'object' && !Array.isArray(value);
  }

  function isCount(value) {
    return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
  }

  // Scope comparison: sorted non-empty filter pairs, lifecycle state excluded.
  function queryPairs(query) {
    var pairs = [];
    if (!isObject(query)) return pairs;
    Object.keys(query).sort().forEach(function (key) {
      var value = query[key];
      if (key === 'state' || value === null || value === undefined || value === '') return;
      pairs.push([key, String(value)]);
    });
    return pairs;
  }

  function sameQuery(a, b) {
    return JSON.stringify(queryPairs(a)) === JSON.stringify(queryPairs(b));
  }

  // Startup configuration status as supplied by root.
  function validConfig(config) {
    return isObject(config) && config.schema === STATUS_SCHEMA && HEX64.test(String(config.id)) &&
      typeof config.label === 'string' && config.label.length > 0 && config.label.length <= 160 &&
      isCount(config.rate_count) && config.rate_count <= MAX_RATES && config.kind === 'counterfactual';
  }

  function scopeKey(ctx) {
    return JSON.stringify([
      String(ctx.identity), String(ctx.currency), queryPairs(ctx.query),
      ctx.config ? [ctx.config.id, ctx.config.label, ctx.config.rate_count] : null
    ]);
  }

  // Coverage rule: exclusions reconcile and each cost subset partitions eligible records.
  function checkCoverage(cov, usage) {
    if (!isObject(cov)) return 'coverage block missing';
    for (var i = 0; i < COUNT_KEYS.length; i += 1) {
      if (!isCount(cov[COUNT_KEYS[i]])) return 'coverage count ' + COUNT_KEYS[i] + ' invalid';
    }
    var eligible = cov.eligible_usage_records;
    if (cov.matched_usage_records !== eligible + cov.excluded_future_records + cov.excluded_cumulative_records) {
      return 'matched records do not reconcile with exclusions';
    }
    if (cov.baseline_estimated_records + cov.baseline_billed_records + cov.baseline_unknown_records +
        cov.baseline_unconverted_records !== eligible) {
      return 'baseline subsets do not partition eligible records';
    }
    if (cov.scenario_priced_records + cov.scenario_unpriced_records + cov.scenario_unconverted_records !== eligible) {
      return 'alternative subsets do not partition eligible records';
    }
    if (cov.comparable_estimated_records > Math.min(cov.baseline_estimated_records, cov.scenario_priced_records)) {
      return 'comparable records exceed their subsets';
    }
    if (usage !== null && (cov.excluded_future_records !== 0 || cov.excluded_cumulative_records !== 0 ||
        cov.matched_usage_records !== usage || eligible !== usage)) {
      return 'group coverage disagrees with its usage records';
    }
    return '';
  }

  // Subset rule: an amount exists exactly when its record count is above zero.
  function checkSubset(subset, records, name) {
    if (!isObject(subset) || subset.records !== records) return name + ' record count disagrees with coverage';
    if (records === 0) return subset.amount === null ? '' : name + ' has an amount without records';
    var amount = parseDecimal(subset.amount);
    return amount && amount.units >= BigInt(0) ? '' : name + ' amount is not a nonnegative decimal string';
  }

  // Amount rule: subsets match coverage and difference equals alternative minus baseline.
  function checkAmounts(part, cov, name) {
    if (!isObject(part.baseline)) return name + ' baseline block missing';
    var problem = checkSubset(part.baseline.estimated, cov.baseline_estimated_records, name + ' baseline estimated') ||
      checkSubset(part.baseline.billed, cov.baseline_billed_records, name + ' baseline billed') ||
      checkSubset(part.alternative, cov.scenario_priced_records, name + ' alternative');
    if (problem) return problem;
    var comparable = part.comparable;
    if (!isObject(comparable) || comparable.records !== cov.comparable_estimated_records) {
      return name + ' comparable count disagrees with coverage';
    }
    if (comparable.records === 0) {
      return comparable.baseline_estimated_amount === null && comparable.alternative_amount === null &&
        comparable.difference === null ? '' : name + ' comparable amounts present without records';
    }
    var base = parseDecimal(comparable.baseline_estimated_amount);
    var alt = parseDecimal(comparable.alternative_amount);
    var diff = parseDecimal(comparable.difference);
    if (!base || !alt || !diff || base.units < BigInt(0) || alt.units < BigInt(0)) return name + ' comparable amounts malformed';
    if (!sameDecimal(addDecimal(alt, negate(base)), diff)) return name + ' difference is not alternative minus baseline';
    return '';
  }

  var AMOUNT_PATHS = [
    function (p) { return p.baseline.estimated.amount; },
    function (p) { return p.baseline.billed.amount; },
    function (p) { return p.alternative.amount; },
    function (p) { return p.comparable.baseline_estimated_amount; },
    function (p) { return p.comparable.alternative_amount; }
  ];

  // Group rule: bounded rows, unique provider/model, totals reconcile when complete.
  function checkGroups(frame) {
    var groups = frame.groups;
    if (!Array.isArray(groups) || groups.length > MAX_GROUPS) return 'group list missing or above 100 rows';
    if (!isCount(frame.groups_total) || frame.groups_total < groups.length) return 'groups_total invalid';
    if (frame.groups_truncated !== (frame.groups_total > groups.length)) return 'truncation flag disagrees with group counts';
    if (frame.groups_truncated && groups.length !== MAX_GROUPS) return 'truncated list is not 100 rows';
    var seen = {};
    var counts = {};
    var sums = AMOUNT_PATHS.map(function () { return ZERO; });
    COUNT_KEYS.forEach(function (key) { counts[key] = 0; });
    for (var i = 0; i < groups.length; i += 1) {
      var group = groups[i];
      if (!isObject(group) || typeof group.provider !== 'string' || typeof group.model !== 'string') {
        return 'group identity malformed';
      }
      var key = JSON.stringify([group.provider, group.model]);
      if (seen[key]) return 'duplicate provider/model group';
      seen[key] = true;
      if (!isCount(group.usage_records) || group.usage_records === 0 || !isObject(group.tokens)) {
        return 'group usage malformed';
      }
      for (var b = 0; b < BUCKETS.length; b += 1) {
        if (!isCount(group.tokens[BUCKETS[b]])) return 'group token count malformed';
      }
      var problem = checkCoverage(group.coverage, group.usage_records) ||
        checkAmounts(group, group.coverage, 'group ' + group.provider + '/' + group.model);
      if (problem) return problem;
      COUNT_KEYS.forEach(function (name) { counts[name] += group.coverage[name]; });
      for (var a = 0; a < AMOUNT_PATHS.length; a += 1) {
        var amount = AMOUNT_PATHS[a](group);
        if (amount !== null) sums[a] = addDecimal(sums[a], parseDecimal(amount));
      }
    }
    var total = frame.coverage;
    var countKeys = COUNT_KEYS.filter(function (name) {
      return name !== 'matched_usage_records' && name.indexOf('excluded_') !== 0;
    });
    for (var c = 0; c < countKeys.length; c += 1) {
      var sum = counts[countKeys[c]];
      var expected = total[countKeys[c]];
      if (frame.groups_truncated ? sum > expected : sum !== expected) return 'group counts do not reconcile with totals';
    }
    if (!frame.groups_truncated) {
      for (var t = 0; t < AMOUNT_PATHS.length; t += 1) {
        var totalAmount = AMOUNT_PATHS[t](frame);
        if (totalAmount !== null && !sameDecimal(sums[t], parseDecimal(totalAmount))) {
          return 'group amounts do not reconcile with totals';
        }
      }
    }
    return '';
  }

  // Frame validation against the scope pinned when the read started.
  function validateFrame(frame, expect) {
    if (!isObject(frame)) return 'response is not an object';
    if (frame.schema !== FRAME_SCHEMA) return 'unexpected schema';
    if (frame.identity !== expect.identity) return 'owner identity differs';
    if (frame.revision !== expect.revision) return 'ledger revision differs';
    if (frame.currency !== expect.currency) return 'display currency differs';
    if (!isObject(frame.filters) || Object.prototype.hasOwnProperty.call(frame.filters, 'state') || !sameQuery(frame.filters, expect.query)) return 'filters differ from the requested scope';
    var scenario = frame.scenario;
    if (!isObject(scenario) || scenario.id !== expect.config.id || scenario.label !== expect.config.label ||
        scenario.rate_count !== expect.config.rate_count || scenario.kind !== 'counterfactual') {
      return 'alternative rate book differs from the startup configuration';
    }
    if (!Array.isArray(scenario.rates) || scenario.rates.length !== scenario.rate_count) return 'alternative rates are missing';
    for (var r = 0; r < scenario.rates.length; r += 1) {
      var rate = scenario.rates[r];
      if (!isObject(rate) || ['provider', 'model', 'currency', 'provenance', 'from', 'until'].some(function (key) { return typeof rate[key] !== 'string'; })) return 'alternative rate metadata is malformed';
      if (!TIMESTAMP.test(rate.from) || !TIMESTAMP.test(rate.until) || rate.until <= rate.from) return 'alternative rate dates are malformed';
      if (BUCKETS.some(function (key) { var value = parseDecimal(rate[key]); return !value || value.units < BigInt(0); })) return 'alternative rate amount is malformed';
    }
    if (frame.scope !== SCOPE || frame.complete_coverage !== false) return 'scope flags are not counterfactual';
    if (typeof frame.observed_at !== 'string' || !TIMESTAMP.test(frame.observed_at)) return 'observed_at is not a UTC timestamp';
    if (typeof frame.contains_synthetic_records !== 'boolean' || typeof frame.groups_truncated !== 'boolean') {
      return 'boolean flags malformed';
    }
    if (frame.fx !== null && !isObject(frame.fx)) return 'fx book malformed';
    return checkCoverage(frame.coverage, null) || checkAmounts(frame, frame.coverage, 'total') || checkGroups(frame);
  }

  // Text formatting: decimal strings are shown verbatim; null is Unknown, never zero.
  function count(value) {
    return Number(value).toLocaleString('en-US');
  }

  function plural(value, word) {
    return count(value) + ' ' + word + (value === 1 ? '' : 's');
  }

  function money(amount, currency) {
    return amount === null ? 'Unknown' : amount + ' ' + currency;
  }

  function signedMoney(amount, currency) {
    if (amount === null) return 'Unknown';
    var parsed = parseDecimal(amount);
    var sign = parsed && parsed.units > BigInt(0) ? '+' : '';
    return sign + amount + ' ' + currency;
  }

  // DOM helpers: textContent only, no response HTML.
  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function metric(list, term, value, detail) {
    var item = el('div', 'cost-scenario-view__metric');
    item.appendChild(el('dt', '', term));
    var description = el('dd');
    description.appendChild(el('span', 'cost-scenario-view__amount', value));
    if (detail) description.appendChild(el('span', 'cost-scenario-view__detail', detail));
    item.appendChild(description);
    list.appendChild(item);
  }

  function message(list, tone, text) {
    list.appendChild(el('li', 'cost-scenario-view__message is-' + tone, text));
  }

  // Coverage messages: explain every record that is not in a known amount.
  function coverageMessages(list, frame) {
    var cov = frame.coverage;
    if (frame.groups_truncated) {
      message(list, 'warn', 'Showing ' + count(frame.groups.length) + ' of ' + count(frame.groups_total) +
        ' provider/model groups. Totals and coverage include all ' + count(frame.groups_total) + ' groups.');
    }
    if (cov.excluded_future_records > 0) {
      message(list, 'warn', plural(cov.excluded_future_records, 'record') + ' dated after ' + frame.observed_at + ' excluded.');
    }
    if (cov.excluded_cumulative_records > 0) {
      message(list, 'warn', plural(cov.excluded_cumulative_records, 'cumulative CLI receipt') +
        ' (claude-code-result) excluded: receipt-time totals cannot be repriced by activity date.');
    }
    if (cov.baseline_unknown_records > 0) {
      message(list, 'warn', plural(cov.baseline_unknown_records, 'record') + ' have no recorded or rate-book baseline cost. They are not counted as zero.');
    }
    if (cov.baseline_unconverted_records > 0) {
      message(list, 'warn', plural(cov.baseline_unconverted_records, 'record') + ' have a baseline cost with no declared conversion to ' + frame.currency + '.');
    }
    if (cov.scenario_unpriced_records > 0) {
      message(list, 'warn', plural(cov.scenario_unpriced_records, 'record') + ' have no matching alternative rate for their provider, model and date.');
    }
    if (cov.scenario_unconverted_records > 0) {
      message(list, 'warn', plural(cov.scenario_unconverted_records, 'record') + ' have an alternative rate with no declared conversion to ' + frame.currency + '.');
    }
    if (frame.contains_synthetic_records) {
      message(list, 'warn', 'This scope contains synthetic records (source starting with "synthetic").');
    }
    if (frame.fx !== null) {
      message(list, 'info', 'Display conversion dated ' + String(frame.fx.as_of) + ', provenance ' + String(frame.fx.provenance) + '. These are operator-declared display rates, not invoice rates.');
    }
  }

  // Summary: baseline estimated, baseline billed, alternative and comparable kept separate.
  function renderSummary(body, frame) {
    var currency = frame.currency;
    var list = el('dl', 'cost-scenario-view__summary');
    metric(list, 'Baseline estimated (known subset)', money(frame.baseline.estimated.amount, currency),
      plural(frame.baseline.estimated.records, 'record'));
    metric(list, 'Baseline billed (reported)', money(frame.baseline.billed.amount, currency),
      plural(frame.baseline.billed.records, 'record'));
    metric(list, 'Alternative estimate (counterfactual)', money(frame.alternative.amount, currency),
      plural(frame.alternative.records, 'record'));
    metric(list, 'Comparable difference (alternative minus baseline estimated)',
      signedMoney(frame.comparable.difference, currency),
      plural(frame.comparable.records, 'comparable record') + ' · baseline ' +
      money(frame.comparable.baseline_estimated_amount, currency) + ' · alternative ' +
      money(frame.comparable.alternative_amount, currency));
    body.appendChild(list);

    var cov = frame.coverage;
    var coverage = el('dl', 'cost-scenario-view__coverage');
    metric(coverage, 'Matched usage records', count(cov.matched_usage_records));
    metric(coverage, 'Eligible usage records', count(cov.eligible_usage_records));
    metric(coverage, 'Baseline unknown / unconverted', count(cov.baseline_unknown_records) + ' / ' + count(cov.baseline_unconverted_records));
    metric(coverage, 'Alternative unpriced / unconverted', count(cov.scenario_unpriced_records) + ' / ' + count(cov.scenario_unconverted_records));
    body.appendChild(coverage);
  }

  // Per provider/model table, up to 100 rows, horizontally scrollable.
  function amountCell(row, text, detail) {
    var cell = el('td', 'cost-scenario-view__num');
    cell.appendChild(el('span', 'cost-scenario-view__amount', text));
    cell.appendChild(el('span', 'cost-scenario-view__detail', detail));
    row.appendChild(cell);
  }

  function renderTable(body, frame) {
    if (frame.groups.length === 0) {
      body.appendChild(el('p', 'cost-scenario-view__empty', 'No eligible usage records in this scope.'));
      return;
    }
    var currency = frame.currency;
    var wrap = el('div', 'cost-scenario-view__table-wrap');
    wrap.tabIndex = 0;
    wrap.setAttribute('role', 'region');
    wrap.setAttribute('aria-label', 'Provider and model groups, scrollable');
    var table = el('table', 'cost-scenario-view__table');
    table.appendChild(el('caption', '', 'Provider/model groups ranked by alternative estimate (' +
      count(frame.groups.length) + ' of ' + count(frame.groups_total) + ')'));
    var headRow = el('tr');
    ['Provider', 'Model'].concat(BUCKET_LABELS, ['Baseline estimated', 'Baseline billed', 'Alternative estimate', 'Comparable difference'])
      .forEach(function (label) {
        var th = el('th', '', label);
        th.scope = 'col';
        headRow.appendChild(th);
      });
    var head = el('thead');
    head.appendChild(headRow);
    table.appendChild(head);
    var rows = el('tbody');
    frame.groups.forEach(function (group) {
      var row = el('tr');
      var provider = el('th', '', group.provider);
      provider.scope = 'row';
      row.appendChild(provider);
      row.appendChild(el('td', '', group.model));
      BUCKETS.forEach(function (bucket) {
        row.appendChild(el('td', 'cost-scenario-view__num', count(group.tokens[bucket])));
      });
      amountCell(row, money(group.baseline.estimated.amount, currency), plural(group.baseline.estimated.records, 'record'));
      amountCell(row, money(group.baseline.billed.amount, currency), plural(group.baseline.billed.records, 'record'));
      amountCell(row, money(group.alternative.amount, currency), plural(group.alternative.records, 'record'));
      amountCell(row, signedMoney(group.comparable.difference, currency), plural(group.comparable.records, 'comparable record'));
      rows.appendChild(row);
    });
    table.appendChild(rows);
    wrap.appendChild(table);
    body.appendChild(wrap);
  }

  // Expose the selected book for audit without calculating any prices in the browser
  function renderRates(body, frame) {
    var details = el('details', 'cost-scenario-view__rates');
    details.appendChild(el('summary', '', 'Alternative rates (' + frame.scenario.rate_count + ')'));
    var drawn = false;
    details.addEventListener('toggle', function () {
      if (!details.open || drawn) return;
      drawn = true;
      details.appendChild(el('p', '', 'Rates per 1,000,000 disjoint tokens. Exact provider/model intervals include From and exclude Until. These chosen rates do not validate tiers, discounts or billing.'));
      var wrap = el('div', 'cost-scenario-view__table-wrap'); wrap.tabIndex = 0;
      wrap.setAttribute('role', 'region'); wrap.setAttribute('aria-label', 'Alternative price book, scrollable');
      var table = el('table', 'cost-scenario-view__table'), head = el('thead'), headings = el('tr'), rows = el('tbody');
      var fields = ['provider', 'model', 'currency'].concat(BUCKETS, ['from', 'until', 'provenance']);
      fields.forEach(function (field) { var th = el('th', '', field); th.scope = 'col'; headings.appendChild(th); });
      head.appendChild(headings); table.appendChild(head);
      frame.scenario.rates.slice(0, 200).forEach(function (rate) {
        var row = el('tr'); fields.forEach(function (field) { row.appendChild(el('td', '', rate[field])); }); rows.appendChild(row);
      });
      table.appendChild(rows); wrap.appendChild(table); details.appendChild(wrap);
      if (frame.scenario.rate_count > 200) details.appendChild(el('p', '', 'Showing 200 rates. The API and selected book contain all ' + frame.scenario.rate_count + '; calculations use the full book.'));
    });
    body.appendChild(details);
  }

  // Static notes describing what the analysis is and is not.
  var NOTES = [
    'Recorded baseline costs are unchanged. The alternative rate book is applied separately to the same records.',
    'Alternative rates match only by exact provider, model and recorded event date within [from, until), shown in the display currency.',
    'No tier, discount or contract matching. This is not invoice or billing validation and not a savings estimate.',
    'Billed amounts never enter the difference. The difference covers only records with both a converted baseline estimate and a converted alternative estimate.',
    'Missing costs are shown as Unknown, never as an amount of zero.',
    'Each result is a dated historical analysis of one ledger revision, not live worker status.'
  ];

  // Build the panel skeleton once; buttons persist so keyboard focus is kept.
  function build(root) {
    var view = el('section', 'cost-scenario-view');
    view.setAttribute('aria-labelledby', 'cost-scenario-view-heading');
    var header = el('header', 'cost-scenario-view__header');
    var heading = el('h2', 'cost-scenario-view__title', 'Cost scenario');
    heading.id = 'cost-scenario-view-heading';
    header.appendChild(heading);
    header.appendChild(el('strong', 'cost-scenario-view__label', 'COUNTERFACTUAL ESTIMATE'));
    view.appendChild(header);
    var scenario = el('p', 'cost-scenario-view__scenario');
    view.appendChild(scenario);
    var actions = el('div', 'cost-scenario-view__actions');
    var load = el('button', 'cost-scenario-view__button', 'Load cost scenario');
    load.type = 'button';
    var recalc = el('button', 'cost-scenario-view__button', 'Recalculate');
    recalc.type = 'button';
    actions.appendChild(load);
    actions.appendChild(recalc);
    view.appendChild(actions);
    var status = el('p', 'cost-scenario-view__status');
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    view.appendChild(status);
    var messages = el('ul', 'cost-scenario-view__messages');
    view.appendChild(messages);
    var body = el('div', 'cost-scenario-view__body');
    view.appendChild(body);
    var notes = el('ul', 'cost-scenario-view__notes');
    NOTES.forEach(function (text) { notes.appendChild(el('li', '', text)); });
    view.appendChild(notes);
    var setup = el('p', 'cost-scenario-view__setup');
    var link = el('a', 'cost-scenario-view__link', 'Review rate-book setup');
    link.href = '#settings/sources';
    setup.appendChild(link);
    view.appendChild(setup);
    root.appendChild(view);
    return { view: view, scenario: scenario, load: load, recalc: recalc, status: status, messages: messages, body: body };
  }

  // Read failure text: failures and timeouts leave amounts unknown.
  function describeError(error) {
    var status = error && error.status;
    if (status === 409) return 'Owner, ledger revision or rate book changed. Review the scope, then load or recalculate. Previous results, if shown, are historical.';
    if (status === 404) return 'The cost scenario is not available on this server (404). Amounts are unknown.';
    if (isCount(status)) return 'Read failed (HTTP ' + status + '). Amounts are unknown, not zero.';
    return 'Read failed. Amounts are unknown, not zero.';
  }

  // Mount: one panel per root with explicit reads only.
  function mount(root, opts) {
    if (!root || !opts || typeof opts.getContext !== 'function' || typeof opts.requestJSON !== 'function') {
      throw new TypeError('MonitoringCostScenario.mount requires root, getContext and requestJSON');
    }
    var dom = build(root);
    var state = {
      generation: 0, controller: null, timer: 0, loading: false,
      pendingKey: null, pendingRevision: null,
      result: null, resultKey: null, resultRevision: null, rendered: null,
      error: '', warning: '', destroyed: false
    };

    // Context read: tolerate root errors as unavailable context.
    function readContext() {
      var ctx;
      try { ctx = opts.getContext(); } catch (error) { return null; }
      if (!isObject(ctx)) return null;
      var hasConfig = ctx.config !== null && ctx.config !== undefined;
      var valid = hasConfig && validConfig(ctx.config);
      return {
        identity: ctx.identity, revision: ctx.revision, currency: ctx.currency,
        query: isObject(ctx.query) ? ctx.query : {},
        active: ctx.active === true, paused: ctx.paused === true, unavailable: ctx.unavailable === true,
        config: valid ? ctx.config : null, configInvalid: hasConfig && !valid
      };
    }

    // Read readiness: reasons a read cannot start.
    function blockReason(ctx) {
      if (!ctx) return 'Report context is unavailable.';
      if (ctx.configInvalid) return 'Rate-book status is malformed. Review the rate-book setup.';
      if (!ctx.config) return 'No alternative rate book was selected at owner startup.';
      if (!isCount(ctx.revision) || typeof ctx.currency !== 'string') return 'Ledger revision or currency is unknown.';
      if (ctx.unavailable) return 'The ledger is unavailable; reads are stopped.';
      if (ctx.paused) return 'Reads are paused.';
      if (!ctx.active) return 'This view is inactive.';
      return '';
    }

    // Cancel: bump the generation so any late response is ignored.
    function cancel(reason) {
      clearTimeout(state.timer);
      state.timer = 0;
      if (!state.loading) return;
      state.generation += 1;
      var controller = state.controller;
      state.controller = null;
      state.loading = false;
      if (controller) controller.abort();
      if (reason) state.warning = reason;
    }

    function clearResult() {
      state.result = null;
      state.resultKey = null;
      state.resultRevision = null;
      state.error = '';
      state.warning = '';
    }

    // Start one explicit read pinned to the current scope and revision.
    function start(ctx) {
      cancel('');
      var generation = ++state.generation;
      var controller = new AbortController();
      var expect = {
        identity: ctx.identity, revision: ctx.revision, currency: ctx.currency,
        query: ctx.query, config: ctx.config, key: scopeKey(ctx)
      };
      var params = new URLSearchParams();
      queryPairs(ctx.query).forEach(function (pair) { params.append(pair[0], pair[1]); });
      params.append('expected_identity', String(ctx.identity));
      params.append('expected_revision', String(ctx.revision));
      params.append('expected_scenario', ctx.config.id);
      state.controller = controller;
      state.loading = true;
      state.pendingKey = expect.key;
      state.pendingRevision = expect.revision;
      state.error = '';
      state.warning = '';
      state.timer = setTimeout(function () {
        if (state.destroyed || generation !== state.generation) return;
        cancel('');
        state.error = 'Read timed out after 8 seconds. Amounts are unknown, not zero.';
        render(readContext());
      }, TIMEOUT_MS);
      var request;
      try {
        request = Promise.resolve(opts.requestJSON(ENDPOINT + '?' + params.toString(), { signal: controller.signal }));
      } catch (error) {
        request = Promise.reject(error);
      }
      request.then(function (frame) { finish(generation, expect, frame, null); },
        function (error) { finish(generation, expect, null, error || {}); });
      render(ctx);
    }

    // Finish: refuse superseded, stale or inconsistent responses; keep the prior matching result.
    function finish(generation, expect, frame, error) {
      if (state.destroyed || generation !== state.generation) return;
      clearTimeout(state.timer);
      state.timer = 0;
      state.controller = null;
      state.loading = false;
      var ctx = readContext();
      if (error) {
        state.error = describeError(error);
      } else if (!ctx || scopeKey(ctx) !== expect.key || ctx.revision !== expect.revision ||
          ctx.paused || ctx.unavailable || !ctx.active) {
        state.warning = 'Response refused: scope, rate book or ledger revision changed while reading.';
      } else {
        var problem = validateFrame(frame, expect);
        if (problem) {
          state.error = 'Response refused: ' + problem + '. Amounts are unknown.';
        } else {
          state.result = frame;
          state.resultKey = expect.key;
          state.resultRevision = expect.revision;
        }
      }
      render(ctx);
    }

    // Render: controls and messages every time; the result body only when it changes.
    function render(ctx) {
      if (state.destroyed) return;
      var reason = blockReason(ctx);
      root.hidden = !ctx || (!ctx.config && !ctx.configInvalid);
      var config = ctx && ctx.config;
      dom.scenario.textContent = config
        ? 'Alternative rate book: ' + config.label + ' · ' + plural(config.rate_count, 'rate') + ' · id ' + config.id
        : 'Alternative rate book: none selected at owner startup.';
      dom.load.disabled = state.loading || reason !== '';
      dom.recalc.disabled = state.loading || reason !== '' || !state.result;
      dom.view.setAttribute('aria-busy', state.loading ? 'true' : 'false');

      if (state.loading) {
        dom.status.textContent = 'Reading cost scenario for ledger revision ' + state.pendingRevision + '…';
      } else if (state.result) {
        dom.status.textContent = 'Dated analysis of ledger revision ' + state.result.revision + ', observed at ' +
          state.result.observed_at + '. Historical result, not live worker status.';
      } else {
        dom.status.textContent = reason && reason !== 'This view is inactive.'
          ? reason : 'Not loaded. Select Load cost scenario to read a dated analysis.';
      }

      clear(dom.messages);
      if (state.error) message(dom.messages, 'danger', state.error);
      if (state.warning) message(dom.messages, 'warn', state.warning);
      if (state.result && (state.error || state.warning)) {
        message(dom.messages, 'warn', 'The previous result for the same scope is still shown.');
      }
      if (state.result && ctx && isCount(ctx.revision) && ctx.revision !== state.resultRevision) {
        message(dom.messages, 'warn', 'Ledger revision is now ' + ctx.revision + '; this result is from revision ' +
          state.resultRevision + '. Select Recalculate to read again.');
      }
      if (state.result && reason && reason !== 'This view is inactive.') message(dom.messages, 'warn', reason);

      if (state.rendered !== state.result) {
        clear(dom.body);
        if (state.result) {
          var notices = el('ul', 'cost-scenario-view__messages');
          coverageMessages(notices, state.result);
          renderSummary(dom.body, state.result);
          if (notices.firstChild) dom.body.appendChild(notices);
          renderTable(dom.body, state.result);
          renderRates(dom.body, state.result);
        }
        state.rendered = state.result;
      }
    }

    // Update: clear mismatching results, abort reads on pause, inactivity or scope change.
    function update() {
      if (state.destroyed) return;
      var ctx = readContext();
      var key = ctx ? scopeKey(ctx) : null;
      if (state.result && state.resultKey !== key) clearResult();
      if (state.loading) {
        if (!ctx || !ctx.active) cancel('Read cancelled because the view became inactive.');
        else if (ctx.paused || ctx.unavailable) cancel('Read cancelled because reads are paused or the ledger is unavailable.');
        else if (key !== state.pendingKey || ctx.revision !== state.pendingRevision) cancel('Read cancelled because the scope or ledger revision changed.');
      }
      render(ctx);
    }

    // Explicit Load and Recalculate share one guarded path.
    function onRead() {
      if (state.destroyed) return;
      var ctx = readContext();
      var reason = blockReason(ctx);
      if (reason) {
        state.error = reason;
        render(ctx);
        return;
      }
      start(ctx);
    }

    dom.load.addEventListener('click', onRead);
    dom.recalc.addEventListener('click', onRead);
    update();

    // Destroy: abort reads, ignore late responses and remove the panel.
    function destroy() {
      if (state.destroyed) return;
      cancel('');
      state.destroyed = true;
      state.generation += 1;
      dom.load.removeEventListener('click', onRead);
      dom.recalc.removeEventListener('click', onRead);
      if (dom.view.parentNode) dom.view.parentNode.removeChild(dom.view);
    }

    return { update: update, destroy: destroy };
  }

  window.MonitoringCostScenario = { mount: mount };
})();
