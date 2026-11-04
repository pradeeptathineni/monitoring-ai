# Inspect recorded usage

**Usage & costs → Recorded usage** loads a page of accepted usage observations under the current provider, model, surface, project, source, session and UTC date filters. The separate cost filter selects known estimates, known billed amounts, unpriced records or missing currency conversion. Lifecycle state does not filter these historical records.

Each record shows its disjoint input, cache-read, cache-write and output tokens, cost before conversion, display-currency amount and cost provenance. **Inspect** explains the selected cost policy. **Open entity history** opens the exact recorded source/session/entity with its own read revision. It creates no runtime or process relationship.

Unpriced and unconverted records link to **Settings → Sources and connections → Prices and currency conversion**. That section explains the existing startup price/FX options and fictional format examples. It does not write books, fetch rates or change a running owner. Changing a selected book remains a deliberate owner operation.

Recorded estimated or billed costs take precedence over a price book. Without recorded cost, the existing owner pricing engine uses an exact provider/model and dated price interval, including its start and excluding its end. The detail view shows each token bucket multiplied by the supplied per-million rate, in that rate's currency. These are API-equivalent estimates, not a subscription balance or provider invoice. The inspector uses the existing pricing policy and does not force recalculation of recorded costs.

A known recorded zero remains zero. Missing recorded cost and a missing dated rate remain unknown. A known cost with missing required FX remains separately unconverted, retaining its original amount, currency, basis and provenance. Defaults remain USD. A supplied FX book has its own date/provenance and is a display-rate reference. Matching source/display currencies need no conversion. Nothing downloads prices, contacts a provider, changes a ledger or creates spending authority.

Pages contain 50 records in the browser, newest first. Paging pins one owner identity and ledger revision. Owner/filter/currency changes clear the selection. A newer revision requires reload before further paging. Failed reads retain a prior valid page with an error, and paused reads retain historical rows. Late replies from an obsolete selection are discarded. An older owner without the advertised capability reports unsupported rather than an empty record set.

Usage records can be token-counter deltas or receipt-time cumulative summaries. Counts are not necessarily API calls. A receipt timestamp can differ from underlying call times. Future-dated accepted records remain visible for inspection and are counted explicitly. These rows do not establish current activity, complete source coverage or complete billing.

## Read an existing ledger from the CLI

```sh
python3 -m monitoring_ai usage-records --db /private/ledger.sqlite \
  --cost-state unpriced --limit 50
python3 -m monitoring_ai usage-records --db /private/ledger.sqlite \
  --cost-state unpriced --limit 50 --after 50 --expected-revision FIRST_REVISION --json
```

Optional `--pricing`, `--fx` and `--currency` use the existing validated books. Provider/model/surface/project/source/session/date flags match the browser. Pages accept 1–200 records. Later CLI pages require the first revision. Keep the selected ledger fixed between invocations. The reader opens a consistent read-only SQLite transaction, validates normalized records and refuses a missing/incompatible ledger without initializing it. The same bounded reader serves exact entity history.

## Consumer contract

`GET /api/v1/capabilities` advertises `usage_records: "/api/v1/usage-records"`, `usage_filters` and `limits.usage_page: 200`. This endpoint is separate from snapshot v1 and report v1. Deep owns its receiving reader, presentation and managed admission.

`GET /api/v1/usage-records` accepts the listed non-state filters plus `cost_state`, `limit` (default 50, maximum 200), `after` (default 0), `expected_revision` and `expected_identity`. Later pages require both the first revision and owner identity. A mismatch returns HTTP 409. Invalid controls or current-state filters return HTTP 400. The existing 100,000-event read bound applies to the whole ledger and refuses overflow instead of silently truncating.

The `monitoring.usage-records.v1` response contains:

- `identity`, `revision`, `observed_at`, `currency`, supplied `fx`, normalized `filters` and `cost_state`.
- `scope: "matching-accepted-ledger-usage"` and `complete_coverage: false`.
- `records`, `total` after the cost filter, `offset`, `limit` and nullable `next_offset`.
- `matching_usage_records` and `cost_coverage` before the cost filter. Coverage keys are `estimated`, `billed`, `unpriced` and `unconverted`. Estimated/billed counts require a known display-currency amount.
- `contains_synthetic_records` for the existing synthetic-demo source namespace and `future_dated_records` in the selected scope.

Each row contains the accepted validated usage `event`, `pricing_method` (`recorded`, `price-book` or `unpriced`), `cost_state`, a plain `explanation`, nullable `native_cost` before conversion and nullable `display_cost`. Amounts are decimal strings. A matched price additionally supplies its exact `rate` and native-currency `bucket_costs`. Recorded costs are never replaced by those calculations. Unknown amounts remain null, while a known zero remains a decimal zero string.

Consumers must pin owner, revision, filters, currency and cost scope across pages, preserve original currency/basis/provenance, and distinguish incomplete or unsupported reads from empty accepted history. Record order is descending timestamp/source/ID. No usage record becomes a worker/session lifecycle or provider invoice by being displayed here.

## Qualification

A finite trial read an existing authorized private 344-event backup containing 131 historical usage records. Three pages covered every usage identity exactly once, and all four token-bucket totals matched independent source SQL. The browser inspected one record, filtered by provider, opened its exact entity history and retained rows on pause. Source bytes were unchanged, and temporary services stopped. Those actual records had no recorded/configured prices, so their amounts stayed unknown. Recorded zero, dated price calculation and FX behavior have focused synthetic qualification, not reconciliation against a real provider bill. No fresh provider event, paid call, persistent source access or managed Deep change was involved.
