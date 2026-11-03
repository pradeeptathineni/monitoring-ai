"""Refresh explicitly configured input sources independently of each other."""

import threading

from .claude_follow import ClaudeFollower
from .metrics import now_iso
from .source_coverage import source_coverage, source_summary


class SourceObserver:
    """Own immutable source configuration and thread-safe, path-free read status."""

    def __init__(self, sources):
        configured = tuple((source, source.id, source.format, source.project) for source in sources)
        identifiers = [source_id for _, source_id, _, _ in configured]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Input source IDs must be unique")
        self._sources = configured
        self._lock = threading.Lock()
        self._status = {
            source_id: {
                "id": source_id,
                "format": format_name,
                "project": project,
                "state": "unread",
                "last_attempt_at": None,
                "last_success_at": None,
                "records_seen": None,
                "added": 0,
                "replayed": 0,
                "error": None,
            }
            for _, source_id, format_name, project in configured
        }
        self._coverage_summaries = {source_id: None for _, source_id, _, _ in configured}
        self._followers = {
            source_id: ClaudeFollower(source.path, source_id, project)
            for source, source_id, format_name, project in configured if format_name == "claude"
        }

    @staticmethod
    def _error_message(error):
        # Expose only stable generic classes of failure, never exception text.
        if isinstance(error, OSError):
            return "Input unavailable."
        if isinstance(error, ValueError):
            return "Input data was rejected."
        return "Input processing failed."

    def refresh(self, store):
        """Read and atomically ingest every source; one failure cannot stop others."""
        for source, source_id, _, _ in self._sources:
            attempted = now_iso()
            with self._lock:
                self._status[source_id]["last_attempt_at"] = attempted

            records_seen = None
            added = replayed = 0
            follower = self._followers.get(source_id)
            try:
                rows = list(follower() if follower else source.load(live=True))
                records_seen = len(rows)
                summary = source_summary(source, rows, sampled_at=now_iso())
                result = store.ingest(rows, claude_timestamp_replay=bool(follower),
                                      accepted_rows=bool(follower))
                if follower:
                    # A rotated file may contain only a later row timestamp for
                    # a previously accepted message. Coverage uses ledger time.
                    summary = source_summary(source, result.pop("rows"), sampled_at=now_iso())
                if follower:
                    follower.commit()
                added, replayed = result["added"], result["replayed"]
            except Exception as exc:
                if follower:
                    follower.discard()
                with self._lock:
                    status = self._status[source_id]
                    status.update(state="unavailable", records_seen=records_seen,
                                  added=0, replayed=0, error=self._error_message(exc))
                continue

            completed = now_iso()
            with self._lock:
                status = self._status[source_id]
                status.update(state="following", last_success_at=completed,
                              records_seen=records_seen, added=added, replayed=replayed,
                              error=None)
                self._coverage_summaries[source_id] = summary
        return self.snapshot()

    def snapshot(self):
        """Return copied status only; no source paths or raw exceptions are exposed."""
        with self._lock:
            inputs = [dict(self._status[source_id]) for _, source_id, _, _ in self._sources]
        return {
            "schema": "monitoring.inputs.v1",
            "configured": True,
            "inputs": inputs,
            "healthy_inputs": sum(row["state"] == "following" for row in inputs),
            "unavailable_inputs": sum(row["state"] == "unavailable" for row in inputs),
            "unread_inputs": sum(row["state"] == "unread" for row in inputs),
        }

    def coverage(self, now=None):
        """Return source-read coverage without reading sources or the event ledger."""
        with self._lock:
            statuses = [dict(self._status[source_id]) for _, source_id, _, _ in self._sources]
            summaries = [self._coverage_summaries[source_id] for _, source_id, _, _ in self._sources]
        sources = [source for source, _, _, _ in self._sources]
        return source_coverage(sources, statuses, summaries, now=now)
