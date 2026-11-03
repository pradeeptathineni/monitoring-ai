"""Run the local monitoring server, import an explicit source or inspect usage in a terminal."""

import argparse
from contextlib import closing
import json
from pathlib import Path
import secrets
import sqlite3
import sys
import time

from . import __version__
from .adapters import load
from .demo import demo_events, demo_quotas, stream_demo, write_demo
from .events import label, validate
from .metrics import DIMENSIONS, Exchange, Pricing, filters, selected, snapshot
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
            command.add_argument("--app-threads", type=Path, help="Explicit metadata-only app task inventory export")
            command.add_argument("--token-threshold", type=int,
                                 help="Optional observed-token threshold for the current UTC day; not provider quota")
            command.add_argument("--estimated-cost-threshold",
                                 help="Optional current UTC-day threshold over known estimated cost in display currency")
            command.add_argument("--billed-cost-threshold",
                                 help="Optional current UTC-day threshold over known billed cost in display currency")
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
            command.add_argument("--app-kind", choices=["codex", "chatgpt"], help="Filter human-readable app rows only")
            command.add_argument("--app-status", choices=["active", "idle", "unknown"], help="Filter current reported app status only")
            command.add_argument("--app-alias", help="Case-insensitive substring of a private app alias")
            command.add_argument("--app-limit", type=int, help="Human-readable app row limit, 1–100 (default 20)")
            command.add_argument("--sources", type=Path, help="Follow explicitly selected files in this terminal")
            command.add_argument("--processes", action="store_true",
                                 help="Sample matching macOS executable names; separate from session identity")
            command.add_argument("--process-interval", type=int,
                                 help="OS sample interval in seconds (2–60; default 5; requires --processes)")
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
    history = sub.add_parser("history", help="Read exact entity chronology from an existing ledger")
    history.add_argument("--db", type=Path, default=Path(".monitoring-ai/events.sqlite"))
    for key in ("source", "session", "entity"):
        history.add_argument("--" + key, required=True, help="Exact recorded alias, not a path")
    for key in ("provider", "model", "surface", "project", "since", "until"):
        history.add_argument("--" + key)
    history.add_argument("--limit", type=int, default=100, help="Records per page (1–500)")
    history.add_argument("--after", type=int, default=0, help="Oldest-first record offset")
    history.add_argument("--expected-revision", type=int, help="Pin later pages to the first ledger revision")
    history.add_argument("--json", action="store_true", help="Local metadata-only history page")
    session_export = sub.add_parser("export-session", help="Export one exact recorded session as new private normalized JSONL")
    session_export.add_argument("--db", type=Path, required=True)
    session_export.add_argument("--output", type=Path, required=True, help="New 0600 file; existing paths refuse")
    session_export.add_argument("--source", required=True)
    session_export.add_argument("--session", required=True)
    for key in ("entity", "provider", "model", "surface", "project", "since", "until"):
        session_export.add_argument("--" + key)
    session_export.add_argument("--expected-revision", type=int)
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
    setup.add_argument("--db", type=Path, help="Preview selected inputs against this existing ledger before suggesting a restart")
    sources = sub.add_parser("sources", help="Select explicit inputs and check their supplied evidence")
    actions = sources.add_subparsers(dest="source_action", required=True)
    api_receipt = actions.add_parser("capture-openai-response", help="Extract only metadata from one selected completed API response")
    api_receipt.add_argument("--file", type=Path, required=True)
    api_receipt.add_argument("--output", type=Path, required=True, help="Private normalized JSONL file")
    api_receipt.add_argument("--append", action="store_true", help="Append to an existing owned 0600 file, replaying identical receipts once")
    api_receipt.add_argument("--source-id", required=True)
    api_receipt.add_argument("--project", required=True)
    api_receipt.add_argument("--session", required=True, help="Application grouping alias, not an inferred provider conversation")
    host_capture = actions.add_parser("capture-codex-host", help="Read one database-only metadata page from an existing approved local Codex host")
    host_capture.add_argument("--socket", type=Path, required=True, help="Existing owned private socket; no discovery or startup")
    host_capture.add_argument("--cwd", type=Path, required=True, help="Exact authorized project scope")
    host_capture.add_argument("--output", type=Path, required=True)
    host_capture.add_argument("--replace", action="store_true", help="Refresh an existing private export of this same source")
    app_capture = actions.add_parser("capture-app-threads", help="Capture only selected app inventory metadata from stdin")
    app_capture.add_argument("--codex-history", action="store_true", help="Metadata from one supported non-archived Codex history page; no ChatGPT coverage")
    app_capture.add_argument("--output", type=Path, required=True)
    app_capture.add_argument("--captured-at", required=True, help="Original supported inventory observation time")
    app_capture.add_argument("--replace", action="store_true", help="Refresh an existing owned 0600 inventory export")
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
    selected_stream = actions.add_parser("select-claude-stream", help="Create a private selection for one Claude CLI status and result capture")
    selected_stream.add_argument("--output", type=Path, required=True, help="New private source manifest; existing files refuse")
    selected_stream.add_argument("--status-file", type=Path, required=True, help="Normalized status output of capture-claude-stream")
    selected_stream.add_argument("--result-file", type=Path, required=True, help="Sanitized result output of the same capture")
    selected_stream.add_argument("--source-id", required=True, help="Must match capture-claude-stream --source-id")
    selected_stream.add_argument("--project", required=True, help="Must match capture-claude-stream --project")
    capture = actions.add_parser("capture-claude", help="Extract metadata from one selected completed Claude CLI JSON result")
    capture.add_argument("--file", type=Path, required=True)
    capture.add_argument("--captured-at", required=True, help="Original receipt time, with timezone. Never inferred from a reread")
    capture.add_argument("--output", type=Path, required=True, help="New private metadata JSONL file. Existing files refuse")
    live = actions.add_parser("capture-claude-stream", help="Capture one Claude CLI invocation from stdin")
    live.add_argument("--output", type=Path, required=True, help="New private normalized JSONL file; existing files require --append")
    live.add_argument("--append", action="store_true", help="Append another invocation to existing owned private status/result files")
    live.add_argument("--source-id", required=True, help="Stable local alias for this CLI stream")
    live.add_argument("--project", required=True, help="Safe project alias")
    live.add_argument("--result-output", type=Path,
                      help="New private sanitized cumulative result frame at completion; separate accounting source")
    live.add_argument("--quota-output", type=Path,
                      help="Private CLI-reported quota file; --append refreshes an existing owned file")
    live.add_argument("--quota-account", default="local", help="Local alias for the CLI-reported quota observation")
    live.add_argument("--forward", action="store_true", help="Pass raw native stream to stdout for an explicit downstream consumer")
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


