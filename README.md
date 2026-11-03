# Monitoring AI

Local monitoring for AI sessions, agents, workers, tokens and costs. Use it in your terminal, open its web dashboard, or consume its read-only API from another workspace.

**Experimental alpha.** It observes explicitly supplied sources. Personal source compatibility, additional collectors and full-vision acceptance remain incomplete. [Version policy and v1.0.0 requirements](docs/release-readiness.md) keep maturity separate from version numbers.

Current checked version: **v0.8.17**. The example image below uses fictional data from the earlier `0.8.0a1` checkpoint.

![Monitoring AI dashboard with fictional sessions and costs](docs/observatory.png)

## Try it

Python 3.11 or later on macOS or Linux. The runtime uses only Python's standard library. No API key, model call, telemetry or account is needed.

```sh
git clone https://github.com/pradeeptathineni/monitoring-ai.git
cd monitoring-ai
python3 -m monitoring_ai serve --demo --db /tmp/monitoring-demo.sqlite
```

Open **http://127.0.0.1:5274**. The demo creates fictional events in an empty database. Choose a new database path to seed another demo. Stop with Ctrl-C. For an installed CLI, use `python3 -m pip install .`, then `monitoring-ai` in place of `python3 -m monitoring_ai`.

For an actual live demonstration, run these in separate terminals. Choose a new JSONL file and database:

```sh
python3 -m monitoring_ai demo /tmp/monitoring-live.jsonl --stream
python3 -m monitoring_ai serve --file /tmp/monitoring-live.jsonl --db /tmp/monitoring-live.sqlite
python3 -m monitoring_ai top --db /tmp/monitoring-live.sqlite --watch
```

The producer appends usage and lifecycle evidence. The server imports complete lines every two seconds. The terminal and web views read the same ledger. Stopping the producer lets its running evidence expire after 60 seconds.

## What works

- Session, agent and worker identities with parents, recorded chronology, tool outcomes and lifecycle evidence that ages independently of usage.
- [Runtime-owned observations](docs/runtime-producers.md) through a private producer helper, with scoped parent/child ordering and local expand/collapse in Sessions. Actual qualification covers one explicitly owned review process.
- Web and terminal views with provider, model, surface, project, source, session, state and UTC date filters. The web view exposes provider/model/surface/project/date and state controls.
- Token breakdowns for uncached input, cache reads, cache writes and output. Codex reasoning is already part of output and is never counted twice.
- Usage and costs grouped by model, provider, surface, project and UTC date. Estimated and billed subsets retain provenance. Unpriced and unconverted records stay explicit.
- Dated model price books and explicit currency conversion. USD is the default. No exchange rate or provider price is silently fetched.
- Atomic SQLite imports, replay deduplication, changed-identity refusal, hierarchy validation and retained observations when a followed source fails.
- A versioned read contract and a [server-side Deep AI consumer](integrations/deep-ai). Deep integration is supplied for review, not deployed by this repository.
- Source-age views, trailing-window token pace and cautious linear forecasts with observed span, sample count and explicit cost coverage.
- Independent quota observations from an explicit file or an opt-in installed Codex allowance read. Stale values and passed reset times retain their last supplied values.
- A detailed Report view with UTC period presets, adjacent equal-length comparisons, project/model/day breakdowns and categorized tool activity. Unknown timing and unsupported workflow measures stay explicit.
- An explicit multi-file manifest with independent input health, replay counts and retained history when one source fails. Input aliases and event-source filters remain distinct.
- [Entity history](docs/session-history.md) with chronological pages scoped to its source and session. Pages retain one ledger revision, with an explicit restart when history changes.
- Opt-in [macOS process samples](docs/os-processes.md) with executable name, PID/parent PID, CPU, resident memory and start time. These remain separate from session and worker status.

## Connect your data

On macOS, add `--processes` to `serve` to observe matching local AI executable names without reading command arguments or environment. The default sample interval is five seconds; `--process-interval 2` through `60` adjusts it. Overview and Sessions keep those OS samples separate from recorded usage and entities. See [coverage and limits](docs/os-processes.md).

