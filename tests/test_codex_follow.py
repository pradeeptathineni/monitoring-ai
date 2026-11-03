"""A selected Codex file can append without rescanning raw history."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from monitoring_ai.codex_follow import CodexFollower
from monitoring_ai.source_observer import SourceObserver
from monitoring_ai.sources import Source
from monitoring_ai.store import Store


def record(kind, at, payload):
    return json.dumps({"type": kind, "timestamp": at, "payload": payload,
                       "private": "SECRET-TEXT-MUST-DROP"}) + "\n"


META = record("session_meta", "2026-11-03T05:00:00Z", {"id": "fictional-session"})
MODEL = record("turn_context", "2026-11-03T05:00:00Z", {"model": "fictional-model"})


def usage(second, input_count, cached, output):
    return record("event_msg", f"2026-11-03T05:00:{second:02d}Z", {"type": "token_count", "info": {
        "total_token_usage": {"input_tokens": input_count, "cached_input_tokens": cached,
                              "cache_write_input_tokens": 0, "output_tokens": output}}})


FIRST = usage(1, 10, 3, 2)
SECOND = usage(2, 15, 5, 4)
THIRD = usage(3, 19, 6, 5)


class CodexFollowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "selected.jsonl"
        self.store = Store(self.root / "ledger.sqlite")
        self.reader = CodexFollower(self.path, "chosen", "fictional-project")

    def accept(self):
        result = self.store.ingest(self.reader(), accepted_rows=True)
        self.reader.commit()
        return result

    def test_append_partial_idle_and_complete_rotation(self):
        self.path.write_text(META + MODEL + FIRST + SECOND[:30])
        self.assertEqual(self.accept()["added"], 1)
        self.assertNotIn("SECRET-TEXT-MUST-DROP", json.dumps(self.reader._rows))
        with patch("monitoring_ai.claude_follow.parse_jsonl") as parse:
            self.assertEqual(self.accept()["added"], 0)
            parse.assert_not_called()
        with self.path.open("a") as stream:
            stream.write(SECOND[30:])
        self.assertEqual(self.accept()["added"], 1)
        self.assertEqual(self.accept()["added"], 0)
        replacement = self.root / "replacement"
        replacement.write_text(META + MODEL + FIRST + SECOND + THIRD)
        replacement.replace(self.path)
        self.assertEqual(self.accept()["added"], 1)
        self.assertEqual((self.store.read()[0], len(self.store.read()[1])), (3, 3))

    def test_rotated_segment_without_accepted_prefix_refuses_then_recovers(self):
        self.path.write_text(META + MODEL + FIRST + SECOND)
        self.accept()
        old_offset = self.reader._offset
        replacement = self.root / "replacement"
        replacement.write_text(META + MODEL + THIRD)
        replacement.replace(self.path)
        with self.assertRaises(ValueError):
            self.reader()
        self.reader.discard()
        self.assertEqual((self.reader._offset, self.store.read()[0]), (old_offset, 2))
        self.path.write_text(META + MODEL + FIRST + SECOND + THIRD)
        self.assertEqual(self.accept()["added"], 1)

    def test_observer_does_not_expose_claude_response_block_for_codex(self):
        self.path.write_text(META + MODEL + FIRST)
        observer = SourceObserver([Source("chosen", "codex", self.path, "fictional-project")])
        self.assertEqual(observer.refresh(self.store)["healthy_inputs"], 1)
        self.assertEqual(observer.responses()["configured_sources"], 0)
        self.assertEqual(observer.refresh(self.store)["inputs"][0]["added"], 0)
        replacement = self.root / "replacement"
        replacement.write_text(META + MODEL + SECOND)
        replacement.replace(self.path)
        self.assertEqual(observer.refresh(self.store)["inputs"][0]["state"], "unavailable")
        self.assertEqual(self.store.read()[0], 1)
        self.path.write_text(META + MODEL + FIRST + SECOND)
        recovered = observer.refresh(self.store)["inputs"][0]
        self.assertEqual((recovered["state"], recovered["added"], self.store.read()[0]),
                         ("following", 1, 2))


if __name__ == "__main__":
    unittest.main()
