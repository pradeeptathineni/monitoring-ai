"""Refresh explicitly configured input sources independently of each other."""

import threading

from .claude_follow import ClaudeFollower
from .codex_follow import CodexFollower
from .claude_lineage import ClaudeForkHistoryError, MESSAGE as CLAUDE_FORK_MESSAGE
from .codex_lineage import (CodexLineageError, MESSAGE as CODEX_LINEAGE_MESSAGE,
                            UnsupportedHistoryError, UNQUALIFIED_MESSAGE)
from .metrics import now_iso
from .source_coverage import source_coverage, source_summary
from .sources import Source as SelectedSource
from .source_overlap import NativeOverlapError, NATIVE_FORMATS, MESSAGE


class SourceObserver:
    """Own immutable source configuration and thread-safe, path-free read status."""

    def __init__(self, sources):
        configured = tuple((source, source.id, source.format, source.project) for source in sources)
        identifiers = [source_id for _, source_id, _, _ in configured]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Input source IDs must be unique")
        self._sources = configured
        self._native_families = {
            source_id: format_name for source, source_id, format_name, _ in configured
            if isinstance(source, SelectedSource) and format_name in NATIVE_FORMATS
        }
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
            source_id: (ClaudeFollower if format_name == "claude" else CodexFollower)(source.path, source_id, project)
            for source, source_id, format_name, project in configured
            # Programmatic sources supply their own load method; only validated
            # manifest selections use the native incremental file cursor.
            if isinstance(source, SelectedSource) and format_name in ("claude", "codex")
        }

    @staticmethod
    def _error_message(error):
        # Expose only stable generic classes of failure, never exception text.
        if isinstance(error, OSError):
            return "Input unavailable."
        if isinstance(error, NativeOverlapError):
            return MESSAGE
        if isinstance(error, ClaudeForkHistoryError):
            return CLAUDE_FORK_MESSAGE
        if isinstance(error, UnsupportedHistoryError):
            return UNQUALIFIED_MESSAGE
        if isinstance(error, CodexLineageError):
            return CODEX_LINEAGE_MESSAGE
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
                if not follower:
                    summary = source_summary(source, rows, sampled_at=now_iso())
                result = store.ingest(rows, claude_corrections=getattr(follower, "correct_usage", False),
                                      accepted_rows=bool(follower),
                                      overlap_sources=[alias for alias, family in self._native_families.items()
                                                       if alias != source_id and family == self._native_families.get(source_id)])
                if follower:
                    # Versions of one message resolve to one current ledger row.
                    # Coverage uses its original event time and corrected tokens.
                    summary = source_summary(source, result.pop("rows"), sampled_at=now_iso())
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

    def responses(self):
        """Separate native response markers from ledger entity lifecycle."""
        with self._lock:
            books = [(source_id, follower.responses(), self._status[source_id]["state"])
                     for source_id, follower in self._followers.items() if follower.supports_responses]
        rows = [dict(row, source_state=state) for _, book, state in books for row in book["rows"]]
        rows.sort(key=lambda row: (row["prompt_at"], row["source"], row["session"]))
        return dict(schema="monitoring.claude-responses.v1", rows=rows[-100:],
                    truncated=len(rows) > 100 or any(book["truncated"] for _, book, _ in books),
                    configured_sources=len(books),
                    unavailable_sources=sum(state == "unavailable" for _, _, state in books))

    def turns(self):
        """Keep Codex task markers separate from ledger session lifecycle."""
        with self._lock:
            books = [(follower.turns(), self._status[source_id]["state"])
                     for source_id, follower in self._followers.items() if getattr(follower, "supports_turns", False)]
        rows = [dict(row, source_state=state) for book, state in books for row in book["rows"]]
        rows.sort(key=lambda row: (row["start_at"] or row["complete_at"], row["source"], row["turn"]))
        return dict(schema="monitoring.codex-turns.v1", rows=rows[-100:],
                    truncated=len(rows) > 100 or any(book["truncated"] for book, _ in books),
                    ambiguous_turns=sum(book["ambiguous_turns"] for book, _ in books),
                    configured_sources=len(books),
                    unavailable_sources=sum(state == "unavailable" for _, state in books))
