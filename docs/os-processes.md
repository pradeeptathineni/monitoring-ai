# Local OS process observations

On macOS, `serve --processes` opts into a read-only sample every two seconds. It runs `/bin/ps` with fixed `pid`, `ppid`, `%cpu`, `rss`, `lstart` and `comm` columns. It does not request `args`, `command`, environment, ports, file handles, transcripts or credentials. The reader keeps only executable **basenames** matching `codex`, `claude`, `CodeBurnMenubar`, `opencode`, `aider`, `gemini` or `goose`; all other rows and every executable path are discarded before creating a snapshot. No auto-discovery or account connection occurs when the flag is absent.

```sh
python3 -m monitoring_ai serve --db /path/to/private-ledger.sqlite --processes
```

The separate `os_processes` snapshot block has schema `monitoring.os-processes.v1`, state `unread`, `following` or `unavailable`, `sampled_at`, `processes`, `no_longer_observed`, `changed_count`, `truncated` and `coverage`. Process rows carry PID, parent PID, executable basename, UTC start time, recent OS CPU percentage and resident bytes. `session` and `worker` are always null. PID plus start time distinguishes a replaced process from a continuing one. An absent PID in a later successful sample means **no longer observed**; it is not a session-completion event. A failed read clears current rows rather than presenting retained process presence as live. At most 200 current and 200 disappearance rows are returned, with truncation disclosed.

The Sessions view shows this block separately from ledger entities and CodeBurn transcript-write output. Event filters, tokens, costs and ledger revision do not apply. A process can be idle, have multiple sessions, or not correspond to any recorded agent. Child PID does not prove a worker relationship. The browser labels stale, paused or disconnected snapshots as unknown current presence. Deep consumers may ignore the optional block; the existing read fields and event contract are unchanged.

The initial qualification used the actual macOS `/bin/ps` interface and a task-owned short process. Two samples observed its appearance and later absence, without inspecting its arguments. This does not prove all AI executables are discoverable: many CLIs use generic names such as `node`, which are deliberately excluded because their purpose cannot be inferred safely from executable name alone. This observer does not monitor remote/cloud processes. CPU is OS-reported recent utilization, not tokens or time spent by an agent.
