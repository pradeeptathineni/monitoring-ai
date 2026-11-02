"""Create an explicit private selection or check it without changing a ledger."""

import json
import os
from pathlib import Path
import tempfile

from .metrics import now_iso
from .sources import load_sources
from .source_coverage import source_summary


def create_selection(output, *, file, id, format="normalized", project=None):
    """Write one validated selection privately and refuse any existing target."""
    selected = Path(output).absolute()
    target = selected.parent.resolve(strict=True) / selected.name
    entry = dict(id=id, format=format, path=str(file))
    if project is not None:
        entry["project"] = project
    descriptor, temporary = tempfile.mkstemp(prefix=".monitoring-sources-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(dict(schema="monitoring.sources.v1", sources=[entry]), stream, indent=2)
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
    for source in load_sources(path):
        try:
            rows = source.load(live=True)
            summary = source_summary(source, rows, sampled_at=now_iso())
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
                checks=checks, note="Checks parsing and supplied evidence only. Does not import history, reconcile overlapping inputs or establish live workers.")