For an existing CodeBurn v74 daily cache, use the [explicit cached-usage view](docs/codeburn-cache.md). Its replaceable daily totals and source-reported USD amounts stay separate from event accounting. The cache cannot establish live workers or billing evidence.

An existing CodeBurn 0.9.27 menubar status file can be selected through the [optional recent-activity view](docs/codeburn-status.md). It reports source-generation time and recent transcript writes without reading native transcripts. Stale or empty output does not establish that no workers are running. This adapter does not make CodeBurn a runtime dependency.

For completed headless Claude results, use the [explicit capture and check workflow](docs/claude-results.md). It imports cumulative model-level summaries with declared receipt times and estimated costs. It does not infer live worker states or individual call chronology.

Prefer metadata-only [normalized events](docs/contract.md). You can import once or follow an explicitly selected append-only JSONL file:

```sh
python3 -m monitoring_ai import --file examples/events.jsonl --db /tmp/my-observatory.sqlite
python3 -m monitoring_ai top --db /tmp/my-observatory.sqlite --provider openai --json
python3 -m monitoring_ai serve --file /path/to/authorized-events.jsonl --db /tmp/my-observatory.sqlite
```

Two native transcript adapters are included. Only use files you own or are authorized to read. Select one explicit Claude Code JSONL file for [bounded live following](docs/claude-native.md), or a complete [Codex session JSONL file](docs/codex-native.md). There is no home-directory scan, credential reader, hook installation or account connection.

```sh
python3 -m monitoring_ai import --file /path/to/authorized-session.jsonl \
  --format codex --source-id my-codex-session --project my-app

python3 -m monitoring_ai import --file /path/to/authorized-session.jsonl \
  --format claude --source-id my-claude-session --project my-app
```

Use the same source ID and project alias when replaying or following a file. Different aliases create different observations and can double-count the same activity. One source must not be imported through two formats. The native adapters discard conversation content, tool arguments and filesystem paths, and hash session IDs. They reconstruct usage, **not live process state**. Missing model names stay `unknown`. The Claude and Codex followers read only newly completed lines after their initial scans. Identical Claude message usage with a later record timestamp is replay, including after file rotation. A later token revision for the same stable Claude message identity updates the current usage with an audit row and a new ledger revision; an ambiguous same-time or older change refuses. Codex counter decreases and unqualified nonzero cache-write counters refuse. A replaced Codex file must replay every previously accepted usage delta in order before new deltas are accepted. One owned Claude Code 2.1.308 file is qualified through a real append; changed-token correction is fixture-qualified. One historical Codex `0.162.0-alpha.17.2` JSONL file is qualified through parser, ledger, API and private dashboard, plus incremental idle/restart parity. General provider versions and a fresh Codex append remain unqualified.

Claude imports also retain metadata from complete structured tool-use/result pairs: tool aliases, error outcomes and recognized native categories. Incomplete pairs are uncounted. Inputs, result text and shell arguments are discarded. A `Bash` or `Skill` record identifies that invocation, not its private command or skill arguments. The adapter never infers duration from gaps between messages.

### Follow several selected files

For a first source, run `python3 -m monitoring_ai setup`. The terminal asks for one absolute file path that you explicitly choose, its format and safe aliases. It saves a private selection under the user's config directory (`$XDG_CONFIG_HOME/monitoring-ai` or `~/.config/monitoring-ai`), checks the selected file and prints preview and start commands. This keeps the selection outside the current project. Setup does not discover files, run providers, import a ledger or change a running service. A recent check proves only that a selected file was readable; the reported latest event timestamp can be stale, and native usage formats do not establish live workers. To add a file, run `python3 -m monitoring_ai setup --from-selection ~/.config/monitoring-ai/sources.json` (adjust for `$XDG_CONFIG_HOME`); the new selection is `sources-next.json` in the same default config directory. An existing output is never overwritten. Use `--output /path/to/new-private-selection.json` to choose another path. Restart a running owner deliberately with the new selection after previewing it. Deep consumes the same read-only source coverage once its configured owner has restarted.

Create a first selection without hand-writing JSON. Use a new output path in an existing private directory:

