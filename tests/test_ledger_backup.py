"""A private backup preserves accepted history while the original remains writable."""

import json
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest

from monitoring_ai.ledger_backup import backup_ledger, inspect_ledger
from monitoring_ai.store import Store


class LedgerBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_committed_wal_and_correction_history_are_copied_without_later_writes(self):
        source, output = self.root / "source.sqlite", self.root / "backup.sqlite"
        Store(source)
        writer = sqlite3.connect(source)
        try:
            row = {"id": "one", "source": "fixture", "timestamp": "2026-11-03T00:00:00Z"}
            writer.execute("INSERT INTO events(source,event_id,timestamp,payload) VALUES(?,?,?,?)",
                           ("fixture", "one", row["timestamp"], json.dumps(row)))
            writer.execute("INSERT INTO native_usage_versions(source,event_id,record_at,tokens) VALUES(?,?,?,?)",
                           ("fixture", "one", row["timestamp"], '{"input":1}'))
            writer.execute("INSERT INTO corrections(source,event_id,source_record_at,observed_at,prior_payload,next_payload,ledger_revision) VALUES(?,?,?,?,?,?,?)",
                           ("fixture", "one", row["timestamp"], row["timestamp"], "{}", "{}", 2))
            writer.commit()
            self.assertTrue(Path(str(source) + "-wal").exists())
            receipt = backup_ledger(source, output)
            self.assertEqual(receipt["events"], 1)
            self.assertEqual(receipt["corrections"], 1)
            self.assertEqual(receipt["native_usage_versions"], 1)
            self.assertEqual(receipt["revision"], 2)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(inspect_ledger(output), receipt)
            with sqlite3.connect(output) as check:
                self.assertEqual(check.execute("PRAGMA journal_mode").fetchone(), ("delete",))
            self.assertFalse(list(self.root.glob(".backup.sqlite.*.tmp*")))
            writer.execute("INSERT INTO events(source,event_id,timestamp,payload) VALUES(?,?,?,?)",
                           ("fixture", "two", row["timestamp"], json.dumps({**row, "id": "two"})))
            writer.commit()
            self.assertEqual(inspect_ledger(source)["events"], 2)
            self.assertEqual(inspect_ledger(output), receipt)
        finally:
            writer.close()

    def test_existing_output_and_invalid_source_leave_no_partial_backup(self):
        source, output = self.root / "source.sqlite", self.root / "existing.sqlite"
        Store(source)
        output.write_bytes(b"keep")
        with self.assertRaises(FileExistsError):
            backup_ledger(source, output)
        self.assertEqual(output.read_bytes(), b"keep")
        alias = self.root / "alias.sqlite"
        alias.symlink_to(output)
        with self.assertRaises(FileExistsError):
            backup_ledger(source, alias)
        self.assertTrue(alias.is_symlink())
        self.assertEqual(output.read_bytes(), b"keep")
        missing_output = self.root / "missing-output.sqlite"
        with self.assertRaises(ValueError):
            backup_ledger(self.root / "missing.sqlite", missing_output)
        self.assertFalse(missing_output.exists())
        self.assertFalse(list(self.root.glob(".*.tmp")))


if __name__ == "__main__":
    unittest.main()
