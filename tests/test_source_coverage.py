import json
from pathlib import Path
import tempfile
import unittest

from monitoring_ai.source_coverage import source_summary
from monitoring_ai.source_observer import SourceObserver
from monitoring_ai.store import Store

NOW = "2026-11-02T20:00:00Z"


class Source:
    def __init__(self, source_id, format_name, rows=None):
        self.id = source_id
        self.format = format_name
        self.path = Path("/private/path/never-return.jsonl")
        self.project = "project-alias" if format_name != "normalized" else None
        self.rows = list(rows or [])
        self.error = None
        self.reads = 0

    def load(self, *, live=True):
        self.reads += 1
        if self.error:
            raise self.error
        return list(self.rows)


def usage(source="producer-a", ident="u1", session="s1", entity="e1", at=NOW):
    return {"schema": "monitoring.event.v1", "id": ident, "source": source,
            "session": session, "entity": entity, "parent": None, "entity_type": "session",
            "timestamp": at, "kind": "usage", "provider": "test", "model": "model",
            "surface": "test", "project": "project-alias", "tokens": {"input": 8}}


def status(source="producer-a", ident="s1-status", session="s1", entity="e1", at="2026-11-02T19:59:00Z"):
    row = usage(source, ident, session, entity, at)
    del row["tokens"]
    row.update(kind="status", state="completed")
    return row


def tool(source="producer-b", ident="t1", session="s2", entity="e2", at="2026-11-02T19:58:00Z"):
    row = usage(source, ident, session, entity, at)
    del row["tokens"]
    row.update(kind="tool", tool="shell-task", outcome="ok")
    return row