```sh
python3 -m monitoring_ai sources create --output /path/to/private-sources.json \
  --file /absolute/path/to/authorized-events.jsonl --id my-export --format normalized
python3 -m monitoring_ai sources add --sources /path/to/private-sources.json \
  --output /path/to/new-private-sources.json --file /absolute/path/to/authorized-session.jsonl \
  --id my-claude --format claude --project my-app
python3 -m monitoring_ai sources check --sources /path/to/new-private-sources.json
python3 -m monitoring_ai serve --sources /path/to/new-private-sources.json --db /tmp/my-observatory.sqlite
```

For a native export, select `--format claude` or `--format codex` and add `--project my-app`. `sources add` makes a new manifest with one more explicitly named file; it leaves the previous manifest untouched. Both create and add validate file metadata without reading source content, write private file permissions and refuse existing outputs, including symlinks. Neither scans for files. A missing selected file can be configured, then becomes readable when that exact path exists. After adding an input, restart `serve` with the new manifest; a running owner retains its original selection.

`sources check` reads only those selected inputs and reports parsing, observed record kinds and adapter limitations. Use `sources check --sources /path/to/private-sources.json --json` for path-free machine output. A failed input returns exit code 1 after reporting the other inputs. Invalid selection or creation returns 2. The check creates no ledger, imports no history and makes no provider request. It does not prove that a later import will reconcile existing event conflicts or overlapping collectors.

Keep a private JSON manifest outside the repository. Each entry names one authorized absolute file path. For example:

```json
{
  "schema": "monitoring.sources.v1",
  "sources": [
    {"id": "orchestrator-export", "format": "normalized", "path": "/absolute/path/to/events.jsonl"},
    {"id": "selected-codex-session", "format": "codex", "path": "/absolute/path/to/session.jsonl", "project": "my-app"}
  ]
}
```

```sh
python3 -m monitoring_ai serve --sources /path/to/private-sources.json --db /tmp/my-observatory.sqlite
```

Before following, preview ingestion against a disposable ledger:

```sh
python3 -m monitoring_ai sources preview --sources /path/to/private-sources.json --json
python3 -m monitoring_ai sources preview --sources /path/to/private-sources.json \
  --db /path/to/existing-ledger.sqlite
```

Omit `--db` to preview an empty base. With `--db`, the ledger must already exist: the command reads a consistent SQLite copy, including committed WAL records, and changes only a private temporary database. It uses the same immutable identity and hierarchy rules as ingestion, reports proposed additions/replays per input and continues after an input fails. Failed batches leave the preview unchanged. The original accepted history is preserved. Missing or malformed bases refuse rather than creating a ledger.

Readiness includes the current 100,000-event view limit. A ready result describes the copied base and the explicitly selected files read during that preview. Files can change before following begins. It does not establish worker liveness, prevent future conflicts or reconcile activity recorded under different aliases. Preview returns 0 when all inputs fit, 1 for unavailable/rejected inputs and 2 when configuration or the base cannot be inspected. The temporary copy is removed after success or failure. Base copying is bounded to 512 MiB and a five-second backup deadline.

The manifest is loaded once at startup and admits at most 32 files. Restart deliberately to change that selection. File paths stay in the private configuration; the read API and dashboard show only aliases, format, project and read outcomes. Native inputs require a project alias. Normalized inputs retain their supplied source and project dimensions, so a managed input alias is not necessarily an event-source filter.

Each input is read and imported independently. Missing files, malformed data or changed event identities mark that input unavailable; other inputs continue and accepted history stays in the ledger. Recovery replays safely. A changed identity rejects the whole attempted batch for that input. Replacing or truncating a file never deletes earlier observations. The panel shows counts for the latest read attempt, not lifetime ingestion totals. Last successful read describes polling freshness, not worker liveness or completeness of session coverage.

Duplicate selected paths and IDs refuse at startup. Copies of the same native session under different paths and aliases can still double-count; choose one collector per activity. Final-file symlinks, directories, unknown manifest fields and relative paths refuse. There is no directory discovery, browser configuration editor or automatic admission of newly added manifest entries. `--sources`, `--file` and `--demo` are mutually exclusive.

For pricing, adapt [the fictional price book](examples/pricing.json) to your verified rates and validity dates. All four rates are amounts per million tokens. Add `--pricing /path/to/pricing.json` to `serve` or `top`. Reported event costs take precedence. To convert, supply `--currency EUR --fx /path/to/fx.json` with a dated [FX book](examples/fx.json). Example prices and exchange rates are deliberately fictional.

