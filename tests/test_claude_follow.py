"""A selected native file can append, rotate and recover without recounting."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.request import urlopen

from monitoring_ai.claude_follow import ClaudeFollower
from monitoring_ai.source_observer import SourceObserver
from monitoring_ai.sources import Source
from monitoring_ai.store import Store


def assistant(identity, at, tokens):
    return json.dumps({"type": "assistant", "timestamp": at, "sessionId": "fictional-session",
                       "message": {"role": "assistant", "id": identity, "model": "fictional-model",
                                   "content": [{"type": "text", "text": "private text must be dropped"}],
                                   "usage": {"input_tokens": tokens, "output_tokens": 1}}}) + "\n"


class ClaudeFollowTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.path = self.root / "selected.jsonl"
        self.store = Store(self.root / "events.sqlite")
        self.reader = ClaudeFollower(self.path, "chosen", "fictional-project")

    def tearDown(self):
        self.directory.cleanup()

    def accept(self):
        rows = self.reader()
        result = self.store.ingest(rows, claude_timestamp_replay=True)
        self.reader.commit()
        return result

    def test_complete_lines_append_and_partial_line_waits_without_full_reparse(self):
        first = assistant("one", "2026-11-02T20:00:00Z", 10)
        second = assistant("two", "2026-11-02T20:00:01Z", 20)
        self.path.write_text(first + second[:30])
        self.assertEqual(self.accept(), {"added": 1, "replayed": 0})
        self.assertEqual(self.reader._offset, len(first.encode()))
        with patch("monitoring_ai.claude_follow.parse_jsonl") as parse:
            self.assertEqual(self.accept(), {"added": 0, "replayed": 1})
            parse.assert_not_called()
        with self.path.open("a") as stream:
            stream.write(second[30:])
        self.assertEqual(self.accept(), {"added": 1, "replayed": 1})
        revision, rows = self.store.read()
        self.assertEqual((revision, len(rows)), (2, 2))
        self.assertNotIn("private text", json.dumps(rows))

    def test_rotation_and_truncation_keep_history_and_timestamp_only_replay(self):
        original = assistant("one", "2026-11-02T20:00:00Z", 10)
        self.path.write_text(original)
        self.assertEqual(self.accept()["added"], 1)
        [before] = self.store.read()[1]

        replacement = self.root / "replacement.jsonl"
        replacement.write_text(assistant("one", "2026-11-02T20:00:03Z", 10)
                               + assistant("two", "2026-11-02T20:00:04Z", 20))
        replacement.replace(self.path)
        self.assertEqual(self.accept(), {"added": 1, "replayed": 1})
        self.assertEqual(self.store.read()[0], 2)
        self.assertEqual(next(row for row in self.store.read()[1] if row["id"] == before["id"])["timestamp"],
                         before["timestamp"])

        # Same inode may be truncated and rewritten; an accepted event remains.
        self.path.write_text(assistant("two", "2026-11-02T20:00:09Z", 20))
        self.assertEqual(self.accept(), {"added": 0, "replayed": 1})
        self.assertEqual(self.store.read()[0], 2)

        self.path.write_text(assistant("two", "2026-11-02T20:00:10Z", 21))
        with self.assertRaises(ValueError):
            self.accept()
        self.reader.discard()
        self.assertEqual(self.store.read()[0], 2)
        # A normal immutable import gets no timestamp exception.
        with self.assertRaises(ValueError):
            self.store.ingest([dict(before, timestamp="2026-11-02T20:00:11Z")])

    def test_changed_usage_rejects_atomically_and_cursor_retries_after_repair(self):
        first = assistant("one", "2026-11-02T20:00:00Z", 10)
        self.path.write_text(first)
        self.accept()
        offset = self.reader._offset
        with self.path.open("a") as stream:
            stream.write(assistant("two", "2026-11-02T20:00:01Z", 20))
            stream.write(assistant("one", "2026-11-02T20:00:02Z", 11))
        with self.assertRaises(ValueError):
            self.accept()
        self.reader.discard()
        self.assertEqual(self.reader._offset, offset)
        self.assertEqual(self.store.read()[0], 1)
        self.path.write_text(first + assistant("two", "2026-11-02T20:00:01Z", 20))
        self.assertEqual(self.accept(), {"added": 1, "replayed": 1})

    def test_tool_pair_across_appends_stays_metadata_only(self):
        use = {"type": "assistant", "sessionId": "fictional-session", "timestamp": "2026-11-02T20:00:00Z",
               "message": {"role": "assistant", "model": "fictional-model",
                           "content": [{"type": "tool_use", "id": "call-a", "name": "Read",
                                        "input": {"file_path": "/private/content"}}]}}
        result = {"type": "user", "sessionId": "fictional-session", "timestamp": "2026-11-02T20:00:01Z",
                  "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-a",
                                                           "content": "secret result"}]}}
        self.path.write_text(json.dumps(use) + "\n")
        self.assertEqual(self.accept()["added"], 0)
        with self.path.open("a") as stream:
            stream.write(json.dumps(result) + "\n")
        self.assertEqual(self.accept()["added"], 1)
        self.assertNotIn("/private/content", json.dumps(self.store.read()[1]))
        self.assertNotIn("secret result", json.dumps(self.store.read()[1]))

    def test_rotated_replay_coverage_uses_accepted_event_time(self):
        original = "2026-11-02T20:00:00Z"
        self.path.write_text(assistant("one", original, 10))
        observer = SourceObserver([Source("chosen", "claude", self.path, "fictional-project")])
        self.assertEqual(observer.refresh(self.store)["inputs"][0]["added"], 1)
        later = self.root / "later.jsonl"
        later.write_text(assistant("one", "2026-11-02T20:10:00Z", 10))
        later.replace(self.path)
        status = observer.refresh(self.store)["inputs"][0]
        self.assertEqual((status["state"], status["added"], status["replayed"]),
                         ("following", 0, 1))
        self.assertEqual(observer.coverage()["inputs"][0]["latest_usage_at"], "2026-11-02T20:00:00.000Z")

    def test_loopback_rotation_conflict_and_recovery(self):
        original = assistant("one", "2026-11-02T20:00:00Z", 10)
        second = assistant("two", "2026-11-02T20:00:02Z", 20)
        self.path.write_text(original)
        process = subprocess.Popen([
            sys.executable, "-m", "monitoring_ai", "serve", "--file", str(self.path),
            "--format", "claude", "--source-id", "chosen", "--project", "fictional-project",
            "--db", str(self.root / "loopback.sqlite"), "--port", "0",
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            origin = process.stdout.readline().strip().split("→ ")[1]

            def read():
                with urlopen(origin + "/api/v1/snapshot", timeout=3) as response:
                    return json.load(response)

            def until(predicate):
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    value = read()
                    if predicate(value):
                        return value
                    time.sleep(.1)
                self.fail("Claude source state did not reach the expected value")

            first = until(lambda value: value["revision"] == 1)
            replacement = self.root / "next.jsonl"
            replacement.write_text(assistant("one", "2026-11-02T20:00:09Z", 10) + second)
            replacement.replace(self.path)
            moved = until(lambda value: value["revision"] == 2)
            self.assertEqual((moved["summary"]["calls"], moved["source_status"]["state"]), (2, "following"))
            self.assertNotIn(str(self.root), json.dumps(moved))
            with self.path.open("a") as stream:
                stream.write(assistant("one", "2026-11-02T20:00:10Z", 11))
            failed = until(lambda value: value["source_status"]["state"] == "unavailable")
            self.assertEqual((failed["revision"], failed["summary"]["calls"]), (2, 2))
            self.path.write_text(original + second)
            recovered = until(lambda value: value["source_status"]["state"] == "following")
            self.assertEqual((recovered["revision"], recovered["summary"]["calls"]), (2, 2))
            self.assertEqual(first["summary"]["calls"], 1)
        finally:
            process.terminate()
            process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
