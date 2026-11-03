# Capture selected local Codex runtime metadata

This experimental command connects once to an explicitly selected existing local app-server socket. It starts no Codex daemon, opens no TCP listener and makes no model request. The client sends only `initialize`, `initialized` and a project-filtered `thread/list` with `useStateDbOnly: true`. It never resumes threads, reads turns/items or falls back to JSONL repair.

After the relevant host and connection scope have been reviewed:

```sh
python3 -m monitoring_ai sources capture-codex-host \
  --socket /private/owned-host/control.sock --cwd /authorized/project \
  --output /private/codex-runtime.json
python3 -m monitoring_ai serve --app-threads /private/codex-runtime.json \
  --db /private/monitoring.sqlite
```

The socket must already exist, belong to the current user and have mode 0600 in an owned mode-0700 directory. The command changes no permissions and discovers no alternate endpoint. The host must actually own the work being observed. Starting an empty daemon does not establish visibility into another app-server or the desktop app.

The initial protocol gate accepts only a server `userAgent` containing version 0.160.0, whose generated schema was inspected. Other or missing versions refuse before listing. Returned thread `cwd` must exactly match the canonical selected directory. Unknown/unsupported requests fail without fallback. These checks restrict the client, but the underlying server socket is not a server-issued read-only capability. Approve any new long-running access separately.

One page contains at most 20 threads. Export retains only hashed identity, source update time, local host class and active/idle/unknown status. Names, previews, working directories, providers and other response fields are discarded locally. `notLoaded`, `systemError` and other statuses become unknown. No session/process linkage, lifecycle completion, token usage, cost or ChatGPT coverage is inferred. This source has a distinct alias namespace from the app inventory.

The new export is mode 0600. Repeat the capture command with `--replace` only to refresh an existing private export of this same source. The command ends after one page and closes only its own proxy child, leaving the Codex host running. Reads have a five-second deadline and a bounded response buffer. A failure leaves the prior export unchanged. Captures age to unknown after 30 seconds, so a single successful capture is not an unattended refresh service.

The optional snapshot block retains schema `monitoring.app-threads.v1`, with the additional exact pair `source: codex-local-app-server` and `scope: selected-cwd-page`. It remains separate from ledger metrics. Strict Deep consumers need the corresponding receiving change before reading this variant. An isolated patch is supplied to Deep's owner; managed Deep has not adopted it.

The [official app-server reference](https://developers.openai.com/codex/app-server) documents the metadata request and database-only option. The installed CLI 0.160.0 schema confirms the parameter. The default daemon socket was absent during qualification. Protocol fixtures and standalone/isolated receiving views are checked; an actual permitted live host is still required for real-source qualification. No persistent host, credentials or denied session-path access was created.
