"""Native stream frames provide one CLI invocation, not session closure."""

from io import BytesIO
import json
from pathlib import Path
import stat
import tempfile
import unittest

from monitoring_ai.claude_stream import capture_stream
from monitoring_ai.store import Store


def stream(*frames):
    return BytesIO(b"".join(json.dumps(frame).encode() + b"\n" for frame in frames))


INIT = {"type": "system", "subtype": "init", "session_id": "private-native-id",
        "uuid": "private-invocation-id", "model": "claude-example"}
ASSISTANT = {"type": "assistant", "message": {"content": [{"type": "text",
              "text": "private prompt and reply must never enter events"}], "usage": {"input_tokens": 99}}}
SUCCESS = {"type": "result", "subtype": "success", "is_error": False,
           "session_id": "private-native-id", "result": "private final reply"}


class ClaudeStreamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "events.jsonl"
        self.times = iter(("2026-11-03T09:00:00.000Z", "2026-11-03T09:00:00.000Z"))

    def capture(self, frames):
        return capture_stream(frames, self.path, source="owned-cli", project="example",
                              clock=lambda: next(self.times))

    def rows(self):
        return [json.loads(line) for line in self.path.read_text().splitlines()]

    def test_actual_init_and_result_shape_produce_only_one_invocation_lifecycle(self):
        result = self.capture(stream(INIT, ASSISTANT, ASSISTANT, SUCCESS))
        rows = self.rows()
        self.assertEqual(result, {"observed_invocations": 1, "terminal_state": "completed"})
        self.assertEqual([row["state"] for row in rows], ["running", "completed"])
        self.assertEqual([row["kind"] for row in rows], ["status", "status"])
        self.assertEqual([row["entity_type"] for row in rows], ["agent", "agent"])
        self.assertEqual(rows[0]["parent"], rows[0]["session"])
        self.assertEqual(rows[0]["entity"], rows[1]["entity"])
        self.assertLess(rows[0]["timestamp"], rows[1]["timestamp"])
        self.assertNotIn("private", self.path.read_text())
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        store = Store(Path(self.temp.name) / "ledger.sqlite")
        self.assertEqual(store.ingest(rows)["added"], 2)
        self.assertEqual(store.ingest(rows)["added"], 0)

    def test_missing_result_ends_unknown_and_never_claims_success(self):
        result = self.capture(stream(INIT, ASSISTANT))
        self.assertEqual(result["terminal_state"], "unknown")
        self.assertEqual([row["state"] for row in self.rows()], ["running", "unknown"])

    def test_explicit_forward_keeps_native_stream_for_the_callers_consumer(self):
        native = stream(INIT, ASSISTANT, SUCCESS)
        original = native.getvalue()
        forwarded = BytesIO()
        capture_stream(native, self.path, source="owned-cli", project="example",
                       clock=lambda: next(self.times), forward=forwarded)
        self.assertEqual(forwarded.getvalue(), original)
        self.assertNotIn("private final reply", self.path.read_text())

    def test_explicit_native_error_is_failed_and_ambiguous_result_is_unknown(self):
        self.capture(stream(INIT, {**SUCCESS, "subtype": "error_during_execution", "is_error": True}))
        self.assertEqual(self.rows()[-1]["state"], "failed")
        other = Path(self.temp.name) / "other.jsonl"
        times = iter(("2026-11-03T09:00:00.000Z", "2026-11-03T09:00:01.000Z"))
        capture_stream(stream(INIT, {**SUCCESS, "subtype": "unrecognized"}), other,
                       source="owned-cli", project="example", clock=lambda: next(times))
        self.assertEqual(json.loads(other.read_text().splitlines()[-1])["state"], "unknown")

    def test_mismatched_result_refuses_and_leaves_explicit_unknown(self):
        with self.assertRaisesRegex(ValueError, "matching invocation"):
            self.capture(stream(INIT, {**SUCCESS, "session_id": "different"}))
        self.assertEqual([row["state"] for row in self.rows()], ["running", "unknown"])

    def test_existing_output_and_partial_line_refuse(self):
        self.path.write_text("keep")
        with self.assertRaises(ValueError):
            self.capture(stream(INIT, SUCCESS))
        self.assertEqual(self.path.read_text(), "keep")
        self.path.unlink()
        with self.assertRaisesRegex(ValueError, "complete lines"):
            self.capture(BytesIO(json.dumps(INIT).encode()))
        self.assertEqual(self.path.read_text(), "")


if __name__ == "__main__":
    unittest.main()
