"""Counterfactual rate-book cost analysis over recorded usage events.

Recorded baseline costs remain authoritative and are never altered. A second,
operator-selected rate book is applied to the same accepted usage records to
produce a dated hypothetical estimate. This is analysis only: it is not invoice
validation and makes no savings claim. The module is pure: no I/O, network or
model calls, and it reads the clock only when ``now`` is omitted.
"""

import hashlib
import json
from decimal import Decimal, getcontext, localcontext

from .events import BUCKETS, label as event_label, timestamp
from .metrics import filters, now_iso, selected

SCHEMA = 'monitoring.cost-scenario.v1'
ID_SCHEMA = 'monitoring.cost-scenario-id.v1'
SCOPE = 'matching-accepted-usage-counterfactual'
CUMULATIVE_SURFACE = 'claude-code-result'
SYNTHETIC_PREFIX = 'synthetic'
DEFAULT_LABEL = 'operator-scenario'
MAX_RATES = 1000
MAX_EVENTS = 100000
MAX_GROUPS = 100
PRECISION = 60

COUNT_KEYS = (
    'baseline_estimated_records',
    'baseline_billed_records',
    'baseline_unknown_records',
    'baseline_unconverted_records',
    'scenario_priced_records',
    'scenario_unpriced_records',
    'scenario_unconverted_records',
    'comparable_estimated_records',
)


