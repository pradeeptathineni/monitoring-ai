# Recorded session timeline

In **Sessions and workers**, choose **Timeline** on an observed entity. The view pins that row's exact source, session and Monitoring owner. It combines parent-scoped entity lanes, status evidence intervals and separate usage/tool markers. These are observation identities, not execution targets. No process, app task or provider worker is joined by name or time.

The chart and latest source-status label have different meanings. A status marker records what the producer supplied at that time. A nonterminal band ends at the next actual status, the requested range boundary or 60 seconds after its source timestamp, whichever comes first. Quiet time after evidence expires is unknown. A later completion does not turn the whole intervening span into continuous work. Terminal markers are recorded outcomes, not task acceptance or conversation closure. Usage and tool records never make an entity running.

Choose an entity lane or all returned lanes, inspect a marker in the table, then **Open entity history** to read its exact existing chronology. The detail shows only normalized metadata and explicitly reported duration, when supplied. It never derives a tool start or inference duration from gaps. The selected lane's latest accepted status is labeled separately from each historical event's state.

**Follow timeline** is a view-only choice. It reads the owner when ledger revision changes and at most once per ten seconds otherwise, driven by the existing page refresh. It creates no timer service, account poll or saved preference. Disable Follow for a frozen dated result, or use Refresh explicitly. Paused, hidden, inactive or disconnected views stop timeline reads. Failed reads retain dated data with an error. A changed owner clears the result and requires closing/reopening a current owner's row. No alias silently reconnects to a replacement process.

Metadata/date filters apply to shown markers, while current lifecycle state is omitted. Status context uses the exact source/session/entity across those metadata filters and before the selected range. This prevents a model filter from hiding a later terminal status and manufacturing a running label. Context status adds neither hidden usage nor tool volume. Source input failure, browser pause or unavailable reads make current visibility unknown. Equal-time contradictory statuses remain conflicting/unknown. Parent links are supplied relationships, with unobserved, outside-view, ambiguous and cyclic cases explicit.

The API returns at most 500 latest matching records and 100 entity lanes, with matching/shown/omitted counts. The table lists up to 50 retained markers for its lane selection. Tokens describe shown usage only. The chart axis fits returned observations and bounded status intervals. Co-timed markers keep the same horizontal time coordinate and can stack vertically; that does not assign their order. Larger co-timed sets can overlap, while the table keeps their separate records. No missing period is shown as zero activity. Coverage remains incomplete.

## Existing source capture

Use an authorized normalized event file or the existing explicit [Claude CLI stream capture](claude-cli-stream.md). No new source permission is needed merely to inspect already accepted observations. Setup and file selection remain in Settings and the CLI. There is no home scan, hook, new credential or persistent host installation.

## Read-only CLI

```sh
python3 -m monitoring_ai session-timeline --db /path/to/existing-events.sqlite \
  --source selected-source --session selected-session --json
```

Optional provider/model/surface/project/date filters and `--limit 1` through `500` match the marker view. `--expected-revision` refuses a changed ledger. The command validates a consistent existing ledger without creating a missing database or contacting a provider.

## Read contract

Capabilities advertise `session_timeline: "/api/v1/session-timeline"`. Older owners remain unsupported explicitly. The dedicated GET requires exact `source`, `session`, `expected_identity` and `expected_revision`, with optional metadata/date filters and `limit`. Mismatched owner/revision returns 409, invalid/missing controls or current-state filters return 400. Empty matching scope returns an empty exact session, with no broad fallback. Existing loopback Host/Origin rules apply. There is no mutation endpoint.

`monitoring.session-timeline.v1` contains identity/revision, observation time, canonical filters, source/session, `range`, coverage, `truncated`, lanes, markers, segments and existing owner `source_status`. Scope is `exact-source-session-recorded-evidence`; `complete_coverage` is always false. Lanes retain explicit parent/type/depth, parent-state/cycle/conflict diagnostics, shown token/tool counts and latest accepted status. Marker fields are projected by kind. Costs, arbitrary metadata, prompts, arguments, raw tool results and native paths are excluded. Segments describe bounded status evidence, never continuous work. Context counts can include co-timed status records outside the shown marker set. Future records matching the requested filters are excluded and counted. Totals never claim full machine/account coverage.

Deep should discover the optional capability, pin the owner/revision/scope and preserve observation/evidence times and unknown states. A handoff to this native inspection path can avoid duplicating chronology rules. Managed Deep adoption is separate from this standalone source/API/UI qualification.

## Qualification and limits

An ordinary authorized Claude Code 2.1.308 implementation ran through the existing subscription route as `claude-opus-5-5`. Restricted Read access was confined to new project instructions and synthetic fixtures. Its actual stdout was projected as it arrived into a private normalized source. An independent finite owner observed start evidence after 416 ms, expiry to unknown without a new event, and completion after 1.596 seconds. These measure local capture-to-owner observation, excluding provider write latency.

The source supplied two statuses and two matched Read request/result observations. Both tool outcomes stayed unknown because native results omitted the explicit error flag. No success or failure was inferred from that absence. No duration was inferred for those tools. The status-only selection emitted no usage; that does not mean the invocation consumed zero provider tokens or cost. A separate sanitized cumulative result file exists but was not selected for this trial.

The finished timeline retained all four records, one unobserved-parent lane and one 60-second band within a 238.254-second source span. Final private desktop/mobile and entity-history navigation passed with no browser errors, API/readonly CLI parity and unchanged ledger bytes. The API took 5.372 ms and UI display 170.120 ms on that workload. Actual aliases, values, raw stream and screenshots remain local/private. The finite capture/owner/browser stopped afterward. This qualifies one explicit invocation and the observed CLI shape, not broad workers, all Claude sessions, native Codex freshness, provider-wide tokens/costs or sustained acquisition. Larger workloads and managed Deep receiving remain open.
