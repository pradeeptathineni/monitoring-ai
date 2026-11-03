# Local OS process observations

On macOS, `serve --processes` opts into a read-only sample every five seconds by default. It runs `/bin/ps` with fixed `pid`, `ppid`, `%cpu`, `rss`, `lstart` and `comm` columns. It does not request `args`, `command`, environment, ports, file handles, transcripts or credentials. The reader keeps only executable **basenames** matching `codex`, `claude`, `CodeBurnMenubar`, `opencode`, `aider`, `gemini` or `goose`; all other rows and every executable path are discarded before creating a snapshot. No auto-discovery or account connection occurs when the flag is absent.

```sh
python3 -m monitoring_ai serve --db /path/to/private-ledger.sqlite --processes
# Optional: --process-interval 2 through 60 seconds
```

The separate `os_processes` snapshot block has schema `monitoring.os-processes.v1`, state `unread`, `following` or `unavailable`, `sampled_at`, `processes`, `no_longer_observed`, `changed_count`, `truncated`, `interval_seconds` and `coverage`. Process rows carry PID, parent PID, executable basename, UTC start time, recent OS CPU percentage and resident bytes. `session` and `worker` are always null. PID plus start time distinguishes a replaced process from a continuing one. An absent PID in a later successful sample means **no longer observed**; it is not a session-completion event. A failed read clears current rows rather than presenting retained process presence as live. At most 200 current and 200 disappearance rows are returned, with truncation disclosed.

From v0.8.26 the block also includes optional `history` and `history_truncated`. It holds the latest 60 **successful** samples in memory for this owner process, each with its original `sampled_at`, matched-process `count`, combined OS `cpu_percent`, combined `rss_bytes` and a `segment` number. The history contains no PID or executable identity. A failed read clears current presence but keeps prior samples as history; the next success starts another segment. A missed interval longer than 2.5 times the configured cadence (at least ten seconds) also starts a new segment. The Overview charts do not connect those gaps. Old owners can omit the fields; restarting the owner clears this in-memory history. A line between successful samples is only a sampled trend, not continuous execution or a live session.

The Sessions view shows current process rows separately from ledger entities and CodeBurn transcript-write output. Overview shows bounded count, CPU and memory trends. Event filters, tokens, costs and ledger revision do not apply. A process can be idle, have multiple sessions, or not correspond to any recorded agent. Child PID does not prove a worker relationship. The browser labels stale, paused or disconnected snapshots as unknown current presence. Deep consumers may ignore the optional block; the existing read fields and event contract are unchanged.

From v0.8.27 Overview also compares a **recent** Claude or Codex executable-name sample with the accepted read age of explicitly selected native files of that format. It can show that an executable is present while selected input events are historical, unread or unavailable, or that no file of that format is selected. It compares source types only: the file could be unrelated to the process, and a recent event does not prove a relationship either. The comparison disappears when the OS sample is stale, paused or disconnected. Source setup remains an explicit user choice in Settings. This view creates no new event, directory scan or process/session identity link.

The initial qualification used the actual macOS `/bin/ps` interface and a task-owned short process. Two samples observed its appearance and later absence, without inspecting its arguments. This does not prove all AI executables are discoverable: many CLIs use generic names such as `node`, which are deliberately excluded because their purpose cannot be inferred safely from executable name alone. This observer does not monitor remote/cloud processes. CPU is OS-reported recent utilization, not tokens or time spent by an agent.

At the measured five-sample median of 88.642 ms per read on one Mac, a two-second cadence occupies about 4.4% of elapsed time and a five-second cadence about 1.8%. These are wall-time duty ratios, not measured CPU utilization or a general performance guarantee. The default was changed to five seconds; the browser continues to poll snapshots every two seconds and labels old or paused process samples as unknown current presence. Set `--process-interval` to a whole number from 2 to 60 only with `--processes`; longer intervals deliberately create wider periods of unknown current presence.

## Inspect one process

In Sessions, filter the current process table by executable text or PID and order it by CPU, resident memory or executable name. These controls affect only the process table. Ledger filters and the Overview aggregate remain separate.

Choose **Inspect** to see CPU and resident-memory samples for that exact PID and OS start time. The browser retains up to 60 distinct successful samples per visible process identity, plus the selected identity if it disappears. At most 201 identities are retained. Repeated reads of the same source sample add no point. Failed, stale, missed or paused observations leave chart gaps. A reused PID with another start time is a different identity and does not extend the old chart.

A process absent from a later successful sample is labeled no longer observed. Stale, paused or unavailable reads leave current presence unknown. Neither state implies session completion. Resource values are OS observations, not token usage or model inference. The view keeps the existing unknown session/worker relationship explicit.

History stays in this browser page only. Reloading or changing owner identity clears selection and samples. This adds no durable process history, source access, server polling, read endpoint or API field. It uses the owner's existing sampling interval.

One actual permitted macOS trial qualified the selected-process view across two distinct OS samples, memory ordering and the private identifier-redacted image. Ledger calls and active entities stayed zero. No provider request or process/session relationship was introduced. This checks one short local workflow, not long-duration retention, general resource overhead or model activity.
