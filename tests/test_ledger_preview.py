import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import time
import unittest
from unittest.mock import patch

from monitoring_ai import ledger_preview
from monitoring_ai.ledger_preview import snapshot_ledger
from monitoring_ai.store import Store

NOW = "2026-11-02T20:00:00Z"


def event(ident, *, source="synthetic", session=None, entity=None, parent=None, tokens=1):
    session = session or f"session-{ident}"
    entity = entity or session
    return {"schema": "monitoring.event.v1", "id": ident, "source": source,
            "session": session, "entity": entity, "parent": parent, "entity_type": "session",
            "timestamp": NOW, "kind": "usage", "provider": "test", "model": "test-model",
            "surface": "test", "project": "test-app", "tokens": {"input": tokens}}


def database_digest(path):
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT sequence, source, event_id, payload FROM events ORDER BY sequence").fetchall()
    encoded = json.dumps(rows, separators=(",", ":")).encode()
    return len(rows), hashlib.sha256(encoded).hexdigest()


class LedgerPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_none_creates_empty_private_preview_and_cleans_it(self):
        temporary_path = None
        with snapshot_ledger() as preview:
            temporary_path = Path(preview.store.path)
            self.assertTrue(temporary_path.exists())
            self.assertEqual(preview.revision, 0)
            self.assertEqual(preview.events, 0)
            self.assertEqual(preview.store.read(), (0, []))
        self.assertFalse(temporary_path.exists())

    def test_committed_wal_is_visible_while_writer_connection_remains_open(self):
        path = self.root / "events.sqlite"
        Store(path)
        writer = sqlite3.connect(path)
        try:
            writer.execute("PRAGMA journal_mode=WAL")
            row = event("committed")
            payload = json.dumps(row, sort_keys=True, separators=(",", ":"))
            writer.execute("INSERT INTO events(source,event_id,timestamp,payload) VALUES(?,?,?,?)",
                           (row["source"], row["id"], row["timestamp"], payload))
            writer.commit()
            self.assertTrue(Path(str(path) + "-wal").exists())
            with snapshot_ledger(path) as preview:
                self.assertEqual(preview.events, 1)
                self.assertEqual(preview.revision, 1)
                self.assertEqual(preview.store.read()[1][0]["id"], "committed")
        finally:
            writer.close()

    def test_uncommitted_writer_transaction_is_not_in_preview(self):
        path = self.root / "events.sqlite"
        Store(path)
        writer = sqlite3.connect(path)
        try:
            writer.execute("PRAGMA journal_mode=WAL")
            row = event("uncommitted")
            payload = json.dumps(row, sort_keys=True, separators=(",", ":"))
            writer.execute("INSERT INTO events(source,event_id,timestamp,payload) VALUES(?,?,?,?)",
                           (row["source"], row["id"], row["timestamp"], payload))
            with snapshot_ledger(path) as preview:
                self.assertEqual(preview.events, 0)
                self.assertEqual(preview.revision, 0)
                self.assertEqual(preview.store.read()[1], [])
        finally:
            writer.rollback()
            writer.close()
        self.assertEqual(Store(path).read()[1], [])

    def test_preview_ingest_changes_only_copy_and_cleans_after_exception(self):
        path = self.root / "events.sqlite"
        original = Store(path)
        original.ingest([event("original")])
        before = database_digest(path)
        original_bytes = path.read_bytes()
        temporary_path = None
        with self.assertRaisesRegex(RuntimeError, "leave preview"):
            with snapshot_ledger(path) as preview:
                temporary_path = Path(preview.store.path)
                self.assertEqual(stat.S_IMODE(temporary_path.parent.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(temporary_path.stat().st_mode), 0o600)
                result = preview.store.ingest([event("preview-only")])
                self.assertEqual(result["added"], 1)
                self.assertEqual(preview.events, 1)
                raise RuntimeError("leave preview")
        self.assertIsNotNone(temporary_path)
        self.assertFalse(temporary_path.exists())
        self.assertEqual(database_digest(path), before)
        self.assertEqual(path.read_bytes(), original_bytes)
        self.assertEqual(original.read()[1][0]["id"], "original")

    def test_exclusive_delete_journal_lock_times_out_quickly_without_path_details(self):
        path = self.root / "private-exclusive-ledger.sqlite"
        Store(path)
        writer = sqlite3.connect(path, isolation_level=None)
        try:
            mode = writer.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
            self.assertEqual(mode.lower(), "delete")
            writer.execute("BEGIN EXCLUSIVE")
            with patch.object(ledger_preview, "COPY_TIMEOUT_SECONDS", 0.05), \
                 patch.object(ledger_preview, "SOURCE_BUSY_TIMEOUT_SECONDS", 0.01), \
                 patch.object(ledger_preview, "RETRY_SLEEP_SECONDS", 0.005):
                started = time.monotonic()
                with self.assertRaises(ValueError) as caught:
                    with snapshot_ledger(path):
                        self.fail("exclusive database lock was ignored")
                elapsed = time.monotonic() - started
            self.assertEqual(str(caught.exception), ledger_preview.TIMEOUT_ERROR)
            self.assertNotIn(path.name, str(caught.exception))
            self.assertLess(elapsed, 1.0)
        finally:
            writer.rollback()
            writer.close()

    def test_rejects_missing_wrong_schema_and_symlink_paths_without_echoing_path(self):
        secret = self.root / "very-private-ledger-name.sqlite"
        cases = [secret]
        wrong = self.root / "not-a-ledger.sqlite"
        with sqlite3.connect(wrong) as connection:
            connection.execute("CREATE TABLE unrelated (value TEXT)")
        cases.append(wrong)
        wrong_columns = self.root / "wrong-columns.sqlite"
        with sqlite3.connect(wrong_columns) as connection:
            connection.execute("CREATE TABLE events (id TEXT)")
        cases.append(wrong_columns)
        valid = self.root / "valid.sqlite"
        Store(valid)
        alias = self.root / "private-alias.sqlite"
        try:
            alias.symlink_to(valid)
            cases.append(alias)
        except (OSError, NotImplementedError):
            pass
        cases.append(self.root)
        for path in cases:
            with self.subTest(path_type=path.name), self.assertRaises(ValueError) as caught:
                with snapshot_ledger(path):
                    self.fail("invalid preview base was accepted")
            self.assertNotIn(str(path), str(caught.exception))
            self.assertNotIn(path.name, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
