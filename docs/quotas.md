# On-screen quota reserve watch

Open **Settings → Thresholds → Quota reserve watch**. Choose one supplied quota window, enter the remaining percentage at which you want a warning, and save. The suggested value is 2 percent, but no watch is active until saved. One watch is supported in this browser.

The preference pins the Monitoring owner identity and exact source, provider, account alias, window ID, duration and provenance. It compares only the source-reported remaining percentage. It does not calculate subscription balance from token use or convert quota into money. Account aliases are supplied labels, not a new provider-account identity check.

A current reading at or below the threshold shows a banner. The Settings status also shows readings above the threshold. Missing/ambiguous windows, changed owner, stale/future/reset-passed observations, unavailable or reading sources, paused refresh and hidden pages make the watch unknown. Captures must be within the existing five-minute quota freshness interval. A passed reset time never implies replenishment. Review and save again after changing owners.

Save applies immediately and stores the preference only on this browser origin. Another tab on the same origin receives preference changes. A storage failure leaves a page-only watch with an explicit message. Invalid saved input leaves the watch inactive until a valid choice is saved. Disable removes the local preference, with a visible limit if browser storage cannot be changed. Configuration stays in Settings while the warning is visible in the ordinary dashboard.

This is an on-screen watch while the page is open, with no desktop/message notification or background browser task. It never resets quota, buys credits, enables overage, starts a collector or blocks requests. Source collection remains under the existing explicit launch options such as `--quota-file` or `--codex-quota`. No new HTTP field or Deep owner contract is required.

A finite actual native Codex weekly-quota trial qualified saving, reload and pause-to-unknown behavior without ledger activity or a model call. Its trial threshold was deliberately set above the observed remaining allowance to exercise the warning. That is not evidence that the ordinary 2 percent reserve was reached. Real account values remain in private evidence; public tests use fictional quota windows. Actual Claude registry and API producer adoption gaps remain separate from this quota workflow.

## Dated quota history

Add `--quota-history /path/to/private-quota-history.sqlite` when starting an owner with `--codex-quota` or `--quota-file`. Its parent directory must already exist. A new history file is created mode 0600. Existing history must be an owned, private regular file with the supported schema. No existing file is overwritten or repaired. This opt-in stores alias-only percentages, window/reset times, source capture times and local recording times in a separate SQLite database. It does not add usage events or change ledger revision.

```sh
python3 -m monitoring_ai serve --db /path/to/events.sqlite \
  --codex-quota --quota-account local \
  --quota-history /path/to/private-quota-history.sqlite
```

**Usage and costs → Quota history** shows the recorded window, elapsed-time chart and dated table. Choose a different window, load older observations or return to the latest page. Chart pages contain at most 200 observations, with the latest 20 also in the table. A page pins its owner and history revision. When new observations arrive, an older page stays unchanged until explicitly refreshed. Ledger filters do not apply to quota history.

Scopes remain separate by source, provider, account alias, provenance, window ID and duration. An alias does not verify the logged-in account. When changing the underlying account, deliberately choose a new alias and history file. Equal source times and equal contents replay without adding another observation. Different contents at the same source time refuse the entire new book. Owner changes, failed reads, absent windows, gaps over three minutes, changed reset times, unavailable percentages and reported decreases break chart continuity. A reported decrease or passed reset time does not prove replenishment. Missing periods stay gaps rather than zero use. No exhaustion forecast, token conversion or currency estimate is derived from percentage history.

History recording stops at 10,000 observations or 100 exact window scopes. Earlier records are retained without automatic pruning. Stop the owner and deliberately select another file to start another history. A history write failure or capacity limit is separate from the current quota source: a successful source read can still supply the current allowance. Conversely, an old graph tail never supplies a current reading. Current allowance requires a matching, followed source, a numeric percentage, a capture within five minutes, an unpassed reset time and a connected, unpaused browser. No background notification is provided.

Without either quota source option, `serve --quota-history EXISTING_FILE` opens the history read-only. Mode 0400 or 0600 is supported for this recorded-only view. No account is polled, no missing file is created and current allowance stays unknown. The terminal reader is also read-only:

```sh
python3 -m monitoring_ai quota-history --db /path/to/private-quota-history.sqlite --json
```

Use `--series KEY`, `--limit 1` through `200`, and `--before TIME --expected-revision REVISION` to inspect an older page. Older pages require an exact series key. The command contacts no provider and prints no current account claim.

Local qualification used three finite native Codex allowance queries about 61 seconds apart. The observations reached the history API and headless desktop/mobile dashboard with an empty usage ledger. Source reads took 0.74–1.26 seconds and SQLite writes 2.0–2.7 ms on that workload. Native `captured_at` is local response receipt time, since the provider interface supplies no qualified upstream sample time. The finite collector and temporary owner stopped afterward. Actual account percentages and screenshots remain private. Public checks use synthetic windows. This does not qualify unattended collection, account-wide session visibility or managed Deep adoption.
