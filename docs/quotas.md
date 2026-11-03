# On-screen quota reserve watch

Open **Settings → Thresholds → Quota reserve watch**. Choose one supplied quota window, enter the remaining percentage at which you want a warning, and save. The suggested value is 2 percent, but no watch is active until saved. One watch is supported in this browser.

The preference pins the Monitoring owner identity and exact source, provider, account alias, window ID, duration and provenance. It compares only the source-reported remaining percentage. It does not calculate subscription balance from token use or convert quota into money. Account aliases are supplied labels, not a new provider-account identity check.

A current reading at or below the threshold shows a banner. The Settings status also shows readings above the threshold. Missing/ambiguous windows, changed owner, stale/future/reset-passed observations, unavailable or reading sources, paused refresh and hidden pages make the watch unknown. Captures must be within the existing five-minute quota freshness interval. A passed reset time never implies replenishment. Review and save again after changing owners.

Save applies immediately and stores the preference only on this browser origin. Another tab on the same origin receives preference changes. A storage failure leaves a page-only watch with an explicit message. Invalid saved input leaves the watch inactive until a valid choice is saved. Disable removes the local preference, with a visible limit if browser storage cannot be changed. Configuration stays in Settings while the warning is visible in the ordinary dashboard.

This is an on-screen watch while the page is open, with no desktop/message notification or background browser task. It never resets quota, buys credits, enables overage, starts a collector or blocks requests. Source collection remains under the existing explicit launch options such as `--quota-file` or `--codex-quota`. No new HTTP field or Deep owner contract is required.

A finite actual native Codex weekly-quota trial qualified saving, reload and pause-to-unknown behavior without ledger activity or a model call. Its trial threshold was deliberately set above the observed remaining allowance to exercise the warning. That is not evidence that the ordinary 2 percent reserve was reached. Real account values remain in private evidence; public tests use fictional quota windows. Actual Claude registry and API producer adoption gaps remain separate from this quota workflow.
