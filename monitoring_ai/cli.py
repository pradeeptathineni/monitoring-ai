"""Run the local observatory, import an explicit source or inspect usage in a terminal."""

import argparse
import json
from pathlib import Path
import sqlite3
import sys
import time

from . import __version__
from .adapters import load
from .demo import demo_events, stream_demo, write_demo
from .metrics import DIMENSIONS, Exchange, Pricing, snapshot
from .server import Observatory, serve
from .store import Store


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
        if name != "top":
            command.add_argument("--file", type=Path, required=name == "import")
            command.add_argument("--format", choices=["normalized", "claude", "codex"], default="normalized")
            command.add_argument("--source-id", help="Stable alias for this native input, reused across replay")
            command.add_argument("--project", help="Public-safe project alias for native input")
        if name == "serve":
            command.add_argument("--port", type=int, default=5274)
            command.add_argument("--demo", action="store_true", help="Seed an empty ledger with fictional data")
        if name == "top":
            command.add_argument("--json", action="store_true")
            command.add_argument("--watch", action="store_true", help="Refresh every two seconds in this terminal")
            for key in (*DIMENSIONS, "since", "until", "state"):
                command.add_argument("--" + key)
    demo = sub.add_parser("demo", help="Write fictional normalized JSONL, optionally keep appending")
    demo.add_argument("file", type=Path)
    demo.add_argument("--stream", action="store_true")
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
    return "\n".join(lines)


def run(args):
    if args.command == "demo":
        (stream_demo if args.stream else write_demo)(args.file)
        return
    store = Store(args.db)
    if args.command == "import":
        result = store.ingest(load(args.file, args.format, args.source_id, args.project))
        print(json.dumps(result))
        return
    if len(args.currency) != 3 or not args.currency.isupper():
        raise ValueError("Use an uppercase three-letter currency code")
    pricing, fx = Pricing(args.pricing), Exchange(args.currency, args.fx)
    if args.command == "serve":
        if not 0 <= args.port <= 65535:
            raise ValueError("Invalid port")
        if args.demo:
            if args.file:
                raise ValueError("Choose --demo or --file")
            if store.read()[0]:
                raise ValueError("Demo seeding requires an empty ledger")
            store.ingest(demo_events())
        loader = (lambda: load(args.file, args.format, args.source_id, args.project, live=True)) if args.file else None
        serve(Observatory(store, pricing, fx, loader), args.port)
    else:
        if args.watch and (args.json or not sys.stdout.isatty()):
            raise ValueError("--watch needs an interactive terminal and cannot combine with --json")
        query = {key: getattr(args, key) for key in (*DIMENSIONS, "since", "until", "state") if getattr(args, key)}
        while True:
            revision, rows = store.read()
            value = snapshot(revision, rows, query, pricing, fx)
            if args.watch:
                print("\033[2J\033[H", end="")
            print(json.dumps(value, indent=2) if args.json else terminal(value), flush=True)
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