def terminal(value, *, app_kind=None, app_status=None, app_alias=None, app_limit=20):
    summary = value["summary"]
    lines = [f"MONITORING AI  {value['observed_at']}  •  {value['currency']}",
             f"{summary['sessions']} sessions  {summary['active']} running  {summary['attention']} need attention  {summary['total_tokens']:,} tokens",
             f"Estimated {money(summary['costs']['estimated']['amount'])}  Billed {money(summary['costs']['billed']['amount'])}  Unknown {summary['unknown_cost_events']}  Unconverted {summary['unconverted_cost_events']}",
             "", f"{'ENTITY':<22} {'STATE':<12} {'MODEL':<24} {'TOKENS':>12} {'ESTIMATE':>12}"]
    for row in value["entities"]:
        lines.append(f"{row['entity'][:22]:<22} {row['state']:<12} {row['model'][:24]:<24} {row['metrics']['total_tokens']:>12,} {money(row['metrics']['costs']['estimated']['amount']):>12}")
    lines.extend(["", "Coverage: explicitly supplied events. Lifecycle ages independently of usage."])
    inputs = value.get("inputs")
    if inputs:
        rows = value.get("source_coverage", {}).get("inputs", [])
        lines.append(f"Selected inputs: {inputs['healthy_inputs']}/{len(inputs['inputs'])} readable. A read does not prove a live session.")
        for row in rows[:8]:
            count = row["records"]["total"] if row["records"] is not None else "unknown"
            age = row["event_age_seconds"]
            freshness = row["event_freshness"]
            evidence = f"{round(age)}s old · {freshness}" if age is not None and age >= 0 else freshness
            retained = " · retained history" if row["retained"] else ""
            lines.append(f"  {row['id']} ({row['format']}): {row['read_state']} · {count} records · {evidence}{retained}")
        if len(rows) > 8:
            lines.append(f"  {len(rows) - 8} more selected inputs; inspect --json for all read states.")
    app = value.get("app_threads")
    if app:
        from .metrics import seconds
        source_label = {"codex-local-app-server": "Codex runtime metadata", "codex-app-task-history": "Codex app tasks"}.get(app.get("source"), "App task inventory")
        current = app.get("freshness") == "recent" and app.get("read_state") == "following"
        status = lambda row: row["status"] if current else "unknown"
        supplied = app.get("threads", [])
        rank = {"active": 0, "idle": 1, "unknown": 2}
        matching = sorted((row for row in supplied if (not app_kind or row["kind"] == app_kind)
            and (not app_status or status(row) == app_status)
            and (not app_alias or app_alias.casefold() in row["alias"].casefold())),
            key=lambda row: (rank[status(row)], -seconds(row["updated_at"]), row["alias"]))
        lines.append(f"{source_label}: {app.get('read_state', 'unknown')} read · {app.get('freshness', 'unknown')} capture {app.get('captured_at', 'unavailable')}")
        lines.append(f"  {len(supplied)} supplied rows · {len(matching)} match app filters · showing {min(app_limit, len(matching))}. Incomplete coverage; ledger filters do not apply.")
        if not current:
            lines.append("  Current app status unknown. Retained rows are previous observations, not current activity.")
        for row in matching[:app_limit]:
            reported = status(row) if current else f"previously {row['status']}; current unknown"
            lines.append(f"  {row['alias']} · {row['kind']} · app status {reported} · updated {row['updated_at']}")
        if len(matching) > app_limit:
            lines.append(f"  {len(matching) - app_limit} matching app rows omitted; narrow --app-kind, --app-status or --app-alias, or increase --app-limit.")
        lines.append("  Reported app status is not model inference, worker lifecycle, token usage or billing.")
    processes = value.get("os_processes")
    if processes:
        if processes["state"] != "following":
            lines.append("OS process sample unavailable. Current process presence is unknown.")
        else:
            lines.append(f"OS sample: {len(processes['processes'])} matching executables at {processes['sampled_at']}; no verified session or worker links.")
            for row in processes["processes"][:8]:
                lines.append(f"  {row['executable'][:18]:18} PID {row['pid']:>7}  CPU {row['cpu_percent']:>5.1f}%  RSS {row['rss_bytes'] / 1048576:>6.1f} MiB")
            if processes["truncated"] or len(processes["processes"]) > 8:
                lines.append("  Additional matching processes omitted from this terminal view.")
    threshold = value.get("token_threshold")
    if threshold:
        lines.append(f"UTC-day observed token threshold: {threshold['observed_tokens']:,} / {threshold['limit_tokens']:,} · {threshold['state']}. Incomplete source coverage; no provider enforcement.")
    cost_thresholds = value.get("cost_thresholds")
    if cost_thresholds:
        for item in cost_thresholds["items"]:
            observed = item["observed_amount"] if item["observed_amount"] is not None else "unknown"
            lines.append(f"UTC-day {item['basis']} cost threshold: {observed} / {item['limit_amount']} {cost_thresholds['currency']} · {item['state']}. Known subset only; no spending enforcement.")
    insights = value.get("insights", {})
    pace = insights.get("pace", {})
    forecast = insights.get("forecast", {})
    lines.append(f"Selected-window pace: {pace.get('tokens_per_minute', 'unknown')} tokens/min; forecast {forecast.get('state', 'unknown')} ({forecast.get('confidence', 'unknown')} heuristic confidence)")
    lines.append(f"Estimated cost rate: {money(pace.get('estimated_cost_per_hour'))} {value['currency']}/hour; billed records excluded")
    for window in insights.get("quotas", {}).get("windows", []):
        remaining = window["remaining_percent"]
        lines.append(f"Quota {window['provider']}/{window['account']} {window['label']}: {'unknown' if remaining is None else str(remaining) + '% remaining'} · {window['freshness']} · captured {window['captured_at']}")
    return "\n".join(lines)


