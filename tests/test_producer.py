import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from monitoring_ai.adapters import read_jsonl
from monitoring_ai.events import validate
from monitoring_ai.metrics import Exchange, Pricing, snapshot
from monitoring_ai.producer import EventWriteError, EventWriter, session_alias, status_event
from monitoring_ai.store import Store, stable_id

NOW = "2026-11-02T12:00:00Z"


def status(ident, *, entity="session-a", parent=None, entity_type="session", at=NOW, state="running"):
    return status_event(id=ident, source="runtime", project="demo", session="session-a",
                        entity=entity, entity_type=entity_type, parent=parent,
                        state=state, timestamp=at)


class ProducerTests(unittest.TestCase):
    def test_native_session_alias_is_hashed_and_matches_claude_result_identity(self):
        self.assertEqual(session_alias("native-session"), "s-" + stable_id("native-session"))
        opaque_ids = ("Session-X", "session-x", " session-x")
        aliases = [session_alias(value) for value in opaque_ids]
        self.assertEqual(aliases, ["s-" + stable_id(value) for value in opaque_ids])
        self.assertEqual(len(set(aliases)), len(opaque_ids))
        for value in ("", None, "/" * 1001):
            with self.subTest(value=value), self.assertRaises(ValueError):
                session_alias(value)

    def test_status_hierarchy_is_written_as_validated_events_and_ingested(self):
        session = session_alias("native-session-123")
        rows = [
            status_event(id="session-start", source="runtime", project="demo", session=session,
                         entity=session, entity_type="session", state="running", timestamp=NOW),
            status_event(id="agent-start", source="runtime", project="demo", session=session,
                         entity="agent-a", parent=session, entity_type="agent", state="running", timestamp=NOW),
            status_event(id="worker-start", source="runtime", project="demo", session=session,
                         entity="worker-a", parent="agent-a", entity_type="worker", state="running", timestamp=NOW),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations.jsonl"
            with EventWriter(path) as writer:
                for row in rows:
                    self.assertEqual(writer.write(row), row)
            written = [json.loads(line) for line in path.read_text().splitlines()]
            ledger = Store(Path(directory) / "events.sqlite3")
            self.assertEqual(ledger.ingest(written), {"added": 3, "replayed": 0})
            self.assertEqual(len(ledger.read()[1]), 3)

    def test_event_writer_creates_private_regular_new_file_and_refuses_existing_or_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "new.jsonl"
            with EventWriter(path):
                info = path.stat()
                self.assertTrue(stat.S_ISREG(info.st_mode))
                self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
            with self.assertRaisesRegex(ValueError, "new private event file"):
                EventWriter(path)
            with self.assertRaises(ValueError) as existing_error:
                EventWriter(root)
            self.assertNotIn(str(root), str(existing_error.exception))
            target = root / "target.jsonl"
            target.write_text("")
            alias = root / "alias.jsonl"
            alias.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "new private event file"):
                EventWriter(alias)
            self.assertTrue(alias.is_symlink())
            with self.assertRaisesRegex(ValueError, "new private event file"):
                EventWriter(root / "missing-parent" / "events.jsonl")
            self.assertFalse((root / "missing-parent").exists())

    def test_validation_happens_before_any_bytes_are_written_and_closed_writer_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            writer = EventWriter(path)
            invalid = dict(status("invalid"), state="not-a-real-state", secret="discard-me")
            with self.assertRaises(ValueError):
                writer.write(invalid)
            self.assertEqual(path.read_bytes(), b"")
            writer.close()
            with self.assertRaisesRegex(ValueError, "closed"):
                writer.write(status("after-close"))

    def test_immutable_ids_replay_and_changed_batch_conflicts_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            writer = EventWriter(path)
            original = status("same-id")
            writer.write(original)
            writer.write(original)
            ledger = Store(Path(directory) / "events.sqlite3")
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(ledger.ingest(rows), {"added": 1, "replayed": 1})

            new_event = status("would-be-added")
            changed = status("same-id", state="failed")
            writer.write(new_event)
            writer.write(changed)
            batch = [json.loads(line) for line in path.read_text().splitlines()][2:]
            with self.assertRaisesRegex(ValueError, "different content"):
                ledger.ingest(batch)
            self.assertEqual(len(ledger.read()[1]), 1)
            writer.close()

    def test_rereading_old_observation_does_not_refresh_its_timestamp(self):
        observed_at = NOW
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            with EventWriter(path) as writer:
                writer.write(status("old", at=observed_at))
            one = json.loads(path.read_text())
            two = json.loads(path.read_text())
            self.assertEqual(one["timestamp"], observed_at.replace("Z", ".000Z"))
            self.assertEqual(two["timestamp"], one["timestamp"])
            ledger = Store(Path(directory) / "events.sqlite3")
            ledger.ingest([one])
            revision, rows = ledger.read()
            view = snapshot(revision, rows, pricing=Pricing(), fx=Exchange(), now="2026-11-02T12:01:01Z")
            self.assertEqual(view["entities"][0]["state"], "unknown")
            self.assertEqual(view["insights"]["source_freshness"][0]["state"], "stale")

    def test_size_and_record_caps_reject_before_append_without_corrupting_file(self):
        cases = (("bytes", 10_000, None), ("records", None, 1))
        for name, byte_cap, record_cap in cases:
            with self.subTest(limit=name), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "events.jsonl"
                first = status("event-001")
                second = status("event-002")
                byte_limit = byte_cap
                if byte_limit is None:
                    byte_limit = 100_000
                if name == "bytes":
                    # The first line fits exactly; the next complete line does not.
                    line_length = len((json.dumps(validate(first), sort_keys=True, separators=(",", ":")) + "\n").encode())
                    byte_limit = line_length
                with patch("monitoring_ai.producer.MAX_BYTES", byte_limit), \
                        patch("monitoring_ai.producer.MAX_RECORDS", record_cap or 50_000):
                    writer = EventWriter(path)
                    writer.write(first)
                    original = path.read_bytes()
                    with self.assertRaisesRegex(ValueError, "file limit"):
                        writer.write(second)
                    self.assertEqual(path.read_bytes(), original)
                    parsed = [json.loads(line) for line in path.read_text().splitlines()]
                    self.assertEqual(parsed, [first])
                    self.assertEqual(Store(Path(directory) / "events.sqlite3").ingest(parsed),
                                     {"added": 1, "replayed": 0})
                    writer.close()

    def test_concurrent_writes_remain_complete_json_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            writer = EventWriter(path)
            barrier = threading.Barrier(8)

            def write(index):
                barrier.wait()
                writer.write(status(f"concurrent-{index}"))

            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(write, range(8)))
            writer.close()
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(len(rows), 8)
            self.assertEqual({row["id"] for row in rows}, {f"concurrent-{i}" for i in range(8)})

    def test_short_os_writes_are_completed_before_acknowledgement(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            original_write = os.write

            def short_write(fd, data):
                return original_write(fd, data[:3])

            with EventWriter(path) as writer, patch("monitoring_ai.producer.os.write", side_effect=short_write):
                writer.write(status("short-write"))
            self.assertEqual(json.loads(path.read_text())["id"], "short-write")

    def test_partial_write_failure_keeps_prefix_and_live_reader_ignores_partial_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            writer = EventWriter(path)
            first = status("accepted-prefix")
            writer.write(first)
            prefix = path.read_bytes()
            original_write = os.write
            calls = 0

            def partial_then_fail(fd, data):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return original_write(fd, data[:5])
                raise OSError("private low-level detail")

            with patch("monitoring_ai.producer.os.write", side_effect=partial_then_fail):
                with self.assertRaises(EventWriteError) as caught:
                    writer.write(status("partial-tail"))
            self.assertEqual(caught.exception.outcome, "unknown")
            self.assertEqual(str(caught.exception), "Observation write outcome is unknown. Event writer is closed.")
            self.assertIsNone(writer._fd)
            self.assertTrue(path.read_bytes().startswith(prefix))
            self.assertGreater(len(path.read_bytes()), len(prefix))
            self.assertEqual([row["id"] for row in read_jsonl(path, live=True)], ["accepted-prefix"])
            writer.close()

    def test_complete_append_with_fsync_failure_can_be_replayed_without_double_count(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            writer = EventWriter(path)
            first, second = status("prefix"), status("complete-unacknowledged")
            writer.write(first)
            with patch("monitoring_ai.producer.os.fsync", side_effect=OSError("private fsync detail")):
                with self.assertRaises(EventWriteError) as caught:
                    writer.write(second)
            self.assertEqual(caught.exception.outcome, "unknown")
            self.assertEqual(str(caught.exception), "Observation write outcome is unknown. Event writer is closed.")
            rows = read_jsonl(path, live=True)
            self.assertEqual([row["id"] for row in rows], ["prefix", "complete-unacknowledged"])
            ledger = Store(Path(directory) / "events.sqlite3")
            self.assertEqual(ledger.ingest(rows), {"added": 2, "replayed": 0})
            self.assertEqual(ledger.ingest(rows), {"added": 0, "replayed": 2})


if __name__ == "__main__":
    unittest.main()
