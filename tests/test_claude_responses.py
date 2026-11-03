"""Native response evidence never becomes a synthetic session state."""

import json
from pathlib import Path
import tempfile
import unittest

from monitoring_ai.adapters import claude_row
from monitoring_ai.claude_follow import ClaudeFollower
from monitoring_ai.claude_responses import response_spans
from monitoring_ai.source_observer import SourceObserver
from monitoring_ai.sources import Source
from monitoring_ai.store import Store


def row(kind, at, **fields):
    return dict(type=kind, timestamp=at, sessionId="fictional-session", **fields)


class ClaudeResponseTest(unittest.TestCase):
    def test_temporal_mismatch_and_malformed_optional_rows_stay_unpaired(self):
        projected = [
            None,
            dict(row("user", "2026-11-03T04:00:00Z"), prompt_observed=True, row_id=[]),
            dict(row("assistant", "2026-11-03T03:59:59Z"), response_end_observed=True, message="bad"),
            dict(row("system", "2026-11-03T04:00:01Z"), stop_hook_observed=True),
            dict(row("assistant", "2026-11-03T04:00:02Z"), response_end_observed=True, message={"id": "m1"}),
            dict(row("system", "2026-11-03T04:00:03Z"), stop_hook_observed=True),
            dict(row("assistant", "2026-11-03T04:00:04Z"), response_end_observed=True, message={"id": "m2"}),
        ]
        [span] = response_spans(projected, "chosen", "project")["rows"]
        self.assertEqual(span["response_end_at"], "2026-11-03T04:00:02.000Z")
        self.assertEqual(span["stop_hook_at"], "2026-11-03T04:00:03.000Z")
        self.assertEqual(span["evidence"], "stop_hook_observed")

    def test_new_prompt_does_not_merge_failed_attempt_and_duplicate_response(self):
        native = [
            row("user", "2026-11-03T04:00:00Z", uuid="u1", message={"content": "private prompt"}),
            row("assistant", "2026-11-03T04:00:01Z", message={"id": "m1", "stop_reason": "stop_sequence",
                "content": [{"type": "text", "text": "private error"}]}),
            row("user", "2026-11-03T04:01:00Z", uuid="u2", message={"content": "private retry"}),
            row("assistant", "2026-11-03T04:01:03Z", message={"id": "m2", "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "private result"}]}),
            row("assistant", "2026-11-03T04:01:04Z", message={"id": "m2", "stop_reason": "end_turn"}),
            row("system", "2026-11-03T04:01:05Z", subtype="stop_hook_summary"),
        ]
        projected = [claude_row(item) for item in native]
        self.assertNotIn("private", json.dumps(projected))
        [failed, succeeded] = response_spans(projected, "chosen", "project")["rows"]
        self.assertEqual((failed["evidence"], failed["response_end_at"], failed["stop_hook_at"]),
                         ("prompt_observed", None, None))
        self.assertEqual(succeeded["evidence"], "stop_hook_observed")
        self.assertEqual(succeeded["response_end_at"], "2026-11-03T04:01:03.000Z")
        self.assertEqual(succeeded["stop_hook_at"], "2026-11-03T04:01:05.000Z")
        self.assertNotEqual(failed["session"], "fictional-session")
        self.assertNotIn("m2", json.dumps((failed, succeeded)))

    def test_follower_append_partial_rotation_and_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "selected.jsonl"
            store = Store(Path(folder) / "ledger.sqlite")
            follower = ClaudeFollower(path, "chosen", "project")
            prompt = json.dumps(row("user", "2026-11-03T04:00:00Z", message={"content": "secret"})) + "\n"
            ending = json.dumps(row("assistant", "2026-11-03T04:00:03Z", message={"id": "m1", "role": "assistant",
                "model": "test-model", "stop_reason": "end_turn", "usage": {"input_tokens": 2, "output_tokens": 1}})) + "\n"
            path.write_text(prompt + ending[:25])
            for _ in range(2):
                store.ingest(follower(), claude_corrections=True, accepted_rows=True)
                follower.commit()
                self.assertEqual(follower.responses()["rows"][0]["evidence"], "prompt_observed")
            with path.open("a") as stream:
                stream.write(ending[25:])
            store.ingest(follower(), claude_corrections=True, accepted_rows=True)
            follower.commit()
            self.assertEqual(follower.responses()["rows"][0]["evidence"], "response_end_observed")
            self.assertEqual(store.read()[0], 1)
            replacement = Path(folder) / "replacement"
            replacement.write_text(prompt + ending)
            replacement.replace(path)
            store.ingest(follower(), claude_corrections=True, accepted_rows=True)
            follower.commit()
            self.assertEqual((len(follower.responses()["rows"]), store.read()[0]), (1, 1))

    def test_observer_retains_prior_evidence_on_source_loss(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "selected.jsonl"
            path.write_text(json.dumps(row("user", "2026-11-03T04:00:00Z", message={"content": "secret"})) + "\n")
            source = Source("chosen", "claude", path, "project")
            observer = SourceObserver([source])
            store = Store(Path(folder) / "ledger.sqlite")
            observer.refresh(store)
            self.assertEqual(observer.responses()["rows"][0]["source_state"], "following")
            path.unlink()
            observer.refresh(store)
            book = observer.responses()
            self.assertEqual(book["unavailable_sources"], 1)
            self.assertEqual(book["rows"][0]["source_state"], "unavailable")


if __name__ == "__main__":
    unittest.main()
