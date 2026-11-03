"""Project selected Codex task markers without asserting session lifecycle."""

import re

from .events import timestamp
from .store import stable_id


def turn_spans(rows, source, project, limit=100):
    session = None
    turns = {}
    ambiguous = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        if row.get("type") == "session_meta":
            identity = payload.get("id")
            if isinstance(identity, str) and 0 < len(identity) <= 1000:
                session = "s-" + stable_id(identity)
            continue
        if row.get("type") != "event_msg" or payload.get("type") not in ("task_started", "task_complete"):
            continue
        turn = payload.get("turn")
        if not session or not isinstance(turn, str) or not re.fullmatch(r"t-[0-9a-f]{32}", turn):
            continue
        try:
            at = timestamp(row.get("timestamp"))
        except ValueError:
            continue
        key = (session, turn)
        span = turns.setdefault(key, dict(source=source, project=project, session=session, turn=turn,
                                          start_at=None, complete_at=None, duration_ms=None,
                                          time_to_first_token_ms=None))
        field = "start_at" if payload["type"] == "task_started" else "complete_at"
        repeated = span[field] is not None
        if span[field] is not None and span[field] != at:
            ambiguous.add(key)
            continue
        span[field] = at
        if field == "complete_at":
            for metric in ("duration_ms", "time_to_first_token_ms"):
                value = payload.get(metric)
                if repeated and span[metric] != value:
                    ambiguous.add(key)
                span[metric] = value if type(value) is int and 0 <= value <= 10**12 else None
    result = []
    for key, span in turns.items():
        if key in ambiguous or (span["start_at"] and span["complete_at"] and
                                span["complete_at"] < span["start_at"]):
            ambiguous.add(key)
            continue
        span["evidence"] = ("paired" if span["start_at"] and span["complete_at"] else
                            "start-only" if span["start_at"] else "completion-only")
        result.append(span)
    result.sort(key=lambda span: (span["start_at"] or span["complete_at"], span["source"], span["turn"]))
    return dict(schema="monitoring.codex-turns.v1", source=source, project=project,
                rows=result[-limit:], truncated=len(result) > limit,
                ambiguous_turns=len(ambiguous))
