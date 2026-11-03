# Capture a completed OpenAI API response receipt

The capture command reads one explicitly selected saved Responses API object locally and publishes a new private normalized JSONL record. It makes no API request and reads no credentials. The existing API application must already have an authorized response to observe.

```sh
python3 -m monitoring_ai sources capture-openai-response \
  --file /private/completed-response.json --output /private/api-metadata.jsonl \
  --source-id api-receipts --project example-project --session application-group
python3 -m monitoring_ai serve --file /private/api-metadata.jsonl \
  --db /private/api-ledger.sqlite
```

`--session` is an explicit application grouping alias. It is not an inferred provider conversation or worker. One usage event is identified by a hash of the response ID. The record retains model, disjoint token buckets, original completion time and server-reported creation-to-completion duration. Instructions, input/output bodies, metadata, user identifiers, payer information, previous-response links and raw response IDs are omitted. The new output is mode 0600 and existing paths refuse unless `--append` is explicitly selected.

This first supported shape requires `object: response`, `status: completed`, integer `created_at` and `completed_at`, and usage totals with both `cached_tokens` and `cache_write_tokens` details. Missing or inconsistent counters, unsupported statuses and absent completion times refuse. Incomplete/failed responses can still consume tokens, but are not covered by this initial completed-receipt adapter. No source completion is translated into normalized session lifecycle.

Ordinary input is total input minus cached reads and cache writes, following [OpenAI's documented disjoint cache accounting](https://developers.openai.com/api/docs/guides/prompt-caching). Reasoning tokens are already part of reported output and are not added again, as shown in [OpenAI's reasoning usage documentation](https://developers.openai.com/api/docs/guides/reasoning). Total input plus output must match `total_tokens`. Cache TTL subdivisions and tool/image charges are not modeled here.

The response does not establish a billed amount. Capture adds no cost; an owner may apply a separately supplied dated price book as an estimate. Unknown cost remains explicit. Re-reading the same normalized event adds zero; changing an accepted event ID's payload refuses rather than duplicating a request. Keep one stable source/grouping convention for the producer and do not aggregate overlapping collectors.

## Capture another receipt into the selected source

Create a private source selection once, then start an owner that follows it:

```sh
python3 -m monitoring_ai sources create --output /private/api-sources.json \
  --file /private/api-metadata.jsonl --id api-input --format normalized
python3 -m monitoring_ai sources check --sources /private/api-sources.json
python3 -m monitoring_ai serve --sources /private/api-sources.json \
  --db /private/api-ledger.sqlite
```

After another ordinary authorized response is saved, explicitly capture it into the same file:

```sh
python3 -m monitoring_ai sources capture-openai-response --append \
  --file /private/another-completed-response.json --output /private/api-metadata.jsonl \
  --source-id api-receipts --project example-project --session application-group
```

The owner imports complete new lines on its normal polling cycle. Deep can read those changes through its existing snapshot consumer after its owner deliberately selects this source. Neither command changes an already running owner's selection.

Append requires an existing owned, regular mode-0600 file containing complete validated records, within the 32 MiB and 50,000-record limits. A nonblocking exclusive lock prevents simultaneous append captures from racing. An identical response returns `events: 0, replayed: 1` without changing the file. A changed response payload or changed source/project/group alias for the same response hash refuses without writing. Keep aliases stable across captures. This file-local check cannot detect the same request in a separate collector or file.

A failed write can leave an incomplete record or an uncertain completed append. The command exits with an explicit unknown-outcome message. Preserve the file and inspect it before retrying. Incomplete final lines refuse further append rather than being erased or repaired automatically. Missing append targets also refuse. Use the default new-file capture for the first receipt.

## Recorded receipts versus ongoing capture

Actual authorized project-review receipts were qualified through initial capture, explicit append, replay, owner refresh and the accepted Deep reader/hook/component in an isolated preview. Their original completion times and unknown costs were preserved, and no new API call or active-worker claim was made. These are recorded completed request observations. Capture time is not their completion time. They do not establish a current session roster, complete API/account usage or sustained unattended capture. The managed Deep owner was not changed.

## Record an ordinary application response directly

An application that already receives a supported completed response dictionary can record its metadata immediately. No intermediate raw response file is required. With Monitoring available in that application's Python environment:

```python
import logging
from pathlib import Path
from monitoring_ai.openai_receipt import record_response

# data is the response already returned by an authorized application request
try:
    record_response(data, Path("/private/api-metadata.jsonl"),
                    source="api-receipts", project="example-project",
                    session="application-group")
except (OSError, ValueError):
    logging.warning("Monitoring receipt unavailable or uncertain; inspect the private sink")
```

Invoke this once after each ordinary response arrives. It creates a new private sink or uses the same checked append/replay path for an existing one. The caller selects an existing private directory and stable aliases. The application can continue returning its already received response if observation fails. Do not repeat the API request because metadata capture failed, and do not log the raw response or exception body. An unknown write outcome needs private file inspection before any observation retry.

This helper makes no provider request, reads no credentials, installs no callback or global hook and starts no thread or background process. Selecting its output in Monitoring lets the existing owner and Deep consumer refresh after later ordinary calls. Actual ongoing provider emission is unqualified until the owning application deliberately adopts the helper and observes a new authorized response. The in-memory application path has synthetic sequential-response, replay, privacy and conflict validation. The earlier actual receipts qualify saved-file capture and receiving only.

`response_event` remains available for a runtime that owns its own event publication. Keep one accounting route per request scope. Deep source adoption and any persistent host installation remain separate owner/access decisions.
