"""Bounded, metadata-only response evidence from an explicitly selected Claude file.

These are native row observations, not normalized lifecycle events. In particular,
an ended model response or a Stop hook record does not close a CLI session.
"""

from .events import timestamp
from .store import stable_id


def response_spans(rows, source, project, limit=100):
    spans = []
    current_by_session = {}
    seen_prompts = set()
    seen_responses = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        kind = row.get("type")
        session = row.get("sessionId")
        if not isinstance(session, str) or not session or len(session) > 1000:
            continue
        try:
            at = timestamp(row.get("timestamp"))
        except ValueError:
            continue  # Do not turn malformed optional evidence into a usage failure.
        session_alias = "s-" + stable_id(session)
        row_id = row.get("row_id") if isinstance(row.get("row_id"), str) and row.get("row_id") else None
        if kind == "user" and row.get("prompt_observed"):
            identity = row_id or (session_alias, at, "prompt")
            if identity in seen_prompts:
                continue
            seen_prompts.add(identity)
            current = dict(source=source, project=project, session=session_alias,
                           prompt_at=at, response_end_at=None, stop_hook_at=None,
                           evidence="prompt_observed")
            current_by_session[session_alias] = current
            spans.append(current)
        elif kind == "assistant" and row.get("response_end_observed") and session_alias in current_by_session:
            current = current_by_session[session_alias]
            message = row.get("message") if isinstance(row.get("message"), dict) else {}
            identity = message.get("id") if isinstance(message.get("id"), str) else row_id or index
            if (session_alias, identity) in seen_responses:
                continue
            if current["response_end_at"] is None and current["stop_hook_at"] is None and at >= current["prompt_at"]:
                current["response_end_at"] = at
                current["evidence"] = "response_end_observed"
                seen_responses.add((session_alias, identity))
        elif kind == "system" and row.get("stop_hook_observed") and session_alias in current_by_session:
            current = current_by_session[session_alias]
            if (current["stop_hook_at"] is None and current["response_end_at"] is not None
                    and at >= current["response_end_at"]):
                current["stop_hook_at"] = at
                current["evidence"] = "stop_hook_observed"
    return dict(schema="monitoring.claude-responses.v1", source=source, project=project,
                rows=spans[-limit:], truncated=len(spans) > limit)
