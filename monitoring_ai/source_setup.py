"""Create an explicit private selection or check it without changing a ledger."""

import json
import os
from pathlib import Path
import tempfile

from .metrics import now_iso
from .sources import load_sources
from .source_coverage import source_summary
from .source_overlap import NATIVE_FORMATS, MESSAGE, identities
from .codex_lineage import CodexLineageError, MESSAGE as CODEX_LINEAGE_MESSAGE


def select_claude_stream(output, *, status_file, result_file, source_id, project):
    """Create one status/result selection with a checked shared event alias.

    Missing outputs are allowed so an operator can configure the observer
    before a CLI invocation starts. Existing selected files are parsed and
    checked locally before the private manifest is published.
    """
    from .adapters import load
    from .events import label
    from .sources import _source_path

    source_id, project = label(source_id, "source"), label(project, "project")
    if len(source_id) > 148:
        raise ValueError("Claude stream source alias is too long for its status input ID")
    status_path, status_identity = _source_path(str(status_file))
    result_path, result_identity = _source_path(str(result_file))
    target = Path(output).absolute()
    target = target.parent.resolve() / target.name
    if not status_path.parent.is_dir() or not result_path.parent.is_dir():
        raise ValueError("Claude capture outputs need existing private directories")
    if any(target.parent == path.parent and target.name.casefold() == path.name.casefold()
           for path in (status_path, result_path)):
        raise ValueError("Claude selection output must differ from capture outputs")
    status_rows = load(status_path, "normalized", live=True) if status_identity else []
    result_rows = load(result_path, "claude-results", source_id, project, live=True) if result_identity else []
    if any(row["source"] != source_id or row["project"] != project for row in status_rows):
        raise ValueError("Claude status file does not match the selected source and project aliases")
    if status_rows and result_rows and not {row["session"] for row in status_rows} & {row["session"] for row in result_rows}:
        raise ValueError("Claude status and result files do not share an observed session")
    return _write_selection(output, [
        _entry(file=status_path, id=source_id + "-status", format="normalized", project=None),
        _entry(file=result_path, id=source_id, format="claude-results", project=project),
    ])


def create_selection(output, *, file, id, format="normalized", project=None):
    """Write one validated selection privately and refuse any existing target."""
    return _write_selection(output, [_entry(file=file, id=id, format=format, project=project)])


def add_selection(sources, output, *, file, id, format="normalized", project=None):
    """Create a new private manifest with one additional explicit source."""
    existing = load_sources(sources)  # Closed schema and file metadata only.
    entries = [_entry(file=source.path, id=source.id, format=source.format, project=source.project)
               for source in existing]
    entries.append(_entry(file=file, id=id, format=format, project=project))
    return _write_selection(output, entries)


def _entry(*, file, id, format, project):
    entry = dict(id=id, format=format, path=str(file))
    if project is not None:
        entry["project"] = project
    return entry


def _write_selection(output, entries):
    selected = Path(output).absolute()
    target = selected.parent.resolve(strict=True) / selected.name
    descriptor, temporary = tempfile.mkstemp(prefix=".monitoring-sources-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(dict(schema="monitoring.sources.v1", sources=entries), stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Reuse the same closed schema and duplicate/path checks as server startup.
        # Validation inspects metadata only, never source content.
        sources = load_sources(temporary)
        os.link(temporary, target, follow_symlinks=False)
        return sources
    finally:
        Path(temporary).unlink(missing_ok=True)


def check_selection(path):
    """Read only selected files; do not create or mutate an event ledger."""
    checks = []
    accepted_rows = {}
    seen = {family: set() for family in NATIVE_FORMATS}
    sources = load_sources(path)
    for source in sources:
        try:
            rows = source.load(live=True)
            accepted_rows[source.id] = rows
            summary = source_summary(source, rows, sampled_at=now_iso())
            if source.format in NATIVE_FORMATS:
                keys = identities(rows)
                if seen[source.format] & keys:
                    check = dict(id=source.id, format=source.format, state="invalid", summary=summary,
                                 error=MESSAGE)
                    checks.append(check)
                    continue
                seen[source.format].update(keys)
            check = dict(id=source.id, format=source.format, state="readable", summary=summary, error=None)
        except OSError:
            check = dict(id=source.id, format=source.format, state="unavailable", summary=None,
                         error="Selected file is unavailable. Check its existence and read access.")
        except CodexLineageError:
            check = dict(id=source.id, format=source.format, state="invalid", summary=None,
                         error=CODEX_LINEAGE_MESSAGE)
        except ValueError:
            check = dict(id=source.id, format=source.format, state="invalid", summary=None,
                         error="Selected file does not match the format or normalized schema.")
        except Exception:
            check = dict(id=source.id, format=source.format, state="invalid", summary=None,
                         error="Selected file could not be processed. Check the documented source format.")
        checks.append(check)
    by_id = {row["id"]: row for row in checks}
    for source in sources:
        if source.format != "claude-results" or source.id + "-status" not in by_id:
            continue
        status_id = source.id + "-status"
        status, result = accepted_rows.get(status_id), accepted_rows.get(source.id)
        if status is None or result is None:
            continue
        if (any(row["kind"] not in ("status", "tool") or row["surface"] != "claude-cli-stream" or
                row["source"] != source.id or row["project"] != source.project for row in status) or
                status and result and not {row["session"] for row in status} & {row["session"] for row in result}):
            by_id[status_id].update(state="invalid", error="Claude capture files do not share the selected alias and session.")
    return dict(schema="monitoring.source-check.v1", observed_at=now_iso(), read_only=True,
                scope="explicit-input-files", all_readable=all(check["state"] == "readable" for check in checks),
                checks=checks, note="Checks parsing, supplied evidence and repeated native identities within this selection. Does not import history, detect all collector overlap or establish live workers.")
