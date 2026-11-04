"""Focused tests for counterfactual cost-scenario analysis with synthetic data."""

import copy
import json
import os
import re
import tempfile
import unittest
from decimal import Decimal

from monitoring_ai.cost_scenarios import cost_scenario, scenario_id

# Adapter: the only code that depends on the existing price-book format and loaders.
# Use the product's existing validated pricing and exchange loaders.
from monitoring_ai.metrics import Exchange, Pricing
from pathlib import Path

BOOK_SCHEMA = 'monitoring.pricing.v1'
RATE_FIELDS = {
    'input': 'input',
    'cache_read': 'cache_read',
    'cache_write': 'cache_write',
    'output': 'output',
}


def write_book(directory, name, rates):
    """Write a synthetic price book file and return its path."""
    path = os.path.join(directory, name)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump({'schema': BOOK_SCHEMA, 'rates': rates}, handle)
    return path


def load_pricing(path):
    """Load a price book through the existing validated loader."""
    return Pricing(Path(path))


def display_exchange():
    """Return the default USD Exchange with no FX book."""
    return Exchange()


# Synthetic fixtures: fixed dates, synthetic sources, no clock reads.
NOW = '2026-03-01T00:00:00.000Z'
JAN = '2026-01-15T00:00:00.000Z'
YEAR_START = '2026-01-01T00:00:00.000Z'
YEAR_END = '2027-01-01T00:00:00.000Z'


def rate(provider, model, start=YEAR_START, end=YEAR_END, currency='USD', **prices):
    """Build one synthetic rate entry with per-million prices."""
    entry = {
        'provider': provider,
        'model': model,
        'currency': currency,
        'from': start,
        'until': end,
        'provenance': 'synthetic-book',
    }
    for bucket, field in RATE_FIELDS.items():
        entry[field] = prices.get(bucket, '0')
    return entry


def tokens(input=0, cache_read=0, cache_write=0, output=0):
    """Build a four-bucket token mapping."""
    return {'input': input, 'cache_read': cache_read, 'cache_write': cache_write, 'output': output}


def usage(ident, when, provider, model, used, cost=None, surface='cli', session='s-main'):
    """Build one synthetic usage event."""
    event = {
        'schema': 'monitoring.event.v1',
        'source': 'synthetic-fixture',
        'id': ident,
        'session': session,
        'entity': 'agent-1',
        'parent': None,
        'entity_type': 'agent',
        'kind': 'usage',
        'timestamp': when,
        'provider': provider,
        'model': model,
        'surface': surface,
        'project': 'demo',
        'tokens': used,
    }
    if cost is not None:
        event['cost'] = cost
    return event


def recorded(amount, basis, currency='USD'):
    """Build a recorded cost block."""
    return {'amount': amount, 'currency': currency, 'basis': basis, 'provenance': 'recorded'}


def dec(value):
    """Parse an amount string for exact comparison; None stays None."""
    return None if value is None else Decimal(value)


