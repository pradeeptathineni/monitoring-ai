"""Publish one selected session as importable private normalized JSONL."""

import hashlib
import json
import os
from pathlib import Path
import tempfile

from .adapters import MAX_BYTES
from .events import label, validate
from .ledger_backup import inspect_ledger, _sync_directory
from .ledger_preview import snapshot_ledger
from .metrics import filters, selected
from .result_capture import ResultPublicationUncertain

MAX_EXPORT_EVENTS = 50_000


def export_session(database, output, *, source, session, entity=None, query=None, expected_revision=None):
    query = dict(query or {})
    if "state" in query:
        raise ValueError("Session export does not filter historical records by current state")
    scope = filters(dict(query, source=source, session=session))
    if entity is not None:
        scope["entity"] = label(entity, "entity")
    if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0):
        raise ValueError("Expected revision must be nonnegative")
    destination = Path(output)
    parent = destination.parent.resolve(strict=True)
    target = parent / destination.name
    # Copy committed WAL data first; validation and export never initialize the selected source
    with snapshot_ledger(database) as preview:
        inspect_ledger(preview.store.path)
        revision, rows = preview.store.read()
        if expected_revision is not None and revision != expected_revision:
            raise ValueError("Ledger changed. Select a new revision before exporting")
        matching = [validate(row) for row in rows if selected(row, scope)]
        if len(matching) > MAX_EXPORT_EVENTS:
            raise ValueError("Selection exceeds the importable event limit; narrow the filters")
        descriptor, temporary = tempfile.mkstemp(prefix=".monitoring-export-", suffix=".tmp", dir=parent)
        digest, byte_count = hashlib.sha256(), 0
        try:
            with os.fdopen(descriptor, "wb") as stream:
                for row in matching:
                    payload = (json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
                    byte_count += len(payload)
                    if byte_count > MAX_BYTES:
                        raise ValueError("Selection exceeds the importable byte limit; narrow the filters")
                    stream.write(payload); digest.update(payload)
                stream.flush(); os.fsync(stream.fileno())
            # New-file publication refuses existing paths, including symlinks
            os.link(temporary, target, follow_symlinks=False)
            try:
                _sync_directory(parent)
            except OSError:
                raise ResultPublicationUncertain from None
        finally:
            Path(temporary).unlink(missing_ok=True)
    return dict(schema="monitoring.session-export.v1", revision=revision, filters=scope,
                events=len(matching), bytes=byte_count, sha256=digest.hexdigest(),
                coverage="selected-accepted-metadata-only", includes_correction_audit=False,
                pricing="original-recorded-cost-only")
