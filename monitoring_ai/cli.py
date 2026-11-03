"""Run the local monitoring server, import an explicit source or inspect usage in a terminal."""

import argparse
import json
from pathlib import Path
import secrets
import sqlite3
import sys
import time

from . import __version__
from .adapters import load
from .demo import demo_events, demo_quotas, stream_demo, write_demo
from .metrics import DIMENSIONS, Exchange, Pricing, snapshot
from .server import Observatory, serve
from .store import Store
from .quota_sources import codex_book, quota_export, quota_file, read_codex_quota
from .reporting import detailed_report


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--version", action="version", version=__version__)
    sub = cli.add_subparsers(dest="command", required=True)
    for name in ("serve", "import", "top"):
        command = sub.add_parser(name)
        command.add_argument("--db", type=Path, default=Path(".monitoring-ai/events.sqlite"))
        if name != "import":
            command.add_argument("--pricing", type=Path, help="Explicit dated per-million price book")
            command.add_argument("--fx", type=Path, help="Explicit dated USD-based exchange rates")
            command.add_argument("--currency", default="USD")
            quota = command.add_mutually_exclusive_group()
            quota.add_argument("--quota-file", type=Path, help="Explicit normalized quota observations")
            quota.add_argument("--codex-quota", action="store_true", help="Read existing Codex account allowance, no sessions or model calls")
            command.add_argument("--quota-account", default="default", help="Local account alias, independent of usage filters")
            command.add_argument("--quota-source-id", default="codex-quota")
            command.add_argument("--codex-executable", type=Path, help="Optional absolute installed Codex executable")
        if name != "top":
            command.add_argument("--file", type=Path, required=name == "import")
            command.add_argument("--format", choices=["normalized", "claude", "codex", "claude-results", "otlp-jsonl"], default="normalized")
            command.add_argument("--source-id", help="Stable alias for this native input, reused across replay")
            command.add_argument("--project", help="Public-safe project alias for native input")
        if name in ("serve", "top"):
            command.add_argument("--token-threshold", type=int,
                                 help="Optional observed-token threshold for the current UTC day; not provider quota")
        if name == "serve":
            command.add_argument("--port", type=int, default=5274)
            command.add_argument("--demo", action="store_true", help="Seed an empty ledger with fictional data")
            command.add_argument("--sources", type=Path, help="Explicit private source manifest, loaded once at startup")
            command.add_argument("--codeburn-cache", type=Path,
                                 help="Explicit CodeBurn v74 daily cache. Separate totals with unknown billing basis")
            command.add_argument("--codeburn-status", type=Path,
                                 help="Explicit existing CodeBurn menubar status output. Recent writes, not process liveness")
            command.add_argument("--codeburn-session-cache", type=Path,
                                 help="Optional CodeBurn v10 cache directory; requires --sources and --cache-source-id")
            command.add_argument("--cache-source-id", help="Exact selected native source ID to inspect in the session cache")
            command.add_argument("--processes", action="store_true",
                                 help="Sample matching AI executable names from macOS; no arguments or session inference")
            command.add_argument("--process-interval", type=int,
                                 help="OS process sample interval in seconds (2–60; default 5; requires --processes)")
        if name == "top":
            command.add_argument("--json", action="store_true")
            command.add_argument("--watch", action="store_true", help="Refresh every two seconds in this terminal")
            for key in (*DIMENSIONS, "since", "until", "state"):
                command.add_argument("--" + key)
    demo = sub.add_parser("demo", help="Write fictional normalized JSONL, optionally keep appending")
    demo.add_argument("file", type=Path)
    demo.add_argument("--stream", action="store_true")
    quota = sub.add_parser("quota", help="Read one quota source as normalized JSON without persisting it")
    source = quota.add_mutually_exclusive_group(required=True)
    source.add_argument("--codex", action="store_true", help="Use existing native Codex login through account/rateLimits/read")
    source.add_argument("--file", type=Path)
    quota.add_argument("--format", choices=["normalized", "codex"], default="normalized")
    quota.add_argument("--captured-at", help="Required original response time for a Codex JSON export")
    quota.add_argument("--source-id", default="codex-quota")
    quota.add_argument("--account", default="default")
    quota.add_argument("--executable", type=Path)
    report = sub.add_parser("report", help="Inspect qualified period and tool reports without contacting a provider")
    report.add_argument("--db", type=Path, default=Path(".monitoring-ai/events.sqlite"))
    report.add_argument("--pricing", type=Path)
    report.add_argument("--fx", type=Path)
    report.add_argument("--currency", default="USD")
    report.add_argument("--json", action="store_true")
    for key in (*DIMENSIONS, "since", "until", "state"):
        report.add_argument("--" + key)
    ledger = sub.add_parser("ledger", help="Back up or check an explicitly selected private ledger")
    ledger_actions = ledger.add_subparsers(dest="ledger_action", required=True)
    backup = ledger_actions.add_parser("backup", help="Save committed history to a new private SQLite file")
    backup.add_argument("--db", type=Path, required=True, help="Existing source ledger")
    backup.add_argument("--output", type=Path, required=True, help="New backup file; existing output refuses")
    verify = ledger_actions.add_parser("verify", help="Read-only integrity and revision check")
    verify.add_argument("--db", type=Path, required=True, help="Existing ledger or backup")
    setup = sub.add_parser("setup", help="Choose one authorized local file with guided prompts")
    setup.add_argument("--output", type=Path, help="New private selection file; default user config directory")
    setup.add_argument("--from-selection", type=Path, help="Existing selection to add to; left unchanged")
    sources = sub.add_parser("sources", help="Select explicit inputs and check their supplied evidence")
    actions = sources.add_subparsers(dest="source_action", required=True)
    create = actions.add_parser("create", help="Create one private selection without overwriting a file")
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--file", type=Path, required=True)
    create.add_argument("--id", required=True, help="Safe alias, independent of a normalized event's source")
    create.add_argument("--format", choices=["normalized", "claude", "codex", "claude-results", "otlp-jsonl"], default="normalized")
    create.add_argument("--project", help="Required project alias for native inputs")
    add = actions.add_parser("add", help="Create a new private selection with one more authorized input")
    add.add_argument("--sources", type=Path, required=True, help="Existing private manifest; left unchanged")
    add.add_argument("--output", type=Path, required=True, help="New private manifest; existing files refuse")
    add.add_argument("--file", type=Path, required=True)
    add.add_argument("--id", required=True)
    add.add_argument("--format", choices=["normalized", "claude", "codex", "claude-results", "otlp-jsonl"], default="normalized")
    add.add_argument("--project", help="Required project alias for native inputs")
    capture = actions.add_parser("capture-claude", help="Extract metadata from one selected completed Claude CLI JSON result")
    capture.add_argument("--file", type=Path, required=True)
    capture.add_argument("--captured-at", required=True, help="Original receipt time, with timezone. Never inferred from a reread")
    capture.add_argument("--output", type=Path, required=True, help="New private metadata JSONL file. Existing files refuse")
    check = actions.add_parser("check", help="Check selected file parsing and coverage without changing a ledger")
    check.add_argument("--sources", type=Path, required=True)
    check.add_argument("--json", action="store_true")
    preview = actions.add_parser("preview", help="Preview ingestion in a disposable ledger without changing history")
    preview.add_argument("--sources", type=Path, required=True)
    preview.add_argument("--db", type=Path, help="Explicit existing ledger to copy read-only. Omit for an empty base")
    preview.add_argument("--json", action="store_true")
    inspect = actions.add_parser("inspect-cache", help="Inspect cached turns for one directly readable selected native file")
    inspect.add_argument("--sources", type=Path, required=True)
    inspect.add_argument("--id", required=True, help="Exact selected Claude or Codex source ID")
    inspect.add_argument("--cache-dir", type=Path, required=True, help="Explicit CodeBurn v10 session-cache directory")
    return cli