class CostScenarioTests(unittest.TestCase):
    """Accounting behaviour of cost_scenario and scenario_id."""

    # Shared books: baseline prices anthropic/model-a only; scenario prices more.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.scenario_rates = [
            rate('anthropic', 'model-a', input='2', output='10'),
            rate('openai', 'model-a', input='1'),
            rate('anthropic', 'model-b', currency='EUR', input='1'),
            rate('anthropic', 'model-c', end='2026-02-01T00:00:00.000Z', input='1'),
        ]
        base_path = write_book(self.tmp.name, 'baseline.json', [rate('anthropic', 'model-a', input='5', output='50')])
        scenario_path = write_book(self.tmp.name, 'scenario.json', self.scenario_rates)
        self.pricing = load_pricing(base_path)
        self.scenario = load_pricing(scenario_path)
        self.fx = display_exchange()

    def run_scenario(self, events, query, revision=7):
        """Run the analysis with the shared books and a fixed clock."""
        return cost_scenario(revision, events, query, self.pricing, self.scenario, self.fx, now=NOW)

    # Recorded precedence, billed/unknown/known-zero subsets and exact intersection.
    def test_baseline_subsets_and_comparable_intersection(self):
        events = [
            usage('e1', JAN, 'anthropic', 'model-a', tokens(input=1000000, output=100000), recorded('1.5', 'estimated')),
            usage('e2', JAN, 'anthropic', 'model-a', tokens(input=1000000, output=100000), recorded('2.25', 'billed')),
            usage('e3', JAN, 'openai', 'model-a', tokens(input=1000000)),
            usage('e4', JAN, 'anthropic', 'model-a', tokens(input=1000000, output=100000), recorded('0', 'estimated'), session='s-zero'),
            usage('e5', JAN, 'anthropic', 'model-a', tokens(input=1000000, output=100000)),
        ]
        query = {'project': 'demo'}
        events_before = copy.deepcopy(events)
        query_before = copy.deepcopy(query)

        frame = self.run_scenario(events, query)

        self.assertEqual(events, events_before)
        self.assertEqual(query, query_before)
        coverage = frame['coverage']
        self.assertEqual(coverage['eligible_usage_records'], 5)
        self.assertEqual(coverage['baseline_estimated_records'], 3)
        self.assertEqual(coverage['baseline_billed_records'], 1)
        self.assertEqual(coverage['baseline_unknown_records'], 1)
        self.assertEqual(coverage['scenario_priced_records'], 5)
        # Recorded 1.5 wins over the 10 the baseline book would compute.
        self.assertEqual(dec(frame['baseline']['estimated']['amount']), Decimal('11.5'))
        self.assertEqual(dec(frame['baseline']['billed']['amount']), Decimal('2.25'))
        self.assertEqual(dec(frame['alternative']['amount']), Decimal('13'))
        comparable = frame['comparable']
        self.assertEqual(comparable['records'], 3)
        self.assertEqual(dec(comparable['baseline_estimated_amount']), Decimal('11.5'))
        self.assertEqual(dec(comparable['alternative_amount']), Decimal('9'))
        self.assertEqual(dec(comparable['difference']), Decimal('-2.5'))

        # Same model under two providers stays as two groups; unknown is not zero.
        self.assertEqual([(g['provider'], g['model']) for g in frame['groups']],
                         [('anthropic', 'model-a'), ('openai', 'model-a')])
        openai = frame['groups'][1]
        self.assertIsNone(openai['baseline']['estimated']['amount'])
        self.assertEqual(openai['coverage']['baseline_unknown_records'], 1)
        self.assertEqual(openai['comparable']['records'], 0)
        self.assertIsNone(openai['comparable']['difference'])
        self.assertEqual(dec(openai['alternative']['amount']), Decimal('1'))

        # A known recorded zero stays a zero amount, distinct from unknown.
        zero = self.run_scenario(events, {'session': 's-zero'})
        self.assertEqual(zero['coverage']['matched_usage_records'], 1)
        self.assertEqual(zero['baseline']['estimated']['records'], 1)
        self.assertEqual(dec(zero['baseline']['estimated']['amount']), Decimal('0'))

    # Exclusions, [from, until) boundaries, missing price, missing FX and filter scope.
    def test_exclusions_boundaries_and_missing_prices(self):
        events = [
            usage('b1', '2026-01-31T23:59:59.999Z', 'anthropic', 'model-c', tokens(input=1000000)),
            usage('b2', '2026-02-01T00:00:00.000Z', 'anthropic', 'model-c', tokens(input=1000000)),
            usage('b3', JAN, 'anthropic', 'model-b', tokens(input=1000000), recorded('4', 'estimated', currency='EUR')),
            usage('b4', '2026-04-01T00:00:00.000Z', 'anthropic', 'model-c', tokens(input=5), surface='claude-code-result'),
            usage('b5', JAN, 'anthropic', 'model-c', tokens(input=5), surface='claude-code-result'),
            usage('b7', JAN, 'openai', 'model-a', tokens(input=1000000)),
            {'schema': 'monitoring.event.v1', 'kind': 'tool', 'id': 'b6', 'timestamp': JAN},
        ]
        frame = self.run_scenario(events, {'provider': 'anthropic'})

        coverage = frame['coverage']
        self.assertEqual(coverage['matched_usage_records'], 5)
        self.assertEqual(coverage['eligible_usage_records'], 3)
        self.assertEqual(coverage['excluded_future_records'], 1)
        self.assertEqual(coverage['excluded_cumulative_records'], 1)
        self.assertEqual(coverage['baseline_unknown_records'], 2)
        self.assertEqual(coverage['baseline_unconverted_records'], 1)
        self.assertEqual(coverage['scenario_priced_records'], 1)
        self.assertEqual(coverage['scenario_unpriced_records'], 1)
        self.assertEqual(coverage['scenario_unconverted_records'], 1)
        self.assertIsNone(frame['baseline']['estimated']['amount'])
        self.assertEqual(dec(frame['alternative']['amount']), Decimal('1'))
        self.assertEqual(frame['comparable']['records'], 0)
        self.assertIsNone(frame['comparable']['difference'])
        self.assertTrue(frame['contains_synthetic_records'])
        self.assertEqual([g['model'] for g in frame['groups']], ['model-c', 'model-b'])
        self.assertIsNone(frame['groups'][1]['alternative']['amount'])
        self.assertFalse(frame['groups_truncated'])
        self.assertEqual(frame['scenario']['rate_count'], len(self.scenario_rates))

        # Scenario aggregation must not change the shared engine's FX precision
        fx_path = Path(self.tmp.name) / 'fx.json'
        fx_path.write_text(json.dumps(dict(schema='monitoring.fx.v1', base='USD', as_of=JAN,
            provenance='synthetic-fx', rates=dict(USD='1', EUR='3'))))
        fx = Exchange('USD', fx_path)
        event = usage('fraction', JAN, 'anthropic', 'model-b', tokens(input=1000000), recorded('1', 'estimated', currency='EUR'))
        fraction = cost_scenario(1, [event], {}, self.pricing, self.scenario, fx, now=NOW)
        self.assertEqual(dec(fraction['baseline']['estimated']['amount']), fx.convert('1', 'EUR'))
        self.assertEqual(dec(fraction['alternative']['amount']), fx.convert('1', 'EUR'))

    # Stable scenario id, changed books and labels, and input refusals.
    def test_scenario_id_and_refusals(self):
        copy_path = write_book(self.tmp.name, 'copy.json', self.scenario_rates)
        first = scenario_id(self.scenario)
        self.assertRegex(first, re.compile(r'^[0-9a-f]{64}$'))
        self.assertEqual(first, scenario_id(load_pricing(copy_path)))

        changed = copy.deepcopy(self.scenario_rates)
        changed[0][RATE_FIELDS['output']] = '11'
        changed_path = write_book(self.tmp.name, 'changed.json', changed)
        self.assertNotEqual(first, scenario_id(load_pricing(changed_path)))

        extra = self.scenario_rates + [rate('anthropic', 'model-unused', input='3')]
        extra_path = write_book(self.tmp.name, 'extra.json', extra)
        self.assertNotEqual(first, scenario_id(load_pricing(extra_path)))
        self.assertNotEqual(first, scenario_id(self.scenario, 'other-scenario'))

        with self.assertRaises(ValueError):
            self.run_scenario([], {'state': 'active'})
        for revision in (True, -1, '3', 1.0):
            with self.assertRaises(ValueError):
                self.run_scenario([], {}, revision=revision)


if __name__ == '__main__':
    unittest.main()
