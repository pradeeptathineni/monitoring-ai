"""Disposable read-only preview copies of a Monitoring AI ledger.

snapshot_ledger() opens an explicit source only through a read-only SQLite URI.
It copies one consistent snapshot, including committed WAL frames, into a
private temporary directory. It initializes Store on that copy only and removes
the copy when the context exits. The source file, its journal mode and its
accepted history are never changed.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile
import time

from .store import Store

__all__ = [
    "BACKUP_PAGES",
    "COPY_ERROR",
    "COPY_TIMEOUT_SECONDS",
    "MAX_SOURCE_BYTES",
    "PreviewLedger",
    "READ_ERROR",
    "RETRY_SLEEP_SECONDS",
    "SCHEMA_ERROR",
    "SIZE_ERROR",
    "SOURCE_BUSY_TIMEOUT_SECONDS",
    "SOURCE_ERROR",
    "TIMEOUT_ERROR",
    "snapshot_ledger",
]

MAX_SOURCE_BYTES = 512 * 1024 * 1024
COPY_TIMEOUT_SECONDS = 5.0
BACKUP_PAGES = 256
RETRY_SLEEP_SECONDS = 0.02
# Busy-handler wait per SQLite call. Keep it short so the copy deadline stays tight.
SOURCE_BUSY_TIMEOUT_SECONDS = 0.1

SOURCE_ERROR = "Ledger preview source must be an existing regular ledger file"
SCHEMA_ERROR = "Ledger preview source is not a compatible ledger database"
SIZE_ERROR = "Ledger preview source exceeds the preview size limit"
TIMEOUT_ERROR = "Ledger preview copy did not finish in time"
COPY_ERROR = "Ledger preview copy could not be created"
READ_ERROR = "Ledger preview could not be read within preview limits"

_EVENT_COLUMNS = ("sequence", "source", "event_id", "timestamp", "payload")
_COPY_NAME = "ledger.sqlite3"
_SQLITE_BUSY = 5
_SQLITE_LOCKED = 6
_SQLITE_CANTOPEN = 14


@dataclass(frozen=True, slots=True)
class PreviewLedger:
    """A disposable Store copy plus the revision and event count read from it."""

    store: Store
    revision: int
    events: int


class _CopyTimedOut(Exception):
    pass


class _CopyTooLarge(Exception):
    pass


@contextmanager
def snapshot_ledger(path=None):
    """Yield a PreviewLedger backed by a private copy that is removed on exit.

    path=None yields a new empty Store with revision 0 and 0 events. Preview
    failures raise ValueError with a fixed message. Exceptions raised inside
    the caller's block propagate unchanged after cleanup.
    """
    try:
        workdir = Path(tempfile.mkdtemp(prefix="monitoring-ai-preview-"))
    except OSError:
        raise ValueError(COPY_ERROR) from None
    try:
        database = workdir / _COPY_NAME
        if path is not None:
            _copy_source(path, database)
        preview = _open_preview(database)
        yield preview
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _copy_source(path, database):
    target, before = _resolve_source(path)
    _create_private_file(database)
    deadline = time.monotonic() + COPY_TIMEOUT_SECONDS
    source = copy = None
    try:
        try:
            # Path.as_uri() percent-escapes '?', '#' and '%'. mode=ro never
            # creates the file. Without immutable=1, SQLite reads committed
            # WAL frames and leaves out uncommitted writes.
            source = sqlite3.connect(
                target.as_uri() + "?mode=ro",
                uri=True,
                timeout=SOURCE_BUSY_TIMEOUT_SECONDS,
            )
        except (sqlite3.Error, OSError, ValueError):
            raise ValueError(SOURCE_ERROR) from None
        page_size = _inspect_source(source, deadline)
        try:
            copy = sqlite3.connect(database)
        except sqlite3.Error:
            raise ValueError(COPY_ERROR) from None
        _backup(source, copy, page_size, deadline)
    finally:
        _close(copy)
        _close(source)
    # Reject a source that was replaced between the initial check and the copy.
    if not _same_file(target, before):
        raise ValueError(SOURCE_ERROR)
    _verify_copy(database)


def _resolve_source(path):
    try:
        raw = os.fspath(path)
        if isinstance(raw, bytes):
            raw = os.fsdecode(raw)
        candidate = Path(raw)
        if not raw or candidate.name in ("", ".", ".."):
            raise ValueError(SOURCE_ERROR)
        # Canonicalize parent aliases only. The final component must not be a link.
        target = candidate.parent.resolve(strict=True) / candidate.name
        info = os.lstat(target)
    except (OSError, RuntimeError, TypeError, ValueError):
        raise ValueError(SOURCE_ERROR) from None
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(SOURCE_ERROR)
    if info.st_size > MAX_SOURCE_BYTES:
        raise ValueError(SIZE_ERROR)
    return target, info


def _create_private_file(database):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(database, flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
    except OSError:
        raise ValueError(COPY_ERROR) from None


def _inspect_source(db, deadline):
    while True:
        try:
            compatible = _has_event_schema(db)
            page_size = db.execute("PRAGMA page_size").fetchone()[0]
        except sqlite3.Error as exc:
            if _error_code(exc) == _SQLITE_CANTOPEN:
                raise ValueError(SOURCE_ERROR) from None
            if not _is_busy(exc):
                raise ValueError(SCHEMA_ERROR) from None
            if time.monotonic() >= deadline:
                raise ValueError(TIMEOUT_ERROR) from None
            time.sleep(RETRY_SLEEP_SECONDS)
            continue
        if not compatible:
            raise ValueError(SCHEMA_ERROR)
        return int(page_size)


def _has_event_schema(db):
    kinds = db.execute(
        "SELECT type FROM sqlite_master WHERE name = 'events' COLLATE NOCASE"
    ).fetchall()
    if kinds != [("table",)]:
        return False
    columns = tuple(row[1] for row in db.execute("PRAGMA table_info(events)").fetchall())
    return columns == _EVENT_COLUMNS


def _backup(source, copy, page_size, deadline):
    def progress(status, remaining, total):
        # Python calls this after every step, including BUSY/LOCKED retries.
        if total * page_size > MAX_SOURCE_BYTES:
            raise _CopyTooLarge
        if time.monotonic() >= deadline:
            raise _CopyTimedOut

    try:
        source.backup(
            copy,
            pages=BACKUP_PAGES,
            progress=progress,
            sleep=RETRY_SLEEP_SECONDS,
        )
    except _CopyTooLarge:
        raise ValueError(SIZE_ERROR) from None
    except _CopyTimedOut:
        raise ValueError(TIMEOUT_ERROR) from None
    except sqlite3.Error as exc:
        raise ValueError(TIMEOUT_ERROR if _is_busy(exc) else COPY_ERROR) from None


def _same_file(target, before):
    try:
        after = os.lstat(target)
    except OSError:
        return False
    return stat.S_ISREG(after.st_mode) and (after.st_dev, after.st_ino) == (
        before.st_dev,
        before.st_ino,
    )


def _verify_copy(database):
    try:
        size = database.stat().st_size
    except OSError:
        raise ValueError(COPY_ERROR) from None
    if size > MAX_SOURCE_BYTES:
        raise ValueError(SIZE_ERROR)
    db = None
    try:
        db = sqlite3.connect(database)
        compatible = _has_event_schema(db)
    except sqlite3.Error:
        compatible = False
    finally:
        _close(db)
    if not compatible:
        raise ValueError(SCHEMA_ERROR)


def _open_preview(database):
    try:
        store = Store(database)
    except (sqlite3.Error, OSError, ValueError):
        raise ValueError(COPY_ERROR) from None
    try:
        revision, rows = store.read()
    except (sqlite3.Error, OSError, ValueError, RecursionError):
        raise ValueError(READ_ERROR) from None
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(READ_ERROR)
    return PreviewLedger(store=store, revision=int(revision), events=len(rows))


def _close(db):
    if db is not None:
        try:
            db.close()
        except sqlite3.Error:
            pass


def _error_code(exc):
    code = getattr(exc, "sqlite_errorcode", None)
    return code & 0xFF if isinstance(code, int) else None


def _is_busy(exc):
    code = _error_code(exc)
    if code is not None:
        return code in (_SQLITE_BUSY, _SQLITE_LOCKED)
    text = str(exc).lower()
    return "locked" in text or "busy" in text