def money(value):
    return "unknown" if value is None else f"{float(value):,.4f}"


def terminal(value):
    summary = value["summary"]
    lines = [f"MONITORING AI  {value['observed_at']}  •  {value['currency']}",
             f"{summary['sessions']} sessions  {summary['active']} running  {summary['attention']} need attention  {summary['total_tokens']:,} tokens",
             f"Estimated {money(summary['costs']['estimated']['amount'])}  Billed {money(summary['costs']['billed']['amount'])}  Unknown {summary['unknown_cost_events']}  Unconverted {summary['unconverted_cost_events']}",
             "", f"{'ENTITY':<22} {'STATE':<12} {'MODEL':<24} {'TOKENS':>12} {'ESTIMATE':>12}"]
    for row in value["entities"]:
        lines.append(f"{row['entity'][:22]:<22} {row['state']:<12} {row['model'][:24]:<24} {row['metrics']['total_tokens']:>12,} {money(row['metrics']['costs']['estimated']['amount']):>12}")
    lines.extend(["", "Coverage: explicitly supplied events. Lifecycle ages independently of usage."])
    threshold = value.get("token_threshold")
    if threshold:
        lines.append(f"UTC-day observed token threshold: {threshold['observed_tokens']:,} / {threshold['limit_tokens']:,} · {threshold['state']}. Incomplete source coverage; no provider enforcement.")
    insights = value.get("insights", {})
    pace = insights.get("pace", {})
    forecast = insights.get("forecast", {})
    lines.append(f"Selected-window pace: {pace.get('tokens_per_minute', 'unknown')} tokens/min; forecast {forecast.get('state', 'unknown')} ({forecast.get('confidence', 'unknown')} heuristic confidence)")
    lines.append(f"Estimated cost rate: {money(pace.get('estimated_cost_per_hour'))} {value['currency']}/hour; billed records excluded")
    for window in insights.get("quotas", {}).get("windows", []):
        remaining = window["remaining_percent"]
        lines.append(f"Quota {window['provider']}/{window['account']} {window['label']}: {'unknown' if remaining is None else str(remaining) + '% remaining'} · {window['freshness']} · captured {window['captured_at']}")
    return "\n".join(lines)


