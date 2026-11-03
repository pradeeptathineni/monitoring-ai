"""Save and inspect a consistent private copy of an existing ledger."""

from contextlib import closing
import os
from pathlib import Path
import secrets
import sqlite3

from .ledger_preview import _copy_source, _resolve_source


def inspect_ledger(path):
    """Check a selected ledger without changing its accepted history."""
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
        # SQLite backup copies the source's WAL mode. Publish a standalone file.
        try:
            with closing(sqlite3.connect(temporary)) as db:
                if db.execute("PRAGMA journal_mode=DELETE").fetchone() != ("delete",):
                    raise ValueError("Backup could not be made standalone")
        except sqlite3.Error:
            raise ValueError("Backup could not be made standalone") from None
        result = inspect_ledger(temporary)
        _clean_sidecars(temporary)
        fd = os.open(temporary, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        # Hard-link publication refuses an existing output without a replacement gap.
        try:
            os.link(temporary, destination, follow_symlinks=False)
        except FileExistsError:
            raise FileExistsError("Backup output already exists") from None
        except OSError:
            raise ValueError("Backup could not be published") from None
        try:
            _sync_directory(parent)
        except OSError:
            raise ValueError("Backup exists, but directory sync failed; verify the output") from None
    finally:
        _clean_sidecars(temporary)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return result


def _clean_sidecars(temporary):
    for suffix in ("-wal", "-shm", "-journal"):
        try:
            os.unlink(str(temporary) + suffix)
        except FileNotFoundError:
            pass


def _sync_directory(parent):
    fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
