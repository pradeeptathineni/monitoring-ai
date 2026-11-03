# Runtime-owned observations

`monitoring_ai.producer` helps a runtime write the metadata it already owns. It creates a new private normalized JSONL file. It does not discover processes, run commands, register hooks, read sessions or control work.

The runtime supplies observation IDs, times, identities and states. For a process it owns, it can record a running observation after checking that process, supply further observations while it remains active, and record its terminal outcome after exit. A successful read of a file or HTTP endpoint is not a heartbeat from that process.

## Write a selected stream

This example uses fictional values. Replace them with facts from the runtime's own observer, keeping its original observation time:

```python
from monitoring_ai.producer import EventWriter, session_alias, status_event

session = session_alias("fictional-native-session")
event = status_event(
    id="example-run-1-observation-1",
    source="example-runtime",
    project="example-project",
    session=session,
    entity="example-worker",
    parent=session,
    entity_type="worker",
    state="running",
    timestamp="2026-11-03T01:00:00Z",
)
with EventWriter("/absolute/private/new-events.jsonl") as stream:
    stream.write(event)
```

Select the new file through the existing interface:

```sh
python3 -m monitoring_ai sources create \
  --output /absolute/private/new-sources.json \
  --file /absolute/private/new-events.jsonl \
  --id runtime-file --format normalized
python3 -m monitoring_ai sources check --sources /absolute/private/new-sources.json
python3 -m monitoring_ai serve --sources /absolute/private/new-sources.json \
  --db /absolute/private/ledger.sqlite
```

The example worker's parent remains unobserved until the owner supplies an observation for that parent. No parent or state is invented by the reader. An owner that knows a conversation identity but not its lifecycle can explicitly report the conversation as unknown.

`EventWriter` refuses existing files, final symlinks and missing parent directories. It creates a regular file with mode 0600, validates every event before writing, serializes its threads and flushes complete records. The stream admits at most 32 MiB and 50,000 records. Reaching a limit leaves the accepted file unchanged. It has no automatic rotation or retention policy. Use one owner writer per stream and close it when finished.

Observation IDs are unique within the event source, including across sessions. Keep the same ID, timestamp and content when retrying an observation. An identical replay is counted once. Changed content under an accepted ID refuses the attempted import. A write failure is reported to the owner and closes the writer if bytes may be incomplete. The runtime owns its recovery and job policy. Monitoring does not cancel work because observation failed.

`EventWriteError` reports `outcome="unknown"`. A partial final line or a complete unacknowledged record may already exist. The writer does not truncate observations that a reader may have accepted. Preserve the stream and exact observation when inspecting or replaying it. Do not automatically retry with a fresh ID or timestamp.

Only the closed normalized metadata schema is accepted. Paths, PIDs, commands, prompts, output bodies and arbitrary metadata do not belong in events. Source and project aliases can still reveal operational information, so keep real streams and ledgers private.

## Relate lifecycle and usage

`session_alias(native_id)` uses the same hashed identity as the Claude result adapter. Use that relation only when the runtime independently knows that both observations belong to that exact native session. A matching project or model name is insufficient.

Native identities are opaque and matched exactly, including case and whitespace. The helper does not normalize UUID spelling or guess another identity. Use the exact value agreed with the runtime.

For a mixed manifest, the lifecycle input can have its own managed-file alias while its normalized `source` matches the `claude-results` input ID. Both must use the same project and hashed session. Conversation totals stay on the session entity. Do not copy cumulative usage onto each worker, which would count it again.

A caller observing one finite CLI invocation can report its worker's exit outcome while leaving the conversation lifecycle unknown. Completed means the reported execution ended. It does not establish task acceptance, correct output or the state of a later invocation. Missing heartbeats let nonterminal evidence age to unknown after 60 seconds. File polling never advances its timestamp.

The Sessions view orders parents before descendants within the exact source/session identity. Expand and collapse affect only the current view. Missing parents, filtered parents and truncated lists remain explicit. These observations do not provide an execution or cancellation target to Deep.

Existing runtime exporters, including Maestro's passive export, retain their own authority. This helper supplies the normalized write boundary for other explicitly integrated owners. It adds no global discovery service or account connection.

Qualification observed this task's actual authorized Claude review process. Its private stream showed running, six heartbeat observations and the completed process outcome through the API and independent read-only dashboard. Its conversation usage joined on the verified native identity and remained on the parent, with no worker cost duplication. This is one explicitly integrated owner process, not discovery of all native sessions or provider-internal workers. No real values or native identifiers are included in public examples or images.