def quota_loader(args):
    if args.quota_file:
        return lambda: quota_file(args.quota_file)
    if args.codex_quota:
        return lambda: read_codex_quota(executable=args.codex_executable,
                                       source=args.quota_source_id, account=args.quota_account)
    return None


def _print_source_check(value):
    print("MONITORING AI INPUT CHECK · explicit selected files only")
    for row in value["checks"]:
        print(f"\n{row['id']} · {row['format']} · {row['state']}")
        if row["error"]:
            print(row["error"])
            continue
        summary = row["summary"]
        counts = summary["records"]
        print(f"{counts['usage']} usage · {counts['status']} lifecycle · {counts['tool']} tool records · {counts['sessions']} observed sessions")
        print(f"Latest supplied event: {summary['latest_event_at'] or 'none'} · checked: {summary['sampled_at']}")
        if summary["capabilities"]["lifecycle"] == "unsupported":
            print("Usage reconstruction only. This adapter supplies no live worker lifecycle.")
        elif not counts["status"]:
            print("No lifecycle events supplied. A producer must explicitly export status evidence.")
        else:
            print("Producer-supplied lifecycle observations. Evidence ages independently of file reads.")
        if not counts["total"]:
            print("No complete supported records yet. Check the selected format and producer output.")
    print("\n" + value["note"])
    print("A recent file read does not prove a live session. Missing evidence means coverage is unknown, not idle.")


