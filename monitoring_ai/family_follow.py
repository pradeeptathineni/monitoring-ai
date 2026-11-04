"""Follow one fixed selected Codex family for a bounded, explicit CLI interval."""

import json
from pathlib import Path
import time

from .codex_replay_export import _signature, reconcile_sources
from .events import validate
from .metrics import now_iso
from .producer import EventWriter, MAX_BYTES, MAX_RECORDS
from .sources import load_sources


def follow_family(manifest, output, *, seconds=30, interval=2, append=False,
                  clock=time.monotonic, sleep=time.sleep):
    """Yield path-free checkpoints and never retry an uncertain output write."""
    from .family_cursor import FamilyCursor

    if type(seconds) is not int or not 1 <= seconds <= 3600:
        raise ValueError("Family follow duration must be 1 through 3600 seconds")
    if type(interval) not in (int, float) or not 1 <= interval <= 60:
        raise ValueError("Family follow interval must be 1 through 60 seconds")
    sources = load_sources(manifest)
    if any(source.format != "codex" for source in sources):
        raise ValueError("Family following requires a fixed Codex-only selection")
    writer = EventWriter(output, append=True) if append else None
    try:
        cursor = FamilyCursor(writer.existing if writer else ())
        written_bytes = Path(output).stat().st_size if writer else 0
    except BaseException:
        if writer:
            writer.close()
        raise
    deadline = clock() + seconds
    signature, last_state = None, "unread"
    polls = failures = added_total = parsed = 0
    last_event_at = last_success_at = None
    try:
        while True:
            polls += 1
            new_events, records, current = [], None, None
            try:
                current = tuple(_signature(source.path) for source in sources)
                if current != signature:
                    parsed += 1
                    records, _ = reconcile_sources(sources)
                    if current != tuple(_signature(source.path) for source in sources):
                        raise ValueError("Selected family changed during collection")
                    new_events = cursor.stage(records)
                    signature = current
                elif last_state == "unavailable":
                    raise ValueError("Selected source snapshot remains unavailable")
            except (OSError, ValueError):
                cursor.discard()
                if current is not None:
                    signature = current
                last_state = "unavailable"
                failures += 1
            else:
                if records is not None:
                    # Refuse capacity before a batch can write its first event
                    payload_size = sum(len((json.dumps(validate(row), sort_keys=True, separators=(",", ":")) + "\n").encode())
                                       for row in new_events)
                    if cursor.count + len(new_events) > MAX_RECORDS or written_bytes + payload_size > MAX_BYTES:
                        raise ValueError("Family output reached the bounded event-file limit")
                    if writer is None:
                        writer = EventWriter(output)
                    for event in new_events:
                        writer.write(event)
                    cursor.commit()
                    written_bytes += payload_size
                    added_total += len(new_events)
                    last_event_at = max((row["timestamp"] for row in records), default=None)
                    last_success_at = now_iso()
                last_state = "following"
            yield dict(schema="monitoring.codex-family-follow.v1", state=last_state,
                       observed_at=now_iso(), last_success_at=last_success_at,
                       latest_event_at=last_event_at, added=len(new_events) if last_state == "following" else 0,
                       accepted_records=cursor.count, complete_coverage=False,
                       error="Selected family could not be reconciled; accepted output is retained" if last_state == "unavailable" else None)
            remaining = deadline - clock()
            if remaining <= 0:
                break
            sleep(min(interval, remaining))
        yield dict(schema="monitoring.codex-family-follow.v1", state="stopped",
                   last_source_state=last_state, observed_at=now_iso(), added_total=added_total,
                   accepted_records=cursor.count, polls=polls, full_reads=parsed,
                   failed_polls=failures, complete_coverage=False, finite=True)
    finally:
        if writer:
            writer.close()
