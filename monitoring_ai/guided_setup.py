"""Prompt for one explicitly chosen local source without changing a running owner."""

import os
from pathlib import Path
import shlex
import stat
import sys

from .source_setup import add_selection, check_selection, create_selection
from .sources import SourceManifestError, load_sources


def _answer(prompt):
    try:
        return input(prompt).strip()
    except EOFError:
        print("\nSetup stopped before writing a selection.", file=sys.stderr)
        raise SystemExit(2) from None


def _private_default_directory():
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    if not base.is_absolute():
        raise ValueError("User config directory must be absolute")
    directory = base / "monitoring-ai"
    for target in (base, directory):
        try:
            os.mkdir(target, 0o700)
        except FileExistsError:
            pass
        info = target.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("Private output directory is not a directory")
    return directory


def run_setup(output=None, previous=None, db=None):
    """Create a private immutable selection and check only its chosen files."""
    if previous is not None:
        load_sources(previous)  # Validate configuration before asking for new input.
    selected = Path(output).expanduser().absolute() if output else None
    default_output = selected is None
    if selected is not None and selected.exists():
        print("Selection output already exists. Choose a new --output; the running service was not changed.", file=sys.stderr)
        raise SystemExit(2)

    print("MONITORING AI SOURCE SETUP")
    print("Choose one local file you are authorized to read. No directories are scanned or agents contacted.")
    print("The file may contain private conversation text; only parsed metrics enter the local ledger.")
    print("Formats: normalized (Monitoring JSONL), claude (Claude Code session JSONL), codex (Codex session JSONL), claude-results (captured result JSONL), otlp-jsonl (completed OTLP trace batches).")
    raw = _answer("Absolute file path: ")
    if not raw or not Path(raw).is_absolute():
        raise ValueError("Select an absolute file path")
    source_file = Path(raw)
    format_name = _answer("Format [normalized]: ") or "normalized"
    if format_name not in {"normalized", "claude", "codex", "claude-results", "otlp-jsonl"}:
        raise ValueError("Unsupported source format")
    alias = _answer("Source alias (short, stable, no path or private name): ")
    project = None if format_name == "normalized" else _answer("Project alias (short, no path or private name): ")

    if selected is None:
        directory = _private_default_directory()
        selected = directory / ("sources-next.json" if previous else "sources.json")
    try:
        if previous:
            add_selection(previous, selected, file=source_file, id=alias, format=format_name, project=project)
        else:
            create_selection(selected, file=source_file, id=alias, format=format_name, project=project)
    except FileExistsError:
        print("Selection output already exists. Choose a new --output; no file was overwritten.", file=sys.stderr)
        raise SystemExit(2) from None
    except SourceManifestError as error:
        print(f"Source selection rejected: {error}", file=sys.stderr)
        raise SystemExit(2) from None

    from .cli import _print_source_check, _print_source_preview
    print("\nPrivate selection saved. Existing selection and running service are unchanged.")
    print("Keep the selection private and out of version control.")
    if default_output:
        print("The default config directory is outside the current project.")
    check = check_selection(selected)
    _print_source_check(check)
    if not check["all_readable"]:
        print("The selection is saved, but at least one input is not readable. Fix the selected source before serving.")
        raise SystemExit(1)
    if db is not None:
        from .source_readiness import preview_selection
        preview = preview_selection(selected, db)
        _print_source_preview(preview)
        if not preview["ready"]:
            print("The new selection is saved, but its preview did not accept every input. Review the conflict before restarting an owner.")
            raise SystemExit(1)
    selected_argument = shlex.quote(str(selected))
    ledger_argument = shlex.quote(str(db)) if db is not None else ".monitoring-ai/events.sqlite"
    if db is None:
        print("\nPreview ingestion: python3 -m monitoring_ai sources preview --sources " + selected_argument)
    print("Start or deliberately restart the local owner with: python3 -m monitoring_ai serve --sources "
          + selected_argument + " --db " + ledger_argument)
    print("A running owner keeps its prior selection until restarted. The dashboard and Deep read the same qualified source coverage.")
