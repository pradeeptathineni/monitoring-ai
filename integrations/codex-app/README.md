# Finite app inventory refresh

`finite-refresh.js` runs inside an already authorized assistant tool runtime exposing `tools`, `text` and `setTimeout`. It is not an account client or a standalone OS daemon. It uses the supported app `list_threads` interface, selects only five metadata fields, and invokes Monitoring's existing private capture command. It never sends a message, starts a model, changes account access or reads native session directories.

After evaluating the file in that runtime, invoke:

```js
await refreshAppInventory({
  projectRoot: "/path/to/monitoring-ai",
  output: "/private/existing-app-inventory.json",
  samples: 3
});
```

The output must already be a valid owned 0600 inventory export created by `sources capture-app-threads`. The recipe refreshes it atomically. One to three samples are allowed, with ten seconds between reads. Each completed write emits only a capture timestamp and task count. The bridge stops after the requested sample count. An unsupported/error response, invalid metadata or failed publication stops the bridge, leaving the previous export to age to unknown. Duplicate identical pinned/recent entries are deduplicated; conflicting metadata refuses publication. Up to 100 selected Codex/ChatGPT entries are kept from the recent page and pinned observations; coverage remains explicitly incomplete.

This exact recipe was exercised through the supported live app tool for three captures while a finite headless Monitoring owner watched the export. All three capture times appeared in the UI as recent. No persistent process, scheduled task, credential or hook was installed. Sustained coverage after the finite run requires another explicitly authorized run or a separately approved persistent bridge. A standalone Monitoring process cannot call the assistant app tool directly.

The full inventory response may contain titles, summaries and paths. The recipe discards those fields before invoking any capture process. Only the selected ID is passed locally to capture, which hashes it before writing. Treat the resulting metadata export as private. App active/idle statuses remain separate from inference activity, normalized worker lifecycle, usage and costs. See [the source contract](../../docs/app-task-observations.md).
