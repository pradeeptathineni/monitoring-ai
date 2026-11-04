import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from monitoring_ai.adapters import load
from monitoring_ai.result_capture import ResultPublicationUncertain
from monitoring_ai.session_export import export_session
from monitoring_ai.store import Store


def event(ident, session="chosen", entity="root", parent=None):
    return dict(schema="monitoring.event.v1", id=ident, source="fixture", session=session,
                entity=entity, parent=parent, entity_type="session" if parent is None else "worker",
                timestamp="2026-11-03T12:00:00Z", kind="usage", provider="example", model="model",
                surface="test", project="project", tokens={"input": 2, "output": 3},
                cost=dict(amount="0.01", currency="EUR", basis="billed", provenance="fixture-reported"))


class SessionExportTests(unittest.TestCase):
    def test_exact_session_roundtrip_preserves_identity_cost_hierarchy_and_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = Store(root / "source.sqlite")
            source.ingest([event("one"), event("two", entity="child", parent="root"), event("other", session="other")])
            before = source.read(); output = root / "session.jsonl"
            receipt = export_session(source.path, output, source="fixture", session="chosen", expected_revision=3)
            self.assertEqual(receipt["events"], 2)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertEqual(receipt["sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
            self.assertEqual(source.read(), before)
            destination = Store(root / "new.sqlite"); rows = load(output, "normalized")
            self.assertEqual(destination.ingest(rows)["added"], 2)
            self.assertEqual(destination.ingest(rows)["added"], 0)
            self.assertEqual(rows[0]["cost"]["basis"], "billed")
            self.assertEqual(rows[1]["parent"], "root")
            self.assertNotIn(str(source.path), str(receipt))
            with self.assertRaises(ValueError):
                destination.ingest([{**rows[0], "tokens": {"input": 999}}])

    def test_refusals_leave_existing_files_and_no_partial_publication(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = Store(root / "source.sqlite")
            source.ingest([event("one"), event("two")])
            output = root / "session.jsonl"; output.write_bytes(b"keep")
            with self.assertRaises(FileExistsError):
                export_session(source.path, output, source="fixture", session="chosen")
            self.assertEqual(output.read_bytes(), b"keep"); output.unlink()
            with self.assertRaises(ValueError):
                export_session(source.path, output, source="fixture", session="chosen", expected_revision=1)
            with patch("monitoring_ai.event_export.MAX_EXPORT_EVENTS", 1), self.assertRaises(ValueError):
                export_session(source.path, output, source="fixture", session="chosen")
            with patch("monitoring_ai.event_export.MAX_BYTES", 1), self.assertRaises(ValueError):
                export_session(source.path, output, source="fixture", session="chosen")
            self.assertFalse(output.exists())
            with patch("monitoring_ai.event_export._sync_directory", side_effect=OSError), self.assertRaises(ResultPublicationUncertain):
                export_session(source.path, output, source="fixture", session="chosen")
            self.assertEqual(len(load(output, "normalized")), 2)
            self.assertFalse(list(root.glob(".monitoring-export-*")))
