"""Project explicitly selected Codex app turn metadata without messages or outputs."""

import hashlib
import json
import os
import re
import stat

from .app_threads import _object
from .events import label, timestamp
from .metrics import now_iso, seconds
from .result_capture import publish_private_json, replace_private_json

SCHEMA = "monitoring.codex-app-turns.v1"
SOURCE = "codex-app-read-thread"
SCOPE = "selected-task-turn-pages"
STATUSES = {"inProgress", "completed", "failed", "interrupted", "unknown"}


def _alias(prefix, value):
    return prefix + hashlib.sha256(value.encode()).hexdigest()[:24]


def _tasks(rows, captured_at, *, encoded):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 4:
        raise ValueError("Select one to four Codex tasks")
    result, task_ids = [], set()
    identity = "alias" if encoded else "id"
    for row in rows:
        if not isinstance(row, dict) or set(row) != {identity, "host", "updated_at", "observed_at", "has_more", "turns"}:
            raise ValueError("Select only supported Codex task metadata")
        native = label(row[identity], "task identity")
        alias = native if encoded else _alias("task-", "codex:" + native)
        if alias in task_ids or not isinstance(row["host"], str) or row["host"] not in {"local", "connected", "unknown"} or type(row["has_more"]) is not bool:
            raise ValueError("Unsupported or repeated selected task")
        if encoded and not re.fullmatch(r"task-[0-9a-f]{24}", alias):
            raise ValueError("Unsupported encoded task identity")
        if not isinstance(row["turns"], list) or len(row["turns"]) > 4:
            raise ValueError("Select at most four turns per task")
        task_ids.add(alias)
        observed = timestamp(row["observed_at"])
        if seconds(observed) > seconds(captured_at) + 5:
            raise ValueError("Task observation is after capture")
        turns, turn_ids = [], set()
        for turn in row["turns"]:
            if not isinstance(turn, dict) or set(turn) != {identity, "status", "started_at", "completed_at", "duration_ms", "error_present"}:
                raise ValueError("Select only supported Codex turn metadata")
            native_turn = label(turn[identity], "turn identity")
            turn_alias = native_turn if encoded else _alias("turn-", "codex-app:" + native + ":" + native_turn)
            if turn_alias in turn_ids or not isinstance(turn["status"], str) or type(turn["error_present"]) is not bool:
                raise ValueError("Unsupported or repeated turn")
            if encoded and not re.fullmatch(r"turn-[0-9a-f]{24}", turn_alias):
                raise ValueError("Unsupported encoded turn identity")
            turn_ids.add(turn_alias)
            state = turn["status"] if turn["status"] in STATUSES else "unknown"
            if encoded and turn["status"] not in STATUSES:
                raise ValueError("Unsupported encoded turn status")
            start = timestamp(turn["started_at"]) if turn["started_at"] is not None else None
            end = timestamp(turn["completed_at"]) if turn["completed_at"] is not None else None
            duration = turn["duration_ms"]
            if duration is not None and (type(duration) is not int or not 0 <= duration <= 2**53 - 1):
                raise ValueError("Duration must be a nonnegative integer number of milliseconds or null")
            if start and end and start > end:
                raise ValueError("Turn end precedes start")
            turns.append(dict(alias=turn_alias, status=state, started_at=start, completed_at=end,
                              duration_ms=duration, error_present=turn["error_present"]))
        result.append(dict(alias=alias, host=row["host"], updated_at=timestamp(row["updated_at"]),
                           observed_at=observed, has_more=row["has_more"], turns=turns))
    return sorted(result, key=lambda row: row["alias"])


def capture_app_turns(stream, output, captured_at, *, replace=False):
    """Publish sanitized selected-task observations to one private export."""
    captured = timestamp(captured_at)
    payload = stream.read(64_001)
    if len(payload) > 64_000:
        raise ValueError("Selected turn metadata exceeds limit")
    tasks = _tasks(json.loads(payload, object_pairs_hook=_object), captured, encoded=False)
    value = dict(schema=SCHEMA, source=SOURCE, scope=SCOPE, complete_coverage=False,
                 captured_at=captured, tasks=tasks)
    if replace:
        previous = read_app_turns(output)
        if [row["alias"] for row in previous["tasks"]] != [row["alias"] for row in tasks]:
            raise ValueError("Different selected tasks need a new export")
        if previous["captured_at"] > captured or (previous["captured_at"] == captured and previous["tasks"] != tasks):
            raise ValueError("Older or conflicting capture cannot replace selected turns")
        replace_private_json(output, value)
    else:
        publish_private_json(output, value)
    return dict(schema=SCHEMA, captured_at=captured, tasks=len(tasks), turns=sum(len(row["turns"]) for row in tasks))


def read_app_turns(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 64_000:
            raise ValueError("Selected turn export must be a bounded regular file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = json.loads(stream.read(64_001), object_pairs_hook=_object)
    finally:
        os.close(fd)
    if not isinstance(raw, dict) or set(raw) != {"schema", "source", "scope", "complete_coverage", "captured_at", "tasks"} or \
        raw["schema"] != SCHEMA or raw["source"] != SOURCE or raw["scope"] != SCOPE or raw["complete_coverage"] is not False:
        raise ValueError("Unsupported Codex app turn export")
    captured = timestamp(raw["captured_at"])
    tasks = _tasks(raw["tasks"], captured, encoded=True)
    return dict(raw, captured_at=captured, tasks=tasks, read_at=now_iso())
