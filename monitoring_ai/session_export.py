"""Publish one selected session as importable private normalized JSONL."""

from .events import label, validate
from .event_export import MAX_EXPORT_EVENTS, publish_events
from .ledger_backup import inspect_ledger
from .ledger_preview import snapshot_ledger
from .metrics import filters, selected


def export_session(database, output, *, source, session, entity=None, query=None, expected_revision=None):
    query = dict(query or {})
    if "state" in query:
        raise ValueError("Session export does not filter historical records by current state")
    scope = filters(dict(query, source=source, session=session))
    if entity is not None:
        scope["entity"] = label(entity, "entity")
    if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0):
        raise ValueError("Expected revision must be nonnegative")
    # Copy committed WAL data first; validation and export never initialize the selected source
    with snapshot_ledger(database) as preview:
        inspect_ledger(preview.store.path)
        revision, rows = preview.store.read()
        if expected_revision is not None and revision != expected_revision:
            raise ValueError("Ledger changed. Select a new revision before exporting")
        matching = [validate(row) for row in rows if selected(row, scope)]
        if len(matching) > MAX_EXPORT_EVENTS:
            raise ValueError("Selection exceeds the importable event limit; narrow the filters")
        publication = publish_events(output, matching)
    return dict(schema="monitoring.session-export.v1", revision=revision, filters=scope,
                **publication,
                coverage="selected-accepted-metadata-only", includes_correction_audit=False,
                pricing="original-recorded-cost-only")
