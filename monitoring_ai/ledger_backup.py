"""Save and inspect a consistent private copy of an existing ledger."""

from contextlib import closing
import os
from pathlib import Path
import secrets
import sqlite3

from .ledger_preview import _copy_source, _resolve_source


def inspect_ledger(path):
    """Check a selected ledger without creating or changing it."""
    target, _ = _resolve_source(path)
    try:
        with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("BEGIN")
            if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise ValueError("Ledger integrity check failed")
            events = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            sequence = db.execute("SELECT COALESCE(MAX(sequence),0) FROM events").fetchone()[0]
            corrections = db.execute("SELECT COUNT(*) FROM corrections").fetchone()[0]
            versions = db.execute("SELECT COUNT(*) FROM native_usage_versions").fetchone()[0]
    except sqlite3.Error:
        raise ValueError("Ledger is not a compatible Monitoring database") from None
    return {"events": events, "corrections": corrections, "native_usage_versions": versions,
            "revision": sequence + corrections, "bytes": target.stat().st_size}


def backup_ledger(source, output):
    """Copy committed WAL-inclusive history to a new private file only."""
    target = Path(output)
    try:
        parent = target.parent.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError("Backup output directory is unavailable") from None
    destination = parent / target.name
    temporary = parent / f".{target.name}.{secrets.token_hex(16)}.tmp"
    try:
        _copy_source(source, temporary)
        result = inspect_ledger(temporary)
        # Hard-link publication refuses an existing output without a replacement gap.
        os.link(temporary, destination, follow_symlinks=False)
    finally:
        if temporary.is_file() and not temporary.is_symlink():
            os.unlink(temporary)
    return result
