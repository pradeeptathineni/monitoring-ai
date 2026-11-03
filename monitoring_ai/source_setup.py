"""Create an explicit private selection or check it without changing a ledger."""

import json
import os
from pathlib import Path
import tempfile

from .metrics import now_iso
from .sources import load_sources
from .source_coverage import source_summary
from .source_overlap import NATIVE_FORMATS, MESSAGE, identities


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
    seen = {family: set() for family in NATIVE_FORMATS}
    for source in load_sources(path):
        try:
            rows = source.load(live=True)
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
        except ValueError:
            check = dict(id=source.id, format=source.format, state="invalid", summary=None,
                         error="Selected file does not match the format or normalized schema.")
        except Exception:
            check = dict(id=source.id, format=source.format, state="invalid", summary=None,
                         error="Selected file could not be processed. Check the documented source format.")
        checks.append(check)
    return dict(schema="monitoring.source-check.v1", observed_at=now_iso(), read_only=True,
                scope="explicit-input-files", all_readable=all(check["state"] == "readable" for check in checks),
                checks=checks, note="Checks parsing, supplied evidence and repeated native identities within this selection. Does not import history, detect all collector overlap or establish live workers.")
