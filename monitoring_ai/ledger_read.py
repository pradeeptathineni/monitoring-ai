"""Read a bounded consistent ledger without initializing the selected database."""

from contextlib import closing
import json
import sqlite3

from .events import validate
from .ledger_preview import _resolve_source, _same_file


def read_ledger(path):
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
        raise ValueError("Selected ledger changed during read")
    if len(rows) > 100000:
        raise ValueError("Ledger exceeds this alpha's 100,000-event read limit")
    return revision, rows
