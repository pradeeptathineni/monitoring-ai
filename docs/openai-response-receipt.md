# Capture a completed OpenAI API response receipt

The capture command reads one explicitly selected saved Responses API object locally and publishes a new private normalized JSONL record. It makes no API request and reads no credentials. The existing API application must already have an authorized response to observe.

```sh
python3 -m monitoring_ai sources capture-openai-response \
  --file /private/completed-response.json --output /private/api-metadata.jsonl \
  --source-id api-receipts --project example-project --session application-group
python3 -m monitoring_ai serve --file /private/api-metadata.jsonl \
  --db /private/api-ledger.sqlite
```

`--session` is an explicit application grouping alias. It is not an inferred provider conversation or worker. One usage event is identified by a hash of the response ID. The record retains model, disjoint token buckets, original completion time and server-reported creation-to-completion duration. Instructions, input/output bodies, metadata, user identifiers, payer information, previous-response links and raw response IDs are omitted. The new output is mode 0600 and existing paths refuse.

This first supported shape requires `object: response`, `status: completed`, integer `created_at` and `completed_at`, and usage totals with both `cached_tokens` and `cache_write_tokens` details. Missing or inconsistent counters, unsupported statuses and absent completion times refuse. Incomplete/failed responses can still consume tokens, but are not covered by this initial completed-receipt adapter. No source completion is translated into normalized session lifecycle.

Ordinary input is total input minus cached reads and cache writes, following [OpenAI's documented disjoint cache accounting](https://developers.openai.com/api/docs/guides/prompt-caching). Reasoning tokens are already part of reported output and are not added again, as shown in [OpenAI's reasoning usage documentation](https://developers.openai.com/api/docs/guides/reasoning). Total input plus output must match `total_tokens`. Cache TTL subdivisions and tool/image charges are not modeled here.

The response does not establish a billed amount. Capture adds no cost; an owner may apply a separately supplied dated price book as an estimate. Unknown cost remains explicit. Re-reading the same normalized event adds zero; changing an accepted event ID's payload refuses rather than duplicating a request. Keep one stable source/grouping convention for the producer and do not aggregate overlapping collectors.

## One receipt versus ongoing capture

One actual authorized project-review receipt was qualified through capture, ledger replay, owner snapshot and the accepted Deep reader/hook/component in an isolated preview. Its original completion time and unknown cost were preserved, and no new API call or active-worker claim was made. This is one completed request observation, not a current session roster, complete API/account usage or sustained capture. The managed Deep owner was not changed.

For ordinary future calls, the owning application can call `response_event(response_dict, source=..., project=..., session=...)` after receiving a supported completed result and pass the validated event to its explicitly selected `EventWriter`. That hooks into the application's existing authorized response path without another API call or credential. This task does not modify that application's scripts, install global hooks or start a persistent collector. Deep can consume the resulting explicitly selected normalized file through its existing contract after deliberate owner/source adoption.
