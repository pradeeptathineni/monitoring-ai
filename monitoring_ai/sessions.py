"""List exact recorded sessions independently of the snapshot entity bound."""

from collections import defaultdict

from .events import label
from .metrics import filters, now_iso, selected


def session_page(revision, events, query, *, limit=100, after=0, search=""):
    if not 1 <= limit <= 100 or after < 0 or "state" in query:
        raise ValueError("Use pages of 1–100 and omit current state")
    query = filters(query)
    search = label(search, "search").casefold() if search else ""
    grouped = defaultdict(list)
    for event in events:
        if selected(event, query) and (not search or search in event["source"].casefold() or search in event["session"].casefold()):
            grouped[(event["source"], event["session"])].append(event)
    rows = []
    for (source, session), records in grouped.items():
        times = [row["timestamp"] for row in records]
        rows.append(dict(source=source, session=session, records=len(records),
                         usage_records=sum(row["kind"] == "usage" for row in records),
                         first_event_at=min(times), last_event_at=max(times)))
    rows.sort(key=lambda row: (row["source"], row["session"]))
    rows.sort(key=lambda row: row["last_event_at"], reverse=True)
    return dict(schema="monitoring.sessions.v1", revision=revision, observed_at=now_iso(),
                filters=query, search=search, scope="matching-accepted-ledger-records",
                complete_coverage=False, sessions=rows[after:after + limit], total=len(rows),
                next_offset=after + limit if after + limit < len(rows) else None)
