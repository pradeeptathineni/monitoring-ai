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

## Observe reported request status separately

`observe-openai-response` captures metadata from a response the caller has already received. It supports explicit `queued`, `in_progress`, `completed`, `failed`, `cancelled` and `incomplete` status reports. These observations create no token, cost or lifecycle records. They can describe responses that the completed-usage adapter cannot admit, including fractional creation timestamps or an absent completion time.

```sh
python3 -m monitoring_ai observe-openai-response \
  --file /private/received-response.json --output /private/request-observations.jsonl \
  --observed-at 2026-11-03T20:00:00Z \
  --source api-caller --project example-project --session application-group
python3 -m monitoring_ai openai-observations --file /private/request-observations.jsonl
python3 -m monitoring_ai serve --db /private/api-ledger.sqlite \
  --openai-observations /private/request-observations.jsonl
```

Supply the original caller receipt time with `--observed-at`. The example time is fictional. Add `--append` for another explicit receipt into the same private file. Exact replay writes nothing. A conflicting same-time receipt or a changed project, grouping alias, model or creation time for the same source/response identity refuses. A later explicit receipt can update that response's reported status without counting another inference. After an uncertain write, inspect the private sink before retrying observation. Never repeat the provider request to repair monitoring.

For an application that already holds a response dictionary, `monitoring_ai.openai_observations.record_observation(data, output, source=..., project=..., session=..., observed_at=..., append=...)` performs the same projection and publication without a raw intermediate file. Choose the first-write or append mode explicitly. It starts no provider call, hook or background capture.

The selected history is bounded to 5,000 frames, 100 source/response identities and the existing 32 MiB source-file limit. Each frame retains only the hashed response ID, explicit aliases, provider/model, reported status, original receipt time, nullable provider creation time and whether an error object was present. Raw response IDs, HTTP request IDs, content, error bodies and usage are discarded. Creation time stays separate from receipt time. An unknown creation time stays unknown.

Overview shows one latest receipt per source/response identity with an ephemeral status filter. Receipt freshness is current through 125 seconds, stale afterward and future when ahead of the browser clock. A successful reread never refreshes the receipt time. Historical terminal statuses remain dated reported outcomes. Stale, future, paused or unavailable nonterminal reports leave current request state unknown. Empty evidence means none captured in the selected file, not zero account use. The panel neither joins grouping aliases to provider conversations nor establishes a live worker roster.

The optional [owner-pinned read route](contract.md#optional-api-request-observations) retains the last accepted dated book after a failed file read. It adds no existing snapshot field or ledger revision. Deep adoption requires its owning workflow to review and admit this separate read contract. The standalone path has synthetic capture, replay, HTTP and browser checks. Actual ongoing provider emission and Deep presentation of this new route remain unqualified.
