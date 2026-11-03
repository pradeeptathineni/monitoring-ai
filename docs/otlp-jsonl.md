# Selected OTLP trace export

Monitoring accepts an explicitly selected local file containing one compact [OTLP/JSON `ExportTraceServiceRequest`](https://github.com/open-telemetry/opentelemetry-proto/blob/main/opentelemetry/proto/collector/trace/v1/trace_service.proto) per newline. A producer or collector must already export that file with the owner's authorization. Monitoring does not install instrumentation, receive network traffic, read environment credentials or contact an OTLP endpoint.

Use a private file and a project alias:

```sh
python3 -m monitoring_ai sources create \
  --output /absolute/private/otlp-sources.json \
  --file /absolute/private/trace-batches.jsonl \
  --id selected-traces --format otlp-jsonl --project my-app
python3 -m monitoring_ai sources check --sources /absolute/private/otlp-sources.json
python3 -m monitoring_ai sources preview --sources /absolute/private/otlp-sources.json
python3 -m monitoring_ai serve --sources /absolute/private/otlp-sources.json \
  --db /absolute/private/ledger.sqlite
```

`check` reads the selected file without changing history. `preview` uses a disposable ledger. Each line must be a complete JSON object; while following, an unfinished final line waits for the next read. The existing 32 MiB file and 50,000-line bounds apply, with 5,000 spans per batch. A producer should rotate/export new bounded files deliberately; this adapter does not install a collector or discover files. Source health reports the last accepted read and event age. File age and old completed spans do not establish a running worker.

The adapter reads only completed trace spans with these [GenAI semantic conventions](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/client-inference.md): `chat`, `generate_content`, `text_completion`, `embeddings` operations with explicit token attributes. Input is the reported total minus cache-read and cache-write subsets; output includes any reasoning already in the reported output total. Missing counters remain zero and impossible subsets refuse the batch. `invoke_agent` produces an agent invocation end observation, `execute_tool` a completed tool observation. Their status is scoped to those spans. A completed agent span does not mean the whole user task succeeded, and a tool span is not a worker. Workflow-level aggregate counters are excluded to avoid counting child inference twice. No generic session-start, current process, remote worker, billing amount or quota is inferred.

Trace and span IDs become stable opaque hashes; request/response bodies, span names, arbitrary attributes, resource names, raw identities and paths are discarded before projection reaches the ledger. Provider, model and tool labels must be short identifiers or become `unknown`/`tool`. Cost is unknown unless a separate explicit dated price book supplies an estimate; no billed cost is projected. OTLP exports can overlap native Claude/Codex records or another collector. Select one authoritative accounting route per activity, and review previews before importing. Cross-format overlap detection is not yet automatic.

This first file adapter implements a useful subset of the stable [OTLP trace JSON wire format](https://github.com/open-telemetry/opentelemetry-proto/blob/main/docs/specification.md#json-protobuf-encoding) and the still-developing GenAI span conventions, inspected on 2026-11-03. It has synthetic end-to-end qualification only; no authorized real OTLP export was available in this task. Distributed causal hierarchy, partial-success receiver semantics, exporter retry policy, metrics/logs/profiles and live remote collection remain outside this adapter.
