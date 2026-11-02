from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from monitoring_ai.store import Store
from test_multi_sources import event


class ConsistentReads(unittest.TestCase):
    def test_concurrent_commit_cannot_mix_revision_and_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            reader = Store(Path(directory) / "ledger.sqlite")
            writer = Store(reader.path)
            reader.ingest([event("first")])
            connect = reader.connect

            class ConcurrentCommit:
                def __init__(self, db):
                    self.db = db

                def execute(self, statement):
                    cursor = self.db.execute(statement)
                    if statement.startswith("SELECT COALESCE"):
                        writer.ingest([event("second")])
                    return cursor

            @contextmanager
            def interleaved():
                with connect() as db:
                    yield ConcurrentCommit(db)

            with patch.object(reader, "connect", interleaved):
                revision, rows = reader.read()
            self.assertEqual(revision, 1)
            self.assertEqual([row["id"] for row in rows], ["first"])
            self.assertEqual(writer.read()[0], 2)