class SourceCoverageTests(unittest.TestCase):
    def test_native_usage_only_summary_reports_capability_boundary(self):
        source = Source("native-alias", "claude")
        row = usage(source="native-alias", ident="native-u")
        summary = source_summary(source, [row], sampled_at=NOW)
        self.assertEqual(summary["capabilities"], {
            "usage": "native-reconstruction", "lifecycle": "unsupported", "tools": "completed-pairs"})
        self.assertEqual(summary["records"], {
            "total": 1, "usage": 1, "status": 0, "tool": 0,
            "sessions": 1, "entities": 1, "event_sources": 1})
        self.assertEqual(summary["latest_usage_at"], "2026-11-02T20:00:00.000Z")
        self.assertIsNone(summary["latest_status_at"])
        self.assertNotIn("cost", json.dumps(summary))
        self.assertNotIn("never-return", json.dumps(summary))
        self.assertNotIn("input", summary)

    def test_normalized_summary_counts_kinds_and_distinct_identities(self):
        source = Source("file-alias", "normalized")
        rows = [usage("producer-a", "u", "s1", "e1"),
                status("producer-a", "s", "s1", "e1"),
                tool("producer-b", "t", "s2", "e2")]
        summary = source_summary(source, rows, sampled_at=NOW)
        self.assertEqual(summary["capabilities"], {
            "usage": "normalized-deltas", "lifecycle": "producer-supplied", "tools": "producer-supplied"})
        self.assertEqual(summary["records"], {
            "total": 3, "usage": 1, "status": 1, "tool": 1,
            "sessions": 2, "entities": 2, "event_sources": 2})
        self.assertEqual(summary["latest_event_at"], "2026-11-02T20:00:00.000Z")
        self.assertEqual(summary["latest_status_at"], "2026-11-02T19:59:00.000Z")
        self.assertEqual(summary["latest_tool_at"], "2026-11-02T19:58:00.000Z")

    def test_identical_replays_count_once_and_conflicting_identity_is_rejected(self):
        source = Source("file-alias", "normalized")
        row = usage("producer-a", "same-id")
        summary = source_summary(source, [row, dict(row)], sampled_at=NOW)
        self.assertEqual(summary["records"]["total"], 1)
        self.assertEqual(summary["records"]["usage"], 1)

        conflict = dict(row, project="different-alias")
        with self.assertRaisesRegex(ValueError, "^Conflicting event identity in source coverage$") as caught:
            source_summary(source, [row, conflict], sampled_at=NOW)
        self.assertNotIn("different-alias", str(caught.exception))

        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "ledger.sqlite")
            input_source = Source("file-alias", "normalized", [row, dict(row)])
            observer = SourceObserver([input_source])
            observer.refresh(store)
            self.assertEqual(observer.snapshot()["inputs"][0]["records_seen"], 2)
            self.assertEqual(observer.coverage(now=NOW)["inputs"][0]["records"]["total"], 1)

    def test_unread_and_successfully_empty_sources_are_distinct_and_inputs_v1_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "ledger.sqlite")
            empty = Source("empty", "codex", [])
            observer = SourceObserver([empty])
            before = observer.snapshot()
            self.assertEqual(set(before), {"schema", "configured", "inputs", "healthy_inputs", "unavailable_inputs", "unread_inputs"})
            self.assertTrue(all(set(row) == {"id", "format", "project", "state", "last_attempt_at", "last_success_at",
                                                "records_seen", "added", "replayed", "error"}
                                for row in before["inputs"]))
            observer.refresh(store)
            coverage = observer.coverage(now=NOW)
            unread_observer = SourceObserver([Source("waiting", "claude", [])])
            unread_coverage = unread_observer.coverage(now=NOW)
        by_id = {"empty": coverage["inputs"][0], "waiting": unread_coverage["inputs"][0]}
        self.assertEqual(observer.snapshot()["schema"], "monitoring.inputs.v1")
        self.assertEqual(coverage["schema"], "monitoring.source-coverage.v1")
        self.assertEqual(coverage["scope"], "latest-accepted-input-reads")
        self.assertFalse(coverage["complete_coverage"])
        self.assertEqual(by_id["empty"]["event_freshness"], "empty")
        self.assertEqual(by_id["empty"]["records"]["total"], 0)
        self.assertEqual(by_id["waiting"]["event_freshness"], "unread")
        self.assertIsNone(by_id["waiting"]["records"])
        self.assertIsNone(by_id["waiting"]["sampled_at"])
        self.assertEqual(by_id["waiting"]["capabilities"]["usage"], "native-reconstruction")

    def test_age_advances_without_read_and_failure_retains_last_accepted_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "ledger.sqlite")
            source = Source("source", "normalized", [usage(at="2026-11-02T19:59:00Z")])
            observer = SourceObserver([source])
            observer.refresh(store)
            first = observer.coverage(now=NOW)["inputs"][0]
            first["records"]["usage"] = 999
            first["capabilities"]["usage"] = "mutated"
            later = observer.coverage(now="2026-11-02T20:02:00Z")["inputs"][0]
            self.assertEqual(first["event_age_seconds"], 60)
            self.assertEqual(first["event_freshness"], "recent")
            self.assertEqual(later["event_age_seconds"], 180)
            self.assertEqual(later["event_freshness"], "stale")
            self.assertEqual(later["records"]["usage"], 1)
            self.assertEqual(later["capabilities"]["usage"], "normalized-deltas")
            self.assertEqual(later["sampled_at"], first["sampled_at"])
            self.assertEqual(source.reads, 1)

            source.error = OSError("/private/path/never-return.jsonl disappeared")
            observer.refresh(store)
            retained = observer.coverage(now="2026-11-02T20:03:00Z")["inputs"][0]
            self.assertEqual(retained["read_state"], "unavailable")
            self.assertTrue(retained["retained"])
            self.assertEqual(retained["event_freshness"], "stale")
            self.assertEqual(retained["latest_event_at"], first["latest_event_at"])
            self.assertNotIn("never-return", json.dumps(retained))

    def test_future_record_is_not_misclassified_as_recent(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "ledger.sqlite")
            source = Source("future", "codex", [usage(source="future", at="2026-11-02T20:00:00.500Z")])
            observer = SourceObserver([source])
            observer.refresh(store)
            row = observer.coverage(now=NOW)["inputs"][0]
        self.assertLess(row["event_age_seconds"], 0)
        self.assertEqual(row["event_freshness"], "future")


if __name__ == "__main__":
    unittest.main()
