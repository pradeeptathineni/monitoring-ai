# Counterfactual cost analysis

A cost scenario applies a deliberately selected rate book to recorded disjoint token usage. It answers what those tokens would cost under those chosen rates. It does not replace original cost evidence, verify a bill, measure task quality or calculate a subscription balance.

Start an owner with an explicit alternative book:

```sh
python3 -m monitoring_ai serve --db /path/to/events.sqlite \
  --scenario-pricing /path/to/alternative-pricing.json \
  --scenario-label operator-scenario
```

Use the existing `monitoring.pricing.v1` format. Supply exact provider/model aliases, currency, provenance, dated `[from, until)` intervals and input/cache-read/cache-write/output rates per million disjoint tokens. The selected regular file is bounded to one MiB and 1,000 rates. Nothing fetches rates or contacts providers. The file is loaded once. Deliberately restart the owner to select a changed book. **Settings → Sources and connections** owns this setup guidance.

For a fictional example, add `--demo --scenario-pricing examples/scenario-pricing.json --scenario-label fictional-rates` with a new temporary ledger. Those rates are made up, not provider prices. Their dates are explicit and do not imply that they are current.

Open **Report → Cost scenario**, then choose **Load cost scenario**. Existing provider/model/surface/project/source/session/UTC filters apply. Current lifecycle state does not filter historical cost analysis. A result is pinned to one owner, ledger revision, rate-book identity, currency and filter scope. A changed owner, filter, currency or scenario clears mismatching results. A newer ledger revision retains the dated result with a Recalculate action. Reads are explicit, stop when inactive/paused/disconnected, and refuse late or mismatching replies. No periodic scenario calculation runs in the browser.

## Interpret the amounts

- **Baseline estimated:** Original recorded estimates take precedence. Without recorded cost, the owner's optional baseline `--pricing` book may supply an estimate. Missing prices and conversion remain separate unknown counts.
- **Baseline billed:** Only the original reported billed subset. It never combines with estimated cost or enters the comparison difference.
- **Alternative estimate:** The alternative book is applied independently, including to token records that carry reported billed amounts. It is always counterfactual. Missing exact provider/model/date rates and missing conversion stay unpriced or unconverted.
- **Comparable difference:** Alternative minus estimated baseline, only for the exact intersection of records with both converted amounts. It is null when that intersection is empty. This is not a savings claim and never subtracts differently covered totals.

Known zero remains zero. A missing amount remains null/Unknown. Both baseline and alternative reuse the existing decimal pricing and FX engine under the same declared display currency. Per-record engine precision is preserved. USD is the default. Any supplied FX book retains its date and provenance as an operator-declared display reference, not an invoice conversion rate.

Cumulative `claude-code-result` receipts are excluded from this entire analysis because receipt dates can differ from underlying activity dates. Future-dated usage is also excluded. Future exclusion happens first so the two counts are disjoint. Other source coverage remains incomplete. Usage records can be counter deltas rather than individual calls.

The provider/model table groups exact pairs, keeping identical model names under different providers separate. Totals cover the entire filtered eligible scope even when only the first 100 groups are shown. Expand **Alternative rates** to inspect the selected dates, currencies, provenance and four bucket rates. Its browser table shows at most 200 rates, while the API and calculations retain all selected rates. Pricing does not infer tiers, discounts, contracts, modalities or cache durations beyond the supported buckets.

## Read-only terminal analysis

```sh
python3 -m monitoring_ai cost-scenario --db /path/to/existing-events.sqlite \
  --scenario-pricing /path/to/alternative-pricing.json --json
```

Optional baseline `--pricing`, `--fx`, `--currency`, metadata/date filters and `--expected-revision` use the same owner semantics. This command reads a bounded, validated, consistent existing ledger without initializing a missing file or changing original records. It makes no provider request.

## Read contract

Configured owners advertise `cost_scenario: "/api/v1/cost-scenario"` in capabilities and an optional snapshot `cost_scenario_status` with schema `monitoring.cost-scenario-status.v1`, rate-book `id`, `label`, `rate_count` and `kind: "counterfactual"`. Unconfigured owners omit these blocks and return 404 at the endpoint.

`GET /api/v1/cost-scenario` accepts the listed non-state filters and requires `expected_identity`, `expected_revision` and `expected_scenario`. A changed owner, ledger or rate-book identity returns 409. Unsupported filters, malformed values and current-state filters return 400. It follows the existing loopback Host/Origin rules. No mutation or price-selection HTTP route exists.

The `monitoring.cost-scenario.v1` frame includes identity/revision/observation time, canonical filters, display currency/FX, `scope: "matching-accepted-usage-counterfactual"`, `complete_coverage: false`, scenario identity/label/count/normalized rates, coverage, baseline, alternative, comparable and bounded provider/model groups. Coverage partitions matched usage into eligible/future/cumulative subsets. Baseline partitions eligible records into converted estimated/billed, unknown and unconverted. Alternative partitions them into priced, unpriced and unconverted. Amount blocks carry their own contributing record counts. Zero contributing records means null amount. Comparable amounts describe the exact shared estimated subset. Group rows use the same partitions, with zero exclusions because they contain eligible records only. `groups_total` and `groups_truncated` distinguish table bounds from global totals. `contains_synthetic_records` identifies the reserved synthetic source-prefix convention, not universal source-origin verification.

Deep should discover this optional workflow and retain Monitoring's calculation and explanation ownership. Existing usage inspection already supplies original costs, per-bucket rates and FX evidence. Deep can hand off there instead of implementing another pricing engine. This candidate does not claim managed receiving or UAT adoption.

## Qualification

A locally selected task-owned ledger supplied 344 accepted historical events and 131 usage records. One newly completed authorized project-only Claude implementation receipt was captured with its original completion receipt time, checked through explicit source configuration and preview, and accepted by a finite owner at revision 345. The scenario excluded that cumulative receipt, kept original estimated/billed costs unknown, and priced 131 historical records using clearly fictional rates. Its amount matched an independent decimal calculation, and readonly CLI/API results agreed. The original backup stayed unchanged. This is accepted-history analysis plus a genuine new completed receipt, not live inference or worker lifecycle. Historical record origins were not all re-audited; a zero-token model alias contains “synthetic,” which alone does not classify its origin.

On that workload, the calculation took 5.867 ms, the scenario API 15.000 ms and explicit UI display 288.030 ms. Desktop, 390 px width and no page errors passed. Actual values, aliases and screenshots remain private. Synthetic tests cover recorded precedence, billed/unknown/zero, exact comparison subsets, missing prices/FX, dates, scope, source identity, readonly operation and failure recovery. Larger ledgers, real contractual prices/bills, sustained acquisition, hosted acceptance and managed Deep admission remain separate gaps.