# Decimal text: exact plain strings so tiny amounts never collapse to zero.
def _decimal_text(value):
    """Return a finite Decimal as a plain decimal string, '0' for any zero."""
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError('amount must be a finite Decimal')
    if value.is_zero():
        return '0'
    text = format(value, 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text


# Converted amounts from the existing engines must be finite Decimals.
def _finite(value):
    """Reject non-Decimal or non-finite amounts returned by Exchange.convert."""
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError('converted amount must be a finite Decimal')
    return value


# JSON safety for echoed structures (filters, fx book) without mutating them.
def _json_safe(value):
    """Return a JSON-safe copy, rendering Decimal values as decimal strings."""
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError('value is not JSON-safe')


# Label validation reuses the ledger alias rule from .events.
def _label(value):
    """Validate a scenario label with the events alias validator."""
    result = event_label(value, "scenario label")
    if not isinstance(result, str) or not result:
        raise ValueError('scenario label is invalid')
    return result


# Revision validation: a non-negative int, booleans excluded.
def _check_revision(revision):
    """Reject booleans, non-integers and negative revisions."""
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError('revision must be a non-negative integer')


# Query scope: refuse lifecycle state, then canonicalize via the shared filters.
def _scope(query):
    """Return the canonical query without state; the input is not modified."""
    if not isinstance(query, dict):
        raise ValueError('query must be a mapping')
    if 'state' in query:
        raise ValueError('state filter is not supported for historical usage analysis')
    canonical = dict(filters(dict(query)))
    canonical.pop('state', None)
    return canonical


# Rate normalization: every field of every rate, canonical and order-independent.
def _rate_value(value):
    """Normalize one rate field value for hashing."""
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise ValueError('rate field has an unsupported type')


def _rates(pricing):
    """Return the pricing rate list, enforcing the rate-count bound."""
    rates = list(pricing.rates)
    if len(rates) > MAX_RATES:
        raise ValueError('rate book exceeds %d rates' % MAX_RATES)
    return rates


def _normalized_rates(pricing):
    """Return all rates, including unused ones, as sorted canonical dicts."""
    normalized = []
    for rate in _rates(pricing):
        if not isinstance(rate, dict):
            raise ValueError('rate must be a mapping')
        entry = {}
        for key, value in rate.items():
            if not isinstance(key, str):
                raise ValueError('rate field names must be strings')
            entry[key] = _rate_value(value)
        normalized.append(entry)
    normalized.sort(key=lambda entry: json.dumps(entry, sort_keys=True, separators=(',', ':')))
    return normalized


# Scenario identity: SHA-256 over canonical JSON of the full rate book and label.
def scenario_id(pricing, label=DEFAULT_LABEL):
    """Return a stable 64-hex id for a validated rate book and label. No I/O."""
    with localcontext() as context:
        context.prec = PRECISION
        payload = {
            'schema': ID_SCHEMA,
            'label': _label(label),
            'rates': _normalized_rates(pricing),
        }
    data = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return hashlib.sha256(data.encode('ascii')).hexdigest()


# Baseline classification: recorded cost precedence is kept by pricing.cost.
def _baseline(pricing, fx, event):
    """Classify one event as estimated, billed, unknown or unconverted."""
    cost = pricing.cost(event)
    if cost is None:
        return 'unknown', None
    basis = cost.get('basis')
    if basis not in ('estimated', 'billed'):
        raise ValueError('unsupported cost basis')
    amount = fx.convert(cost['amount'], cost['currency'])
    if amount is None:
        return 'unconverted', None
    return basis, _finite(amount)


# Alternative classification: force the scenario engine on a cost-free shallow copy.
def _alternative(scenario, fx, event):
    """Classify one event as priced, unpriced or unconverted under the scenario."""
    probe = dict(event)
    probe.pop('cost', None)
    cost = scenario.cost(probe)
    if cost is None:
        return 'unpriced', None
    amount = fx.convert(cost['amount'], cost['currency'])
    if amount is None:
        return 'unconverted', None
    return 'priced', _finite(amount)


# Amount presence: an amount exists only when its subset has records.
def _amount(total, records):
    """Return a decimal string when records > 0, otherwise None."""
    return _decimal_text(total) if records > 0 else None


# Tally: counts, tokens and exact sums for the whole scope or one group.
class _Tally:
    """Accumulate record counts, token sums and Decimal amounts for one scope."""

    def __init__(self):
        self.records = 0
        self.tokens = dict.fromkeys(BUCKETS, 0)
        self.counts = dict.fromkeys(COUNT_KEYS, 0)
        self.estimated = Decimal(0)
        self.billed = Decimal(0)
        self.alternative = Decimal(0)
        self.comparable_baseline = Decimal(0)
        self.comparable_alternative = Decimal(0)

    def add(self, tokens, baseline, alternative):
        """Add one eligible usage record with its baseline and alternative results."""
        base_kind, base_amount = baseline
        alt_kind, alt_amount = alternative
        self.records += 1
        for bucket in BUCKETS:
            self.tokens[bucket] += int(tokens[bucket])
        self.counts['baseline_%s_records' % base_kind] += 1
        if base_kind == 'estimated':
            self.estimated += base_amount
        elif base_kind == 'billed':
            self.billed += base_amount
        self.counts['scenario_%s_records' % alt_kind] += 1
        if alt_kind == 'priced':
            self.alternative += alt_amount
        if base_kind == 'estimated' and alt_kind == 'priced':
            self.counts['comparable_estimated_records'] += 1
            self.comparable_baseline += base_amount
            self.comparable_alternative += alt_amount

    def coverage(self, matched, future, cumulative):
        """Return the coverage block with exclusions and subset partitions."""
        block = {
            'matched_usage_records': matched,
            'eligible_usage_records': self.records,
            'excluded_future_records': future,
            'excluded_cumulative_records': cumulative,
        }
        for key in COUNT_KEYS:
            block[key] = self.counts[key]
        return block

    def amounts(self):
        """Return baseline, alternative and exact-intersection comparable blocks."""
        counts = self.counts
        comparable = counts['comparable_estimated_records']
        difference = None
        if comparable > 0:
            difference = _decimal_text(self.comparable_alternative - self.comparable_baseline)
        return {
            'baseline': {
                'estimated': {
                    'amount': _amount(self.estimated, counts['baseline_estimated_records']),
                    'records': counts['baseline_estimated_records'],
                },
                'billed': {
                    'amount': _amount(self.billed, counts['baseline_billed_records']),
                    'records': counts['baseline_billed_records'],
                },
            },
            'alternative': {
                'amount': _amount(self.alternative, counts['scenario_priced_records']),
                'records': counts['scenario_priced_records'],
            },
            'comparable': {
                'records': comparable,
                'baseline_estimated_amount': _amount(self.comparable_baseline, comparable),
                'alternative_amount': _amount(self.comparable_alternative, comparable),
                'difference': difference,
            },
        }


# Group ranking: known alternative amount desc, tokens desc, then provider/model.
def _rank(item):
    """Sort key placing groups with unknown alternative amounts after known ones."""
    (provider, model), tally = item
    known = tally.counts['scenario_priced_records'] > 0
    amount = -tally.alternative if known else Decimal(0)
    return (0 if known else 1, amount, -sum(tally.tokens.values()), provider, model)


# Group row: same accounting shape as the totals, exclusions fixed at zero.
def _group_row(provider, model, tally):
    """Return one provider/model group frame."""
    row = {
        'provider': provider,
        'model': model,
        'usage_records': tally.records,
        'tokens': dict(tally.tokens),
    }
    row.update(tally.amounts())
    row['coverage'] = tally.coverage(tally.records, 0, 0)
    return row


# Ledger bound: iterate without copying and refuse oversized inputs.
def _bounded(events):
    """Yield events, raising when more than MAX_EVENTS are supplied."""
    for index, event in enumerate(events):
        if index >= MAX_EVENTS:
            raise ValueError('event ledger exceeds %d records' % MAX_EVENTS)
        yield event


# Main analysis: scope, exclusions, per-record classification and grouping.
def cost_scenario(revision, events, query, pricing, scenario, fx, *, label=DEFAULT_LABEL, now=None):
    """Return a JSON-safe counterfactual cost-scenario frame.

    Baseline amounts come from ``pricing.cost`` with recorded-cost precedence.
    Alternative amounts come from ``scenario.cost`` on cost-free copies. Both
    use the same ``fx``. Inputs are not mutated.
    """
    _check_revision(revision)
    canonical = _scope(query)
    label = _label(label)
    engine_context = getcontext().copy()
    with localcontext() as context:
        context.prec = PRECISION
        ident = scenario_id(scenario, label)
        rate_count = len(_rates(scenario))
        observed = timestamp(now if now is not None else now_iso())

        totals = _Tally()
        groups = {}
        matched = future = cumulative = 0
        synthetic = False

        for event in _bounded(events):
            if not isinstance(event, dict) or event.get('kind') != 'usage':
                continue
            if not selected(event, canonical):
                continue
            matched += 1
            if timestamp(event['timestamp']) > observed:
                future += 1
                continue
            if event.get('surface') == CUMULATIVE_SURFACE:
                cumulative += 1
                continue
            # Preserve existing per-record pricing/FX precision, then sum those results
            with localcontext(engine_context):
                baseline = _baseline(pricing, fx, event)
                alternative = _alternative(scenario, fx, event)
            key = (event['provider'], event['model'])
            group = groups.get(key)
            if group is None:
                group = groups[key] = _Tally()
            totals.add(event['tokens'], baseline, alternative)
            group.add(event['tokens'], baseline, alternative)
            source = event.get('source')
            if isinstance(source, str) and source.startswith(SYNTHETIC_PREFIX):
                synthetic = True

        ranked = sorted(groups.items(), key=_rank)
        rows = [_group_row(provider, model, tally) for (provider, model), tally in ranked[:MAX_GROUPS]]
        amounts = totals.amounts()

        return {
            'schema': SCHEMA,
            'revision': revision,
            'observed_at': observed,
            'filters': _json_safe(canonical),
            'currency': fx.currency,
            'fx': _json_safe(fx.raw),
            'scope': SCOPE,
            'complete_coverage': False,
            'scenario': {
                'id': ident,
                'label': label,
                'kind': 'counterfactual',
                'rate_count': rate_count,
                'rates': _normalized_rates(scenario),
            },
            'coverage': totals.coverage(matched, future, cumulative),
            'baseline': amounts['baseline'],
            'alternative': amounts['alternative'],
            'comparable': amounts['comparable'],
            'groups': rows,
            'groups_total': len(ranked),
            'groups_truncated': len(ranked) > MAX_GROUPS,
            'contains_synthetic_records': synthetic,
        }
