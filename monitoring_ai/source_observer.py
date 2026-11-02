"""Refresh explicitly configured input sources independently of each other."""

import threading

from .metrics import now_iso


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
            try:
                rows = list(source.load(live=True))
                records_seen = len(rows)
                result = store.ingest(rows)
                added, replayed = result["added"], result["replayed"]
            except Exception as exc:
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
