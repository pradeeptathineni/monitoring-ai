# Settings ownership and timing

The standalone **Settings** destination is `/#settings`, with contextual Appearance and Sources categories. Overview contains monitoring evidence and contextual filters. Persistent appearance choices and source-setup guidance live in Settings. Existing view hashes and theme preferences remain compatible.

| Item | Owner and source | Default and effect | Persistence |
| --- | --- | --- | --- |
| Appearance | Shared `@deep/theme` validation and palettes, Monitoring browser adapter | System scheme, then an explicit validated selection or overrides. Changes apply immediately. | Monitoring-specific browser storage on this origin. No automatic application-to-application sync. |
| Source selection | Monitoring CLI and explicit `monitoring.sources.v1` manifest | No source discovery. Selected files load at server startup. Restart to change selection. | Private manifest created without overwrite. The browser displays aliases and setup guidance, not paths or a write editor. |
| Currency, prices and FX | Explicit CLI options and dated books, with reported event costs taking precedence | USD unless explicitly selected. No prices or rates are fetched silently. A new server invocation observes new options. | Operator-supplied files and launch options. A preference never grants pricing authority or converts a subscription allowance into money. |
| Provider quota collection | Explicit launch option and existing supported provider interface | Unconfigured unless selected. Polling neither starts inference nor grants new access. | Provider authentication remains in its existing owner. Monitoring creates no credentials or account store. |
| Quota reserve watch | Monitoring browser adapter over supplied quota windows | Off by default. Save one exact owner/source/provider/account alias/window/provenance and a remaining threshold from 0 to 100 percent. Applies immediately. Stale, missing, ambiguous, paused, reset-passed or changed-owner observations are unknown. | This origin's browser storage, with a page-only fallback when storage fails. No account action, collection permission or background notification. |
| Usage filters and grouping | Current dashboard controls or CLI invocation | All supplied observations within the requested selection | Contextual view state, with no new saved-filter preference in this release. |

Settings groups existing owner controls. It does not add a cross-system configuration registry, a browser source-write endpoint or new inheritance rules. Deep should retain its existing capability discovery and owner projections. Shared appearance uses the existing theme package. A future editable Monitoring projection must expose actual scope, defaults, validation, source and timing before mounting it elsewhere.

Ordinary appearance choices are separate from credentials, filesystem access, executable trust, consent, execution and spending. Saving a theme does not connect a source. Creating a source manifest selects explicit paths but starts no collector until the operator launches the server. A read-only setup check creates no ledger and does not accept events into history. An ingestion preview uses a private disposable ledger copy, preserving accepted history.

Invalid appearance input preserves the active valid choice. Storage failure preserves a usable page with a visible persistence limit. Source configuration refuses unknown fields and overwrites, and failures retain already accepted ledger history. Existing qualified costs and lifecycle meanings do not change with settings.
