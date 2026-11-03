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

## Bounded Codex history view

To create a new private export from one non-archived Codex history page:

```js
await refreshAppInventory({
  projectRoot: "/path/to/monitoring-ai",
  output: "/private/codex-app-history.json",
  samples: 1,
  view: "codex-history",
  replace: false,
});
```

This profile calls only `list_threads({mode: "history", source: "codex", limit: 50, archived: false})`. It neither follows the next cursor nor reads messages. It selects the same five fields and publishes the distinct `codex-app-task-history` / `codex-history-page` source/scope pair. Use `replace: true` for an existing export of this same profile. With multiple finite samples, only the first can create; later ones replace. The recent profile remains the default and retains its existing Codex/ChatGPT scope.

The standalone view pages captured rows in groups of 20, filters kind/current status/private alias and orders active observations first, then source update time. UI paging does not query more provider history. A stale or paused capture makes every current status unknown, including for the active filter. Aliases remain stable across the recent and history app profiles, but never establish a worker/session/process relationship. Captures age normally after this finite recipe exits.

This history profile covers one Codex host page only. Older pages, other hosts and ChatGPT history remain unobserved. A real Codex page passed the capture-to-standalone and isolated prepared Deep reader/hook/component workflow. The native runtime-source profile and this profile require their paired enum values admitted by strict Deep consumers before owner adoption. No managed owner or persistent bridge was changed.
