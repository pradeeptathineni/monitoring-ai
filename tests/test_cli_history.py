"""The terminal reads scoped, revision-pinned entity pages without a server."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from monitoring_ai.store import Store


def event(ident, second, *, source="source-a", session="session-a", entity="target", kind="usage"):
    row = {"schema": "monitoring.event.v1", "id": ident, "source": source,
           "session": session, "entity": entity, "parent": None, "entity_type": "session",
           "timestamp": f"2026-11-03T12:00:{second:02}Z", "kind": kind,
           "provider": "example", "model": "test-model", "surface": "terminal", "project": "test"}
    if kind == "usage":
        row.update(tokens={"input": 3}, cost={"amount": "0.01", "currency": "EUR",
                "basis": "billed", "provenance": "fixture-declared"})
    elif kind == "status":
        row["state"] = "completed"
    else:
        row.update(tool="file-change", outcome="unknown")
    return row


class CliHistoryTests(unittest.TestCase):
    def test_exact_entity_pages_keep_revision_cost_and_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "ledger.sqlite"
            store = Store(database)
            store.ingest([event("first", 1), event("second", 2, kind="status"),
                          event("third", 3, kind="tool"),
                          event("other-session", 4, session="session-b"),
                          event("other-source", 5, source="source-b"),
                          event("other-entity", 6, entity="different")])
            base = [sys.executable, "-m", "monitoring_ai", "history", "--db", str(database),
                    "--source", "source-a", "--session", "session-a", "--entity", "target"]
            before = hashlib.sha256(database.read_bytes()).hexdigest()

            first = subprocess.run(base + ["--limit", "2", "--json"], capture_output=True, text=True, check=True)
            page = json.loads(first.stdout)
            self.assertEqual(page["schema"], "monitoring.history-cli.v1")
            self.assertEqual(page["total"], 3)
            self.assertEqual([row["id"] for row in page["events"]], ["first", "second"])
            self.assertEqual(page["next_offset"], 2)
            self.assertNotIn(str(database), first.stdout)
            self.assertEqual(page["events"][0]["cost"]["basis"], "billed")

            later = subprocess.run(base + ["--after", "2", "--expected-revision", str(page["revision"]), "--json"],
                                   capture_output=True, text=True, check=True)
            self.assertEqual([row["id"] for row in json.loads(later.stdout)["events"]], ["third"])
            self.assertIsNone(json.loads(later.stdout)["next_offset"])
            human = subprocess.run(base, capture_output=True, text=True, check=True)
            self.assertIn("0.01 EUR · billed · fixture-declared", human.stdout)
            self.assertIn("state completed", human.stdout)
            self.assertIn("unclassified · file-change · outcome unknown", human.stdout)
            self.assertIn("not proof of current activity", human.stdout)
            self.assertEqual(hashlib.sha256(database.read_bytes()).hexdigest(), before)

            store.ingest([event("new", 7)])
            after_ingest = hashlib.sha256(database.read_bytes()).hexdigest()
            changed = subprocess.run(base + ["--after", "2", "--expected-revision", str(page["revision"])],
                                     capture_output=True, text=True)
            self.assertEqual(changed.returncode, 2)
            self.assertNotIn(str(database), changed.stderr)
            no_pin = subprocess.run(base + ["--after", "2"], capture_output=True, text=True)
            self.assertEqual(no_pin.returncode, 2)
            self.assertEqual(hashlib.sha256(database.read_bytes()).hexdigest(), after_ingest)

            absent = Path(directory) / "missing.sqlite"
            missing = subprocess.run([sys.executable, "-m", "monitoring_ai", "history", "--db", str(absent),
                                      "--source", "source-a", "--session", "session-a", "--entity", "target"],
                                     capture_output=True, text=True)
            self.assertEqual(missing.returncode, 2)
            self.assertFalse(absent.exists())


if __name__ == "__main__":
    unittest.main()
