"""One selected cache member, stale rejection, and private-field exclusion."""

import json
import os
from pathlib import Path
import tempfile
import unittest

from monitoring_ai.codeburn_sessions import inspect_selected_session
from monitoring_ai.sources import Source
from monitoring_ai.metrics import Exchange, Pricing
from monitoring_ai.server import Observatory
from monitoring_ai.store import Store


class SelectedCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cache = self.root / "cache"
        self.cache.mkdir()
        self.native = self.root / "chosen.jsonl"
        self.native.write_text("{}\n")
        self.source = Source("chosen", "claude", self.native, "work")
        self.secret = "private-conversation-and-command"
        self.write_cache()

    def tearDown(self):
        self.temp.cleanup()

    def write_cache(self, *, fingerprint=None, member=None, piece="claude.day.json"):
        info = self.native.stat()
        record = member or {"turns": [{"timestamp": "2026-11-03T01:02:03Z", "sessionId": self.secret,
            "userMessage": self.secret, "calls": [{"provider": "claude", "model": "claude-test",
                "tools": [self.secret], "bashCommands": [self.secret], "usage": {
                    "inputTokens": 3, "outputTokens": 5, "cacheReadInputTokens": 7,
                    "cacheCreationInputTokens": 11, "reasoningTokens": 0}}]}]}
        key = str(self.native)
        body = json.dumps(key) + ":" + json.dumps(record, separators=(",", ":"))
        (self.cache / piece).write_text("{" + body + "}")
        row = [key, "day", "month", None, info.st_dev, info.st_ino,
               info.st_mtime_ns / 1_000_000, info.st_size, None, None, 0, 1, len(body)]
        if fingerprint is not None:
            row[7] = fingerprint
        (self.cache / "index.claude.json").write_text(json.dumps({"version": 10,
            "pieces": {"day": piece}, "rows": [row, ["/unrelated/private.jsonl", "other", "month", None,
                1, 2, 3, 4, None, None, 0, 0, 0]]}))
        (self.cache / "envelope.json").write_text(json.dumps({"version": 10, "complete": True,
            "nonce": "one", "providers": {"claude": {"complete": True, "index": "index.claude.json"}}}))

    def test_only_selected_member_projects_allowlisted_metadata(self):
        book = inspect_selected_session(self.cache, self.source)
        self.assertEqual((book["total_turns"], book["total_calls"]), (1, 1))
        self.assertEqual(book["turns"][0]["tokens"], {
            "input": 3, "output": 5, "cache_read": 7, "cache_write": 11, "reasoning": 0})
        self.assertNotIn(self.secret, json.dumps(book))
        self.assertNotIn(str(self.native), json.dumps(book))
        self.assertNotIn("/unrelated", json.dumps(book))

    def test_stale_native_or_cache_member_is_rejected(self):
        self.write_cache(fingerprint=999)
        with self.assertRaisesRegex(ValueError, "Selected session cache"):
            inspect_selected_session(self.cache, self.source)
        self.write_cache()
        self.native.write_text("{}\n{}\n")
        with self.assertRaises(ValueError):
            inspect_selected_session(self.cache, self.source)

    def test_unrelated_member_and_symlink_are_never_opened(self):
        self.write_cache(piece="../../elsewhere.json")
        with self.assertRaises(ValueError):
            inspect_selected_session(self.cache, self.source)
        self.write_cache()
        link = self.root / "alias.jsonl"
        link.symlink_to(self.native)
        with self.assertRaises(ValueError):
            inspect_selected_session(self.cache, Source("alias", "claude", link, "work"))

    def test_malformed_selected_usage_refuses_without_emitting_content(self):
        self.write_cache(member={"turns": [{"timestamp": "2026-11-03T01:02:03Z",
            "userMessage": self.secret, "calls": [{"model": "claude-test", "usage": {"inputTokens": True}}]}]})
        with self.assertRaisesRegex(ValueError, "Selected session cache") as raised:
            inspect_selected_session(self.cache, self.source)
        self.assertNotIn(self.secret, str(raised.exception))

    def test_optional_api_view_retains_last_good_cache_without_ledger_changes(self):
        store = Store(self.root / "ledger.sqlite")
        owner = Observatory(store, Pricing(None), Exchange("USD", None),
                            session_cache_loader=lambda: inspect_selected_session(self.cache, self.source))
        owner.refresh_session_cache()
        first = owner.read({})
        self.assertEqual(first["selected_cache_status"]["state"], "following")
        self.assertEqual(first["selected_cache_session"]["total_calls"], 1)
        revision = first["revision"]
        self.native.write_text("{}\n{}\n")
        owner.refresh_session_cache()
        retained = owner.read({})
        self.assertEqual(retained["selected_cache_status"]["state"], "unavailable")
        self.assertTrue(retained["selected_cache_session"]["retained"])
        self.assertEqual(retained["revision"], revision)


if __name__ == "__main__":
    unittest.main()