def watch_changes(previous, current):
    """Describe only ledger rows newly visible in this terminal's bounded view."""
    if previous is None or current["revision"] < previous["revision"]:
        return [], f"Watching ledger from revision {current['revision']}; earlier records are retained history."
    if current["revision"] == previous["revision"]:
        return [], "No new ledger revision observed."
    earlier = {(row["source"], row["id"]): row for row in previous["events"]}
    visible = []
    for row in current["events"]:
        prior = earlier.get((row["source"], row["id"]))
        if prior == row:
            continue
        detail = (f"{sum(row['tokens'].values()):,} recorded tokens" if row["kind"] == "usage"
                  else f"state {row['state']}" if row["kind"] == "status"
                  else f"tool {row['tool']} · {row['outcome']}")
        visible.append(f"  {'Revised' if prior else 'Added'} {row['kind']} · {row['source']} / {row['session']} · {detail}\n"
                       f"    Source {row['timestamp']} · observed {current['observed_at']}")
    if not visible:
        return [], "Ledger revision advanced, but no changed row is visible in the latest 100 matching records."
    return visible, f"{len(visible)} changed record{'s' if len(visible) != 1 else ''} visible in the latest snapshot."


def history_page(path, args):
    """Page one exact entity from a consistent ledger read without a running owner."""
    if not 1 <= args.limit <= 500 or args.after < 0 or (args.expected_revision is not None and args.expected_revision < 0):
        raise ValueError("Use limit 1–500, nonnegative offset and revision")
    if args.after and args.expected_revision is None:
        raise ValueError("Later history pages require the first ledger revision")
    query = filters({key: getattr(args, key) for key in
                     ("source", "session", "provider", "model", "surface", "project", "since", "until")
                     if getattr(args, key)})
    entity = label(args.entity, "entity")
    from .ledger_preview import _resolve_source, _same_file
    target, before = _resolve_source(path)
    try:
        with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True, timeout=10)) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            revision = db.execute("SELECT COALESCE(MAX(sequence),0) FROM events").fetchone()[0]
            revision += db.execute("SELECT COUNT(*) FROM corrections").fetchone()[0]
            rows = [validate(json.loads(payload)) for (payload,) in db.execute(
                "SELECT payload FROM events ORDER BY timestamp, source, event_id LIMIT 100001")]
    except (sqlite3.Error, ValueError, TypeError):
        raise ValueError("Ledger is not a compatible Monitoring database") from None
    if not _same_file(target, before):
        raise ValueError("Selected ledger changed during history read")
    if len(rows) > 100000:
        raise ValueError("Ledger exceeds this alpha's 100,000-event read limit")
    if args.expected_revision is not None and revision != args.expected_revision:
        raise ValueError("Ledger revision changed. Restart history at offset zero")
    matching = [row for row in rows if row["entity"] == entity and selected(row, query)]
    page = matching[args.after:args.after + args.limit]
    next_offset = args.after + args.limit if args.after + args.limit < len(matching) else None
    return {"schema": "monitoring.history-cli.v1", "revision": revision,
            "source": args.source, "session": args.session, "entity": entity,
            "offset": args.after, "total": len(matching), "events": page, "next_offset": next_offset}