[Desktop input-health example](docs/inputs.png) · [Mobile panel](docs/inputs-mobile.png). Both show fictional sources, including an intentionally missing demo input.

### Understand connected coverage

The dashboard separates **what an adapter can supply** from **what its latest accepted read actually contains**. Claude and Codex files reconstruct usage, with completed tool-pair metadata supported for Claude. Neither adapter supplies worker lifecycle. Normalized producers can supply explicit usage, status and tool observations, but a file with no status records does not provide lifecycle evidence merely because that format permits it.

Coverage counts unique `(source, id)` observations within each input's latest accepted read. Raw read-attempt counts, replay counts and all-time ledger totals are different measures. Separate inputs can overlap, so their coverage counts are never summed into a claim of complete activity. Entity and session identities are observed metadata, not an operating-system process roster.

Input sample time records when data was read. Event age records its supplied evidence time and continues aging between reads. A successfully polled old file stays stale. Empty, unread, future-dated and retained-after-failure samples remain distinct. Current session states continue to use the ledger's independent lifecycle rules. Source inventory is global and does not change with usage filters.

## Usage pace and quotas

Overview shows recorded usage over the trailing 60-minute window. Its one-hour projection uses that full window as the denominator. The view shows sample count, observed span and a heuristic confidence label. Estimates with incomplete pricing remain qualified, and billed amounts are excluded from projected estimates. This is a linear scenario, not a prediction of provider exhaustion or a future invoice.

Quota is a separate account-wide observation. Usage filters do not make a quota project-specific. An imported snapshot can be read without any account connection:

```sh
python3 -m monitoring_ai serve --db /tmp/my-observatory.sqlite --quota-file examples/quotas.json
python3 -m monitoring_ai quota --file examples/quotas.json
```

The example is fictional and becomes stale according to its original timestamp. Demo mode seeds fresh **fictional** quota observations, with an unknown weekly value to demonstrate missing evidence. No account connection is made by default.

If the installed Codex CLI already has an authorized login, opt in to its supported account allowance interface:

```sh
python3 -m monitoring_ai quota --codex --account work
python3 -m monitoring_ai serve --db /tmp/my-observatory.sqlite --codex-quota --quota-account work
```

The first command reads once and prints quota-only JSON. The server reads at most once a minute in a separate worker, so a slow quota read does not block usage views. It sends only initialization and `account/rateLimits/read` to its own finite app-server process. It does not inspect conversations, change login, start inference, reset allowance or enable overage. `captured_at` is local response-receipt time, not a provider sample timestamp. Unsupported or unavailable sources retain prior observations with a visible failure state. Use `--codex-executable /absolute/path/to/codex` if the installed executable is not on PATH.

