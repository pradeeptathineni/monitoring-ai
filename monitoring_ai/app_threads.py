"""Read one selected metadata-only app task inventory export."""

import json
import hashlib
import os
import stat

from .events import label, timestamp
from .metrics import now_iso, seconds


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate inventory field")
        result[key] = value
    return result


def capture_app_threads(stream, output, captured_at, *, replace=False):
    from .result_capture import publish_private_json, replace_private_json
    captured_at = timestamp(captured_at)
    payload = stream.read(256_001)
    if len(payload) > 256_000:
        raise ValueError("Inventory metadata exceeds limit")
    raw = json.loads(payload, object_pairs_hook=_object)
    if not isinstance(raw, list) or len(raw) > 100:
        raise ValueError("Supply at most 100 metadata-only task rows")
    rows, seen = [], set()
    for row in raw:
        if not isinstance(row, dict) or set(row) != {"id", "kind", "status", "host", "updated_at"} or \
            not all(isinstance(row[key], str) for key in ("kind", "status", "host")) or \
            row["kind"] not in {"codex", "chatgpt"} or row["host"] not in {"local", "connected", "unknown"}:
            raise ValueError("Select only supported inventory metadata fields")
        ident = label(row["id"], "thread id")
        alias = "task-" + hashlib.sha256((row["kind"] + ":" + ident).encode()).hexdigest()[:24]
        value = dict(alias=alias, kind=row["kind"], host=row["host"],
                     status=row["status"] if row["status"] in {"active", "idle"} else "unknown",
                     updated_at=timestamp(row["updated_at"]))
        if alias in seen:
            raise ValueError("Duplicate task identity")
        seen.add(alias); rows.append(value)
    value = dict(schema="monitoring.app-threads.v1", captured_at=captured_at,
                 source="codex-app-task-inventory", scope="recent-app-page", complete_coverage=False,
                 threads=sorted(rows, key=lambda row: row["alias"]))
    if replace:
        previous = read_app_threads(output)
        if previous["captured_at"] > captured_at:
            raise ValueError("Older capture cannot replace newer observations")
        if previous["captured_at"] == captured_at and previous["threads"] != value["threads"]:
            raise ValueError("Conflicting capture timestamp")
        replace_private_json(output, value)
    else:
        publish_private_json(output, value)
    return dict(schema=value["schema"], captured_at=captured_at, tasks=len(rows))


def read_app_threads(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 256_000:
            raise ValueError("Selected app inventory must be a bounded regular file")
        with os.fdopen(fd, "rb", closefd=False) as source:
            raw = json.loads(source.read(256_001), object_pairs_hook=_object)
    finally:
        os.close(fd)
    if not isinstance(raw, dict) or set(raw) != {"schema", "captured_at", "source", "scope", "complete_coverage", "threads"} or \
        raw["schema"] != "monitoring.app-threads.v1" or raw["source"] != "codex-app-task-inventory" or \
        raw["scope"] != "recent-app-page" or raw["complete_coverage"] is not False or \
        not isinstance(raw["threads"], list) or len(raw["threads"]) > 100:
        raise ValueError("Unsupported app inventory")
    captured = timestamp(raw["captured_at"])
    rows, seen = [], set()
    for row in raw["threads"]:
        if not isinstance(row, dict) or set(row) != {"alias", "kind", "status", "updated_at", "host"} or \
            not all(isinstance(row[key], str) for key in ("kind", "status", "host")) or \
            row["kind"] not in {"codex", "chatgpt"} or row["status"] not in {"active", "idle", "unknown"} or \
            row["host"] not in {"local", "connected", "unknown"}:
            raise ValueError("Unsupported app task row")
        alias = label(row["alias"], "alias")
        if alias in seen:
            raise ValueError("Duplicate app task alias")
        seen.add(alias)
        rows.append(dict(row, alias=alias, updated_at=timestamp(row["updated_at"])))
    observed = now_iso()
    age = seconds(observed) - seconds(captured)
    return dict(raw, captured_at=captured, threads=rows, read_at=observed, age_seconds=age,
                freshness="future" if age < -5 else "recent" if age <= 30 else "stale")