def run(args):
    if args.command == "ledger":
        from .ledger_backup import backup_ledger, inspect_ledger
        result = (backup_ledger(args.db, args.output) if args.ledger_action == "backup"
                  else inspect_ledger(args.db))
        print(json.dumps(result, indent=2))
        return
    if args.command == "setup":
        from .guided_setup import run_setup
        run_setup(args.output, args.from_selection)
        return
    if args.command == "sources":
        from .source_setup import add_selection, create_selection, check_selection
        from .sources import SourceManifestError
        try:
            if args.source_action == "inspect-cache":
                from .sources import load_sources
                from .codeburn_sessions import inspect_selected_session
                selected = [source for source in load_sources(args.sources) if source.id == args.id]
                if len(selected) != 1:
                    raise ValueError("Selected native source ID is unavailable")
                print(json.dumps(inspect_selected_session(args.cache_dir, selected[0]), indent=2))
                return
            if args.source_action == "capture-claude":
                from .result_capture import capture_cli_result
                capture_cli_result(args.file, args.output, captured_at=args.captured_at)
                print("Created a private metadata result frame. It records usage summaries, not live worker status. Select it with sources create --format claude-results.")
                return
            if args.source_action == "create":
                create_selection(args.output, file=args.file, id=args.id, format=args.format, project=args.project)
                print("Created a private source selection. Run sources check, then serve --sources with the selected manifest.")
                return
            if args.source_action == "add":
                add_selection(args.sources, args.output, file=args.file, id=args.id,
                              format=args.format, project=args.project)
                print("Created a new private source selection. The prior manifest is unchanged. Run sources check or preview, then restart serve with the new manifest.")
                return
            if args.source_action == "preview":
                from .source_readiness import preview_selection
                value = preview_selection(args.sources, args.db)
                if args.json:
                    print(json.dumps(value, indent=2))
                else:
                    base = value["base"]
                    print(f"MONITORING AI INGESTION PREVIEW · {base['kind']} · revision {base['revision']} · {base['events']} accepted observations in base")
                    for row in value["inputs"]:
                        print(f"\n{row['id']} · {row['format']} · {row['state']}")
                        if row["proposed"] is not None:
                            print(f"Proposed additions {row['proposed']['added']} · replays {row['proposed']['replayed']}")
                        if row["error"]:
                            print(row["error"])
                        if row["summary"]:
                            counts = row["summary"]["records"]
                            print(f"Unique sample: {counts['usage']} usage · {counts['status']} lifecycle · {counts['tool']} tool observations")
                            if not counts["total"]:
                                print("The selected file supplies no complete supported observations yet.")
                        if row["summary"] and row["summary"]["capabilities"]["lifecycle"] == "unsupported":
                            print("This usage adapter supplies no live worker lifecycle.")
                    print("\nPrivate temporary preview completed. Accepted ledger history is unchanged.")
                    print(value["note"])
                if not value["ready"]:
                    raise SystemExit(1)
                return
            value = check_selection(args.sources)
        except FileExistsError:
            print("monitoring-ai: output already exists. Choose a new --output. No file was overwritten.", file=sys.stderr)
            raise SystemExit(2) from None
        except SourceManifestError as error:
            # Manifest parser diagnostics are fixed text, without private input values.
            print(f"monitoring-ai: source selection rejected. {error}", file=sys.stderr)
            raise SystemExit(2) from None
        if args.json:
            print(json.dumps(value, indent=2))
        else:
            _print_source_check(value)
        if not value["all_readable"]:
            raise SystemExit(1)
        return
    if args.command == "quota":
        if args.codex:
            value = read_codex_quota(executable=args.executable, source=args.source_id, account=args.account)
        elif args.format == "normalized":
            value = quota_file(args.file)
        else:
            if not args.captured_at:
                raise ValueError("Codex exports require their original --captured-at response time")
            value = codex_book(quota_export(args.file), captured_at=args.captured_at,
                               source=args.source_id, account=args.account)
        print(json.dumps(value, indent=2))
        return
    if args.command == "demo":
        (stream_demo if args.stream else write_demo)(args.file)
        return
    if args.command in ("serve", "top") and args.token_threshold is not None:
        from .token_threshold import validate_limit
        validate_limit(args.token_threshold)
    store = Store(args.db)
    if args.command == "import":
        result = store.ingest(load(args.file, args.format, args.source_id, args.project))
        print(json.dumps(result))
        return
    if len(args.currency) != 3 or not args.currency.isupper():
        raise ValueError("Use an uppercase three-letter currency code")
    pricing, fx = Pricing(args.pricing), Exchange(args.currency, args.fx)
    if args.command == "report":
        query = {key: getattr(args, key) for key in (*DIMENSIONS, "since", "until", "state") if getattr(args, key)}
        value = detailed_report(*store.read(), query, pricing, fx)
        if args.json:
            print(json.dumps(value, indent=2))
        else:
            summary = value["summary"]
            print(f"MONITORING AI REPORT  {value['observed_at']}  •  {value['currency']}")
            print(f"{summary['calls']} usage records · {summary['observed_sessions']} observed sessions · {summary['total_tokens']:,} tokens")
            print(f"Known estimate {money(summary['costs']['estimated']['amount'])} · reported billed {money(summary['costs']['billed']['amount'])}")
            print(f"Unknown cost {summary['unknown_cost_events']} · missing FX {summary['unconverted_cost_events']} · estimates and bills stay separate")
            print("\nPROJECT                  TOKENS       KNOWN ESTIMATE      OBSERVED SESSIONS")
            for row in value["breakdowns"]["projects"]["rows"]:
                print(f"{row['key'][:24]:24} {row['metrics']['total_tokens']:>12,} {money(row['metrics']['costs']['estimated']['amount']):>18} {row['observed_sessions']:>22}")
            print(f"\nTool records {value['tools']['records']} · errors {value['tools']['errors']} · unclassified {value['tools']['unclassified_records']}")
            print("Comparison: " + value["coverage"]["comparison_basis"])
            print("Unavailable from these records: " + ", ".join(value["coverage"]["unsupported"]))
        return
    if args.command == "serve":
        if not 0 <= args.port <= 65535:
            raise ValueError("Invalid port")
        if sum(bool(value) for value in (args.demo, args.file, args.sources)) > 1:
            raise ValueError("Choose --demo, --file or --sources")
        if args.sources and (args.format != "normalized" or args.source_id or args.project):
            raise ValueError("Source aliases and formats belong in the manifest")
        if bool(args.codeburn_session_cache) != bool(args.cache_source_id) or (args.cache_source_id and not args.sources):
            raise ValueError("Session cache inspection needs --sources, --codeburn-session-cache and --cache-source-id")
        observer = None
        selected_sources = None
        if args.sources:
            from .sources import load_sources
            from .source_observer import SourceObserver
            selected_sources = load_sources(args.sources)
            observer = SourceObserver(selected_sources)
        session_cache_loader = None
        if args.cache_source_id:
            from .codeburn_sessions import inspect_selected_session
            matching = [source for source in selected_sources if source.id == args.cache_source_id]
            if len(matching) != 1 or matching[0].format not in ("claude", "codex"):
                raise ValueError("Session cache inspection needs one selected native Claude or Codex source")
            session_cache_loader = lambda: inspect_selected_session(args.codeburn_session_cache, matching[0])
        if args.demo:
            if store.read()[0]:
                raise ValueError("Demo seeding requires an empty ledger")
            store.ingest(demo_events())
        if args.file and args.format in ("claude", "codex"):
            from .claude_follow import ClaudeFollower
            from .codex_follow import CodexFollower
            loader = (ClaudeFollower if args.format == "claude" else CodexFollower)(args.file, args.source_id, args.project)
        else:
            loader = (lambda: load(args.file, args.format, args.source_id, args.project, live=True)) if args.file else None
        cache_loader = None
        if args.codeburn_cache:
            from .codeburn_cache import read_codeburn_cache
            cache_loader = lambda: read_codeburn_cache(args.codeburn_cache)
        activity_loader = None
        if args.codeburn_status:
            from .codeburn_status import read_codeburn_status
            alias_salt = secrets.token_bytes(32)
            activity_loader = lambda: read_codeburn_status(args.codeburn_status, alias_salt=alias_salt)
        process_observer = None
        if args.process_interval is not None and not args.processes:
            raise ValueError("--process-interval requires --processes")
        if args.processes:
            if sys.platform != "darwin":
                raise ValueError("OS process sampling currently supports macOS")
            from .process_observer import ProcessObserver
            process_observer = ProcessObserver(interval_seconds=args.process_interval if args.process_interval is not None else 5)
        serve(Observatory(store, pricing, fx, loader, quota_loader=quota_loader(args),
                          source_observer=observer,
                          cached_usage_loader=cache_loader,
                          codeburn_activity_loader=activity_loader,
                          session_cache_loader=session_cache_loader,
                          process_observer=process_observer,
                          token_threshold=args.token_threshold,
                          quota_book=demo_quotas() if args.demo and not args.quota_file and not args.codex_quota else None), args.port)
    else:
        if args.watch and (args.json or not sys.stdout.isatty()):
            raise ValueError("--watch needs an interactive terminal and cannot combine with --json")
        query = {key: getattr(args, key) for key in (*DIMENSIONS, "since", "until", "state") if getattr(args, key)}
        quota_read = quota_loader(args)
        book, last_quota_read = None, float("-inf")
        quota_error = False
        while True:
            if quota_read and time.monotonic() - last_quota_read >= 60:
                try:
                    book = quota_read()
                    quota_error = False
                except (ValueError, OSError):
                    quota_error = True
                last_quota_read = time.monotonic()
            revision, rows = store.read()
            value = snapshot(revision, rows, query, pricing, fx, quota_book=book)
            if args.token_threshold is not None:
                from .token_threshold import evaluate_token_threshold
                value["token_threshold"] = evaluate_token_threshold(rows, limit=args.token_threshold,
                                                                       now=value["observed_at"])
            value["insights"]["quotas"]["read_status"] = dict(state="unavailable" if quota_error else "following" if quota_read else "unconfigured")
            if args.watch:
                print("\033[2J\033[H", end="")
            print(json.dumps(value, indent=2) if args.json else terminal(value), flush=True)
            if quota_error and not args.json:
                print("Quota read unavailable; retained dated observations may be stale.", flush=True)
            if not args.watch:
                break
            time.sleep(2)


def main():
    args = parser().parse_args()
    try:
        run(args)
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, sqlite3.Error):
        # Avoid printing source bodies, credentials or native paths in ordinary diagnostics
        print("monitoring-ai: operation failed. Check arguments, file access and the documented schema.", file=sys.stderr)
        raise SystemExit(2) from None
