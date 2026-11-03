"""Small metadata-only helpers for explicitly owned runtime producers.

This module records caller-supplied observations. It does not inspect or manage
the process whose state the caller reports.
"""

import json
import os
from pathlib import Path
import stat
import threading

from .events import SCHEMA, validate
from .store import stable_id

MAX_BYTES = 32 * 1024 * 1024
MAX_RECORDS = 50_000


def status_event(*, id, source, project, session, entity, entity_type, state,
                 timestamp, parent=None, provider="unknown", model="unknown",
                 surface="runtime-observer"):
    """Build one validated lifecycle observation from explicit caller facts."""
    return validate({
        "schema": SCHEMA,
        "id": id,
        "source": source,
        "project": project,
        "session": session,
        "entity": entity,
        "entity_type": entity_type,
        "parent": parent,
        "provider": provider,
        "model": model,
        "surface": surface,
        "timestamp": timestamp,
        "kind": "status",
        "state": state,
    })


def session_alias(native_id):
    """Hash a caller-selected native session identity into the ledger alias form."""
    if not isinstance(native_id, str) or not native_id or len(native_id) > 1000:
        raise ValueError("Native session identity must be a nonempty string")
    return "s-" + stable_id(native_id)


class EventWriter:
    """Append validated normalized observations to a new private JSONL file.

    After an I/O error, don't retry automatically or erase the tail. Preserve
    the file and recover only by replaying the same immutable event ID and timestamp.
    """

    def __init__(self, path):
        try:
            selected = Path(path).absolute()
            name = selected.name
            if not name or name in {".", ".."}:
                raise ValueError
            parent = selected.parent.resolve(strict=True)
            if not parent.is_dir():
                raise ValueError
            target = parent / name
            flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND
                     | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
            fd = os.open(target, flags, 0o600)
            try:
                os.fchmod(fd, 0o600)
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise OSError("not a regular file")
            except Exception:
                os.close(fd)
                raise
        except Exception:
            raise ValueError("Unable to create a new private event file") from None
        self._fd = fd
        self._lock = threading.Lock()
        self._bytes = 0
        self._records = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False

    def write(self, event):
        """Validate, append and flush one normalized event."""
        normalized = validate(event)
        payload = (json.dumps(normalized, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        with self._lock:
            if self._fd is None:
                raise ValueError("Event writer is closed")
            if self._records + 1 > MAX_RECORDS or self._bytes + len(payload) > MAX_BYTES:
                raise ValueError("Event file limit reached")
            offset = 0
            try:
                while offset < len(payload):
                    written = os.write(self._fd, payload[offset:])
                    if written <= 0:
                        raise OSError("short write")
                    offset += written
                os.fsync(self._fd)
                self._bytes += len(payload)
                self._records += 1
            except OSError:
                fd, self._fd = self._fd, None
                try:
                    os.close(fd)
                except OSError:
                    pass
                raise EventWriteError from None
        return normalized

    def close(self):
        with self._lock:
            if self._fd is None:
                return
            fd, self._fd = self._fd, None
            try:
                os.close(fd)
            except OSError:
                raise OSError("Unable to close metadata event file") from None


class EventWriteError(OSError):
    """A write or flush failed after its append outcome became uncertain."""

    outcome = "unknown"

    def __init__(self):
        super().__init__("Observation write outcome is unknown. Event writer is closed.")
