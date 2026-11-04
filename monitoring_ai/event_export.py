"""Publish a bounded private normalized event file without replacing a target."""

import hashlib
import json
import os
from pathlib import Path
import tempfile

from .adapters import MAX_BYTES
from .events import validate
from .ledger_backup import _sync_directory
from .result_capture import ResultPublicationUncertain

MAX_EXPORT_EVENTS = 50_000


def publish_events(output, events):
    """Validate before new-file publication and report uncertain durability."""
    destination = Path(output)
    parent = destination.parent.resolve(strict=True)
    target = parent / destination.name
    descriptor, temporary = tempfile.mkstemp(prefix=".monitoring-export-", suffix=".tmp", dir=parent)
    digest, byte_count, count = hashlib.sha256(), 0, 0
    try:
        with os.fdopen(descriptor, "wb") as stream:
            for row in events:
                payload = (json.dumps(validate(row), sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
                count += 1
                byte_count += len(payload)
                if count > MAX_EXPORT_EVENTS or byte_count > MAX_BYTES:
                    raise ValueError("Selection exceeds the importable export limit")
                stream.write(payload)
                digest.update(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, target, follow_symlinks=False)
        try:
            _sync_directory(parent)
        except OSError:
            raise ResultPublicationUncertain from None
    finally:
        Path(temporary).unlink(missing_ok=True)
    return dict(events=count, bytes=byte_count, sha256=digest.hexdigest())
