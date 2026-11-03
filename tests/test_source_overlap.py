"""Copied selected native logs must not count twice under new aliases."""

import json
from pathlib import Path
import tempfile
import unittest

from monitoring_ai.source_setup import add_selection, check_selection, create_selection
from monitoring_ai.source_readiness import preview_selection
from monitoring_ai.source_observer import SourceObserver
from monitoring_ai.sources import Source, load_sources
from monitoring_ai.store import Store


def message(identity, session="fictional-session"):
    return {"type": "assistant", "sessionId": session, "timestamp": "2026-11-03T08:00:00Z",
            "message": {"role": "assistant", "id": identity, "model": "fictional-model",
                        "usage": {"input_tokens": 3, "output_tokens": 5}}}


class NativeOverlapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.first, self.second = self.root / "first.jsonl", self.root / "copied.jsonl"
        content = json.dumps(message("m1")) + "\n"
        self.first.write_text(content)
        self.second.write_text(content)
        one, self.manifest = self.root / "one.json", self.root / "two.json"
        create_selection(one, file=self.first, id="first", format="claude", project="work")
        add_selection(one, self.manifest, file=self.second, id="second", format="claude", project="work")

    def test_check_preview_and_live_owner_refuse_copy_then_recover(self):
        selected = load_sources(self.manifest)
        check = check_selection(self.manifest)
        self.assertFalse(check["all_readable"])
        self.assertEqual([row["state"] for row in check["checks"]], ["readable", "invalid"])
        self.assertIn("Overlapping native records", check["checks"][1]["error"])
        self.assertNotIn(str(self.root), json.dumps(check))

        preview = preview_selection(self.manifest)
        self.assertFalse(preview["ready"])
        self.assertEqual([row["state"] for row in preview["inputs"]], ["ready", "rejected"])
        self.assertEqual(preview["inputs"][0]["proposed"]["added"], 1)
        self.assertIn("Overlapping native records", preview["inputs"][1]["error"])

        store = Store(self.root / "ledger.sqlite")
        observer = SourceObserver(selected)
        first = observer.refresh(store)
        self.assertEqual([row["state"] for row in first["inputs"]], ["following", "unavailable"])
        self.assertEqual(first["inputs"][1]["added"], 0)
        self.assertEqual(len(store.read()[1]), 1)

        # A distinct native message in the same session is admitted on recovery.
        self.second.write_text(json.dumps(message("m2")) + "\n")
        recovered = observer.refresh(store)
        self.assertEqual([row["state"] for row in recovered["inputs"]], ["following", "following"])
        self.assertEqual(recovered["inputs"][1]["added"], 1)
        self.assertEqual(len(store.read()[1]), 2)

    def test_same_identity_in_different_native_sessions_is_not_overlap(self):
        self.second.write_text(json.dumps(message("m1", session="another-session")) + "\n")
        self.assertTrue(check_selection(self.manifest)["all_readable"])
        self.assertTrue(preview_selection(self.manifest)["ready"])
        store = Store(self.root / "ledger.sqlite")
        result = SourceObserver(load_sources(self.manifest)).refresh(store)
        self.assertEqual(result["healthy_inputs"], 2)
        self.assertEqual(len(store.read()[1]), 2)

    def test_partial_overlap_rolls_back_earlier_new_records_in_the_same_input(self):
        self.second.write_text(json.dumps(message("m2")) + "\n" + json.dumps(message("m1")) + "\n")
        preview = preview_selection(self.manifest)
        self.assertEqual([row["state"] for row in preview["inputs"]], ["ready", "rejected"])
        store = Store(self.root / "ledger.sqlite")
        outcome = SourceObserver(load_sources(self.manifest)).refresh(store)
        self.assertEqual(outcome["inputs"][1]["added"], 0)
        self.assertEqual(len(store.read()[1]), 1)

    def test_codex_cumulative_identity_refuses_the_same_selected_session_twice(self):
        def record(kind, payload, second):
            return json.dumps({"type": kind, "timestamp": f"2026-11-03T08:00:{second:02d}Z",
                               "payload": payload, "private": "discard-conversation"}) + "\n"
        content = (record("session_meta", {"id": "fictional-codex-session"}, 0) +
                   record("turn_context", {"model": "fictional-model"}, 0) +
                   record("event_msg", {"type": "token_count", "info": {"total_token_usage": {
                       "input_tokens": 12, "cached_input_tokens": 2, "output_tokens": 4}}}, 1))
        self.first.write_text(content)
        self.second.write_text(content)
        selected = [Source("first", "codex", self.first, "work"),
                    Source("second", "codex", self.second, "work")]
        store = Store(self.root / "codex-ledger.sqlite")
        result = SourceObserver(selected).refresh(store)
        self.assertEqual([row["state"] for row in result["inputs"]], ["following", "unavailable"])
        self.assertEqual(len(store.read()[1]), 1)
        self.assertNotIn("discard-conversation", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