def terminal_history(value):
    lines = [f"MONITORING AI ENTITY HISTORY · ledger revision {value['revision']}",
             f"{value['source']} / {value['session']} / {value['entity']}",
             f"{len(value['events'])} of {value['total']} matching records · oldest first · offset {value['offset']}"]
    for row in value["events"]:
        if row["kind"] == "usage":
            cost = row.get("cost")
            detail = f"{sum(row['tokens'].values()):,} tokens · {row['provider']} / {row['model']} · "
            detail += (f"{cost['amount']} {cost['currency']} · {cost['basis']} · {cost['provenance']}"
                       if cost else "cost unreported")
        elif row["kind"] == "status":
            detail = f"state {row['state']}"
        else:
            detail = f"{row.get('tool_category', 'unclassified')} · {row['tool']} · outcome {row['outcome']}"
        lines.append(f"{row['timestamp']}  {row['kind']}  {detail}")
    if value["next_offset"] is not None:
        lines.append(f"Next page: use the same selection with --after {value['next_offset']} --expected-revision {value['revision']}")
    lines.append("Recorded source times are not proof of current activity. Costs are supplied evidence in original currency only.")
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


def _print_source_preview(value):
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


def run(args):
    if args.command == "export-session":
        from .session_export import export_session
        from .result_capture import ResultPublicationUncertain
        query = {key: getattr(args, key) for key in ("provider", "model", "surface", "project", "since", "until") if getattr(args, key)}
        try:
            result = export_session(args.db, args.output, source=args.source, session=args.session,
                                    entity=args.entity, query=query, expected_revision=args.expected_revision)
        except ResultPublicationUncertain:
            print("Export file exists, but publication durability is uncertain. Verify it before retrying.", file=sys.stderr)
            raise SystemExit(2) from None
        print(json.dumps(result, indent=2))
        return
    if args.command == "ledger":
        from .ledger_backup import backup_ledger, inspect_ledger
        result = (backup_ledger(args.db, args.output) if args.ledger_action == "backup"
                  else inspect_ledger(args.db))
        print(json.dumps(result, indent=2))
        return
    if args.command == "setup":
        from .guided_setup import run_setup
        run_setup(args.output, args.from_selection, args.db)
        return
    if args.command == "sources":
        if args.source_action == "capture-openai-response":
            from .openai_receipt import capture_response
            from .producer import EventWriteError
            from .result_capture import ResultPublicationUncertain
            try:
                receipt = capture_response(args.file, args.output, source=args.source_id, project=args.project,
                                           session=args.session, append=args.append)
            except EventWriteError:
                print("Receipt append outcome is unknown. Preserve the file and inspect it before retrying.", file=sys.stderr)
                raise SystemExit(2) from None
            except ResultPublicationUncertain:
                print("Capture file exists, but publication durability is uncertain. Verify it before retrying.", file=sys.stderr)
                raise SystemExit(2) from None
            print(json.dumps(receipt))
            return
        if args.source_action == "capture-codex-host":
            from .codex_host import capture_host
            print(json.dumps(capture_host(args.socket, args.cwd, args.output, replace=args.replace)))
            return
        if args.source_action == "capture-app-threads":
            from .app_threads import capture_app_threads
            print(json.dumps(capture_app_threads(sys.stdin.buffer, args.output, args.captured_at, replace=args.replace,
                                                source="codex-app-task-history" if args.codex_history else "codex-app-task-inventory")))
            return
        from .source_setup import add_selection, create_selection, check_selection, select_claude_stream
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
            if args.source_action == "capture-claude-stream":
                from .claude_stream import capture_stream
                result = capture_stream(sys.stdin.buffer, args.output,
                                        source=args.source_id, project=args.project,
                                        forward=sys.stdout.buffer if args.forward else None,
                                        result_output=args.result_output,
                                        quota_output=args.quota_output, quota_account=args.quota_account,
                                        append=args.append)
                print(json.dumps(result), file=sys.stderr if args.forward else sys.stdout)
                if (result.get("result_summary") in {"failed", "uncertain"} or
                        result.get("quota_observation") in {"failed", "partial", "uncertain"}):
                    raise ValueError("Optional observation publication could not be confirmed")
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
            if args.source_action == "select-claude-stream":
                select_claude_stream(args.output, status_file=args.status_file,
                                     result_file=args.result_file, source_id=args.source_id,
                                     project=args.project)
                print("Created a private Claude status/result selection. Run sources check after capture. A quota file, if supplied, is selected separately with --quota-file.")
                return
            if args.source_action == "preview":
                from .source_readiness import preview_selection
                value = preview_selection(args.sources, args.db)
                if args.json:
                    print(json.dumps(value, indent=2))
                else:
                    _print_source_preview(value)
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
    cost_limits = {}
    if args.command in ("serve", "top"):
        if args.estimated_cost_threshold is not None:
            cost_limits["estimated"] = args.estimated_cost_threshold
        if args.billed_cost_threshold is not None:
            cost_limits["billed"] = args.billed_cost_threshold
        if cost_limits:
            from .cost_thresholds import validate_limits
            cost_limits = validate_limits(cost_limits)
    if args.command == "history":
        value = history_page(args.db, args)
        print(json.dumps(value, indent=2) if args.json else terminal_history(value))
        return
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
        from .app_threads import read_app_threads
        serve(Observatory(store, pricing, fx, loader, quota_loader=quota_loader(args),
                          source_observer=observer,
                          app_threads_loader=(lambda: read_app_threads(args.app_threads)) if args.app_threads else None,
                          cached_usage_loader=cache_loader,
                          codeburn_activity_loader=activity_loader,
                          session_cache_loader=session_cache_loader,
                          process_observer=process_observer,
                          token_threshold=args.token_threshold,
                          cost_thresholds=cost_limits,
                          quota_book=demo_quotas() if args.demo and not args.quota_file and not args.codex_quota else None), args.port)
    else:
        if args.watch and (args.json or not sys.stdout.isatty()):
            raise ValueError("--watch needs an interactive terminal and cannot combine with --json")
        app_controls = any(value is not None for value in (args.app_kind, args.app_status, args.app_alias, args.app_limit))
        if app_controls and (not args.app_threads or args.json):
            raise ValueError("App view controls require --app-threads and human-readable output")
        if args.app_limit is not None and not 1 <= args.app_limit <= 100:
            raise ValueError("--app-limit must be between 1 and 100")
        if args.process_interval is not None and not args.processes:
            raise ValueError("--process-interval requires --processes")
        source_observer = None
        if args.sources:
            from .sources import load_sources
            from .source_observer import SourceObserver
            source_observer = SourceObserver(load_sources(args.sources))
        process_observer = None
        if args.processes:
            if sys.platform != "darwin":
                raise ValueError("OS process sampling currently supports macOS")
            from .process_observer import ProcessObserver
            process_observer = ProcessObserver(interval_seconds=args.process_interval if args.process_interval is not None else 5)
        last_process_read = float("-inf")
        query = {key: getattr(args, key) for key in (*DIMENSIONS, "since", "until", "state") if getattr(args, key)}
        quota_read = quota_loader(args)
        book, last_quota_read = None, float("-inf")
        quota_error = False
        prior_watch = None
        recent_changes = []
        app_capture = None
        while True:
            if source_observer is not None:
                inputs = source_observer.refresh(store)
            if process_observer is not None and time.monotonic() - last_process_read >= process_observer.interval_seconds:
                process_observer.refresh()
                last_process_read = time.monotonic()
            if quota_read and time.monotonic() - last_quota_read >= 60:
                try:
                    book = quota_read()
                    quota_error = False
                except (ValueError, OSError):
                    quota_error = True
                last_quota_read = time.monotonic()
            revision, rows = store.read()
            value = snapshot(revision, rows, query, pricing, fx, quota_book=book)
            if args.app_threads:
                from .app_threads import app_threads_snapshot, read_app_threads
                app_capture = app_threads_snapshot(lambda: read_app_threads(args.app_threads), app_capture, value["observed_at"])
                value["app_threads"] = app_capture
            if source_observer is not None:
                value["inputs"] = inputs
                value["source_coverage"] = source_observer.coverage(now=value["observed_at"])
            if process_observer is not None:
                value["os_processes"] = process_observer.snapshot()
            if args.token_threshold is not None:
                from .token_threshold import evaluate_token_threshold
                value["token_threshold"] = evaluate_token_threshold(rows, limit=args.token_threshold,
                                                                       now=value["observed_at"])
            if cost_limits:
                from .cost_thresholds import evaluate_cost_thresholds
                value["cost_thresholds"] = evaluate_cost_thresholds(rows, limits=cost_limits,
                    pricing=pricing, fx=fx, now=value["observed_at"])
            value["insights"]["quotas"]["read_status"] = dict(state="unavailable" if quota_error else "following" if quota_read else "unconfigured")
            if args.watch:
                changes, change_note = watch_changes(prior_watch, value)
                recent_changes = (changes + recent_changes)[:5]
                prior_watch = {"revision": value["revision"], "events": value["events"]}
                print("\033[2J\033[H", end="")
            print(json.dumps(value, indent=2) if args.json else terminal(value,
                app_kind=args.app_kind, app_status=args.app_status, app_alias=args.app_alias,
                app_limit=args.app_limit if args.app_limit is not None else 20), flush=True)
            if args.watch:
                print("\nLedger changes in this terminal · latest five visible observations")
                print(change_note)
                print("\n".join(recent_changes) if recent_changes else "No changes observed after this terminal started watching.")
                print("Source times can predate observation. The latest-100 comparison can miss intermediate rows; a new record does not prove a running worker.", flush=True)
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
