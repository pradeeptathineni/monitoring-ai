# Completed Claude CLI results

The `claude-results` adapter reads explicitly selected, timestamped CLI result snapshots. It does not run Claude, discover sessions or read login data. The installed CLI qualified for this work is Claude Code 2.1.308. Other versions require format checks.

Claude documents JSON result output and cumulative per-model usage. Resumed results can include earlier conversation totals, and model totals include subagent usage. The reported costs are client-side estimates. They are not provider billing or a subscription balance. See the official [CLI output documentation](https://code.claude.com/docs/en/headless) and [usage and cost scope](https://code.claude.com/docs/en/agent-sdk/cost-tracking).

## Select and check a result

Use a completed JSON result that you already own or are authorized to read. Record its original receipt time with a timezone. Do not substitute the current time when rereading an old result.

```sh
python3 -m monitoring_ai sources capture-claude \
  --file /absolute/path/to/completed-result.json \
  --captured-at 2026-11-03T00:00:00Z \
  --output /absolute/path/to/new-metadata-results.jsonl

python3 -m monitoring_ai sources create \
  --output /absolute/path/to/new-private-sources.json \
  --file /absolute/path/to/new-metadata-results.jsonl \
  --id cli-reviews --format claude-results --project example-project

python3 -m monitoring_ai sources check --sources /absolute/path/to/new-private-sources.json
python3 -m monitoring_ai sources preview --sources /absolute/path/to/new-private-sources.json
python3 -m monitoring_ai serve --sources /absolute/path/to/new-private-sources.json \
  --db /absolute/path/to/private-ledger.sqlite
```

Capture reads one bounded regular file and creates a new metadata-only JSONL file with mode 0600. Existing outputs and final symlinks refuse. It discards result text, prompts, tool arguments, arbitrary native metadata and top-level aggregate totals. No model request, hook, credential or ledger is created by capture.

An application that already receives results can write the same frames in complete, ordered history. The closed wrapper has `schema: monitoring.claude-result.v1`, `captured_at` and `result`. The result contains native `type: result`, `session_id` and `modelUsage`. Each model requires `inputTokens`, `cacheReadInputTokens`, `cacheCreationInputTokens` and `outputTokens`. Optional `costUSD` supplies a cumulative estimate. Unknown native fields are discarded. The private source can contain opaque native session IDs, which are hashed before ledger storage.

## Accounting and coverage

The adapter emits disjoint token deltas per session and raw model key. It uses the per-model totals once, ignoring overlapping top-level `usage` and `total_cost_usd`. It does not add thinking tokens to output. Known cumulative cost differences remain `estimated`, in USD, with `claude-cli-client-estimate` provenance. Missing cost stays unknown. A positive cost-only difference is retained with zero tokens.

Identical totals add no event. Decreasing counters or costs, disappearing models, reversed receipt times, changed provider identity and ambiguous changes between known and unknown costs refuse. New session IDs start independent histories. Fixed per-model event positions make removed accepted captures conflict with ledger history rather than recounting their usage in a later cumulative total. Reuse the same source alias and keep the complete selected history. There is no automatic rotation or correction protocol.

The first capture can summarize earlier work in that session. Later deltas cover work between captured totals. Their timestamps are declared receipt times, not individual request times. Usage-record counts are summary observations, not the number of API calls. No per-call duration, tool activity, child hierarchy or lifecycle is inferred. Even a successful result does not create a completed-worker status.

These summaries are excluded from short-window pace and forecasts. Period totals still include their recorded tokens and estimates, grouped by receipt date, and can contain activity from before that date. Snapshot and report coverage count those summaries explicitly. This prevents a newly captured conversation total from appearing as a burst of recent calls.

Provider `firstParty` maps to `anthropic`. An absent or unrecognized provider remains `unknown`. Model keys are normalized as display aliases. Client estimates remain estimates even when capture uses a paid API result. Prefer one observation route for a conversation. Importing the same work through transcript and result adapters under different aliases can double-count it.

The existing source checks, disposable ingestion preview, read-health/coverage, filters and history views apply to this format. A readable old file remains old evidence. Neither polling nor replay creates new usage or establishes a live worker. Actual session directories and interactive-session compatibility remain outside this qualification.

Qualification used one actual completed, authorized project-review result from Claude Code 2.1.308. Capture, parsing, preview, replay, read API, consumer validation and the read-only dashboard passed while preserving the original receipt time. Token buckets matched and only estimated cost evidence entered the private ledger. No private values or native result body are included in public examples. This does not qualify interactive session transcripts, continuous result producers or other CLI versions.