Quota evidence becomes stale after five minutes. Future timestamps and passed reset times are explicit. A passed reset never implies replenishment. The collector was qualified against an existing authorized Codex login, while public examples and tests remain synthetic. [The quota and insight contract](docs/contract.md#usage-insights-and-quota-observations) documents the format and limits.

## Detailed reports

Open **Report** or press **5**. Choose Today, 7 days, 30 days, This month, 6 months or All recorded, then filter by provider, model, surface, project, source or session. Date controls use UTC. Six months rolls back six calendar months to the same day, clamping month ends. Custom date bounds remain available.

Projects show usage, observed sessions and qualified costs. Models add recorded cache-read share and timing from explicitly timed records. Daily rows retain chronology. Tool panels group supplied core/MCP/shell/skill/agent aliases, errors and reported durations. These are invocation observations, not a worker roster. Untagged tools stay unclassified.

The comparison uses the immediately preceding interval of equal elapsed length. Today's end is clamped to the read time, so its prior interval is an adjacent equal-length window rather than an asserted calendar-day comparison. Exact bounds are displayed. All recorded has no invented preceding period. Cost changes remain unavailable unless each period has complete coverage for that same cost basis.

```sh
python3 -m monitoring_ai report --db /tmp/my-observatory.sqlite --since 2026-11-01 --until 2026-11-02
python3 -m monitoring_ai report --db /tmp/my-observatory.sqlite --project my-app --json
```

Known estimate per priced session is a covered subtotal divided by sessions with priced estimated records. Recorded cache-read share uses cached reads divided by all recorded input buckets. Reported output rate uses only records with positive explicit durations and is not model streaming speed. Context overhead, one-shot rate, corrections, time to first edit and rework are unavailable from this schema. No workflow-quality conclusion is inferred from tool errors or chronology. [The detailed report contract](docs/contract.md#detailed-report) defines those measures.

[Explore all six dashboard themes](docs/gallery/README.md) with the same fictional data and layout. Settings screenshots show the separate configuration destination.

## Appearance

Open **Settings → Appearance**. Follow the system scheme or choose Neon 1 Dark, Neon 1 Light, Slate Dark, Sand Light, Forest Light or Matrix Dark. Monitoring bundles the pinned shared `@deep/theme` candidate locally, with its MIT license. It needs no running Deep service, registry installation or network request.

Color pickers, corner radius and density offer local overrides. **Apply overrides** validates them before changing the view. Unsafe contrast is refused and the current theme stays active. **Reset appearance** returns to system mode. Explicit choices survive reload and update other tabs on the same Monitoring origin. Storage failures leave the current page usable with a visible persistence message. Settings use a Monitoring-specific browser key, with no automatic cross-application synchronization.

Settings also holds source-setup guidance. Overview keeps monitoring evidence and contextual filters. [Settings ownership and timing](docs/settings-contract.md) describes sources, defaults, persistence and effect.

The shared palette changes appearance only. Estimated and billed costs, unknown values, source read health and lifecycle evidence retain their existing meanings. [Pinned package provenance](docs/theme-source.json) records the exact candidate and checksums. Monitoring owns its adapter and layouts.

[Dark dashboard](docs/theme-dark.png) · [Light dashboard](docs/theme-light.png) · [Mobile appearance](docs/theme-mobile.png) · [Light input health](docs/theme-inputs-mobile.png). These captures contain fictional data.

The main navigation can collapse to an icon rail while keeping named destinations and tooltips. Settings has contextual categories for All settings, Appearance and Sources. Small screens use local horizontal navigation strips, and reduced-motion preferences disable transitions. The layout choice is browser-local and separate from appearance preferences. [Shared navigation ownership](docs/navigation-contract.md) records the source pattern and compatibility boundaries.

[Collapsed desktop Settings](docs/navigation-desktop.png) · [Mobile contextual navigation](docs/navigation-mobile.png). Both use fictional observations.

## Limits

Cloud producers can write normalized metadata through an authorized export or collector. Cloud account adapters, remote authentication, OpenTelemetry receivers, hardware/process sampling, budget alerts and execution controls remain planned. Quota collection currently supports the installed Codex account interface and explicit normalized files. Subscription quotas and API-equivalent costs are different measures. This alpha does not derive one from the other.

The server binds to loopback and rejects cross-origin browser reads. It has no write API. The local ledger is private operational data, not encrypted storage. Use a private working directory with appropriate filesystem permissions. Never commit your ledger, source files or real session exports. The repository contains only fictional examples and screenshots.

Each source is limited to 32 MiB and 50,000 records per import. Snapshot reads are limited to 100,000 stored events and 500 displayed entities. File following rereads the bounded file and deduplicates it. This is suitable for a small local observatory, not a high-throughput ingestion service. The limits fail visibly instead of silently omitting usage. Ordinary Events mode shows the snapshot's latest 100 records. Selecting an entity opens chronological history in pages of 100, scoped to its source and session and pinned to one ledger revision. Current usage/date filters still apply. A changed ledger requires an explicit restart, and loaded history is not live-worker evidence.

## Development

```sh
python3 -m unittest discover -s tests -v
node --test tests/client.test.mjs  # Node 24 for native TypeScript stripping
npm ci
npx playwright install chromium
npm run test:browser
```

Browser tests launch their own synthetic server and headless browser. They make no model call and do not use your foreground browser. See [the contract](docs/contract.md), [reuse findings](docs/reuse.md) and [roadmap](docs/roadmap.md). MIT licensed.
