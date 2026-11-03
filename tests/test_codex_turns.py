"""Codex turn markers add chronology without changing session lifecycle."""

import json
from pathlib import Path
import tempfile
import unittest

from monitoring_ai.adapters import codex_row
from monitoring_ai.codex_follow import CodexFollower
from monitoring_ai.codex_turns import turn_spans
from monitoring_ai.metrics import Exchange, Pricing
from monitoring_ai.server import Observatory
from monitoring_ai.source_observer import SourceObserver
from monitoring_ai.sources import Source
from monitoring_ai.store import Store


def meta():
    return {"type": "session_meta", "payload": {"id": "fictional-session", "cwd": "private path"}}


def marker(kind, turn, at, **values):
    return {"type": "event_msg", "timestamp": at,
            "payload": {"type": kind, "turn_id": turn, "content": "private prompt or result", **values}}


START = "2026-11-03T03:00:00Z"
END = "2026-11-03T03:00:04Z"


class CodexTurnTests(unittest.TestCase):
    def test_projected_markers_pair_without_retaining_content_or_raw_turn_id(self):
        native = [meta(), marker("task_started", "secret-turn-1", START),
                  marker("task_complete", "secret-turn-1", END,
                         duration_ms=4100, time_to_first_token_ms=700)]
        projected = [codex_row(row) for row in native]
        self.assertNotIn("private", json.dumps(projected))
        self.assertNotIn("secret-turn-1", json.dumps(projected))
        [span] = turn_spans(projected, "chosen", "project")["rows"]
        self.assertEqual((span["evidence"], span["start_at"], span["complete_at"]),
                         ("paired", "2026-11-03T03:00:00.000Z", "2026-11-03T03:00:04.000Z"))
        self.assertEqual((span["duration_ms"], span["time_to_first_token_ms"]), (4100, 700))
        self.assertNotEqual(span["session"], "fictional-session")

    def test_missing_conflicting_and_reversed_markers_never_infer_running_or_closed_session(self):
        native = [meta(), marker("task_started", "start-only", START),
                  marker("task_complete", "complete-only", END),
                  marker("task_started", "conflict", START),
                  marker("task_started", "conflict", END),
                  marker("task_complete", "reversed", START),
                  marker("task_started", "reversed", END),
                  marker("task_started", "malformed-time", "not-a-time")]
        result = turn_spans([codex_row(row) for row in native], "chosen", "project")
        self.assertEqual(result["ambiguous_turns"], 2)
        self.assertEqual({row["evidence"] for row in result["rows"]},
                         {"start-only", "completion-only"})
        self.assertTrue(all("state" not in row for row in result["rows"]))

    def test_selected_file_replay_and_loss_retain_optional_turn_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "selected.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in
                                    [meta(), marker("task_started", "turn-1", START),
                                     marker("task_complete", "turn-1", END, duration_ms=4100)]))
            store = Store(Path(directory) / "ledger.sqlite")
            observer = SourceObserver([Source("chosen", "codex", path, "project")])
            owner = Observatory(store, Pricing(), Exchange(), source_observer=observer)
            observer.refresh(store)
            first = owner.read({})
            self.assertEqual(first["summary"]["calls"], 0)
            self.assertEqual(first["summary"]["active"], 0)
            self.assertEqual(first["codex_turns"]["rows"][0]["evidence"], "paired")
            observer.refresh(store)
            self.assertEqual(owner.read({})["codex_turns"]["rows"], first["codex_turns"]["rows"])
            path.unlink()
            observer.refresh(store)
            retained = owner.read({})["codex_turns"]
            self.assertEqual(retained["unavailable_sources"], 1)
            self.assertEqual(retained["rows"][0]["source_state"], "unavailable")

    def test_direct_file_owner_uses_the_same_bounded_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "selected.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in
                                    [meta(), marker("task_started", "turn-1", START)]))
            owner = Observatory(Store(Path(directory) / "ledger.sqlite"), Pricing(), Exchange(),
                                loader=CodexFollower(path, "chosen", "project"))
            owner.refresh()
            book = owner.read({})["codex_turns"]
            self.assertEqual(set(book), {"schema", "rows", "truncated", "ambiguous_turns",
                                         "configured_sources", "unavailable_sources"})
            self.assertEqual(book["rows"][0]["evidence"], "start-only")


if __name__ == "__main__":
    unittest.main()
