"""Native stream frames provide one CLI invocation, not session closure."""

from io import BytesIO
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from monitoring_ai.claude_stream import capture_stream
from monitoring_ai.claude_results import claude_results
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

    def test_tool_pairs_keep_only_name_category_and_outcome(self):
        tool_use = {"type": "assistant", "session_id": INIT["session_id"],
                    "message": {"model": "claude-example", "content": [
                        {"type": "tool_use", "id": "private-call", "name": "Bash",
                         "input": {"command": "private command text"}}]}}
        tool_result = {"type": "user", "session_id": INIT["session_id"],
                       "message": {"content": [
                           {"type": "tool_result", "tool_use_id": "private-call",
                            "is_error": False, "content": "private tool output"}]}}
        times = iter(f"2026-11-03T09:00:{second:02d}.000Z" for second in range(10))
        capture_stream(stream(INIT, tool_use, tool_use, tool_result, tool_result, SUCCESS),
                       self.path, source="owned-cli", project="example",
                       clock=lambda: next(times))
        rows = self.rows()
        self.assertEqual([row["kind"] for row in rows], ["status", "tool", "status"])
        self.assertEqual(rows[1]["tool"], "Bash")
        self.assertEqual(rows[1]["tool_category"], "shell")
        self.assertEqual(rows[1]["outcome"], "ok")
        self.assertEqual(rows[1]["entity"], rows[0]["entity"])
        self.assertEqual(rows[1]["parent"], rows[0]["session"])
        self.assertLess(rows[0]["timestamp"], rows[1]["timestamp"])
        self.assertLess(rows[1]["timestamp"], rows[2]["timestamp"])
        self.assertNotIn("private", self.path.read_text())
        store = Store(Path(self.temp.name) / "tool-ledger.sqlite")
        self.assertEqual(store.ingest(rows)["added"], 3)
        self.assertEqual(store.ingest(rows)["added"], 0)

    def test_unanswered_tool_request_remains_unknown(self):
        tool_use = {"type": "assistant", "session_id": INIT["session_id"],
                    "message": {"model": "claude-example", "content": [
                        {"type": "tool_use", "id": "native-call", "name": "mcp__files__lookup",
                         "input": {"path": "/private/example"}}]}}
        times = iter(f"2026-11-03T09:00:{second:02d}.000Z" for second in range(10))
        receipt = capture_stream(stream(INIT, tool_use), self.path,
                                 source="owned-cli", project="example",
                                 clock=lambda: next(times))
        rows = self.rows()
        self.assertEqual(receipt["terminal_state"], "unknown")
        self.assertEqual([row["kind"] for row in rows], ["status", "tool", "status"])
        self.assertEqual(rows[1]["outcome"], "unknown")
        self.assertEqual(rows[1]["tool_category"], "mcp")
        self.assertEqual(rows[1]["tool_server"], "files")
        self.assertNotIn("/private/example", self.path.read_text())

    def test_tool_result_without_explicit_error_flag_is_not_assumed_successful(self):
        use = {"type": "assistant", "session_id": INIT["session_id"],
               "message": {"content": [{"type": "tool_use", "id": "call", "name": "Read"}]}}
        answer = {"type": "user", "session_id": INIT["session_id"],
                  "message": {"content": [{"type": "tool_result", "tool_use_id": "call"}]}}
        times = iter(f"2026-11-03T09:00:{second:02d}.000Z" for second in range(10))
        capture_stream(stream(INIT, use, answer, SUCCESS), self.path,
                       source="owned-cli", project="example", clock=lambda: next(times))
        self.assertEqual(self.rows()[1]["outcome"], "unknown")

    def test_opt_in_result_frame_links_to_invocation_without_copying_content(self):
        result_file = Path(self.temp.name) / "result.jsonl"
        result = {**SUCCESS, "modelUsage": {"claude-example": {
            "inputTokens": 7, "cacheReadInputTokens": 2,
            "cacheCreationInputTokens": 1, "outputTokens": 3,
            "costUSD": 0.04, "provider": "firstParty"}}}
        receipt = capture_stream(stream(INIT, ASSISTANT, result), self.path,
                                 source="owned-cli", project="example",
                                 clock=lambda: next(self.times), result_output=result_file)
        self.assertEqual(receipt["result_summary"], "captured")
        self.assertEqual(stat.S_IMODE(result_file.stat().st_mode), 0o600)
        self.assertNotIn("private final reply", result_file.read_text())
        frame = json.loads(result_file.read_text())
        self.assertEqual(set(frame["result"]), {"type", "session_id", "modelUsage"})
        usage = claude_results([frame], "owned-cli", "example")
        status = self.rows()
        self.assertEqual(usage[0]["session"], status[0]["session"])
        self.assertEqual(usage[0]["entity"], status[0]["parent"])
        self.assertEqual(usage[0]["tokens"], {"input": 7, "cache_read": 2,
                                               "cache_write": 1, "output": 3})
        self.assertEqual(usage[0]["cost"]["basis"], "estimated")
        store = Store(Path(self.temp.name) / "ledger.sqlite")
        self.assertEqual(store.ingest(status + usage)["added"], 3)

    def test_missing_result_summary_does_not_change_observed_lifecycle(self):
        result_file = Path(self.temp.name) / "result.jsonl"
        receipt = capture_stream(stream(INIT), self.path, source="owned-cli", project="example",
                                 clock=lambda: next(self.times), result_output=result_file)
        self.assertEqual(receipt["result_summary"], "unavailable")
        self.assertFalse(result_file.exists())
        self.assertEqual([row["state"] for row in self.rows()], ["running", "unknown"])

    def test_append_two_invocations_to_one_selected_status_and_result_history(self):
        from monitoring_ai.claude_results import claude_results
        from monitoring_ai.source_setup import check_selection, select_claude_stream
        result_file = Path(self.temp.name) / "results.jsonl"
        manifest = Path(self.temp.name) / "sources.json"
        first = {**SUCCESS, "modelUsage": {"claude-example": {
            "inputTokens": 3, "cacheReadInputTokens": 0,
            "cacheCreationInputTokens": 0, "outputTokens": 2,
            "costUSD": 0.01, "provider": "firstParty"}}}
        next_init = {**INIT, "session_id": "another-native-session", "uuid": "another-invocation"}
        next_result = {**first, "session_id": "another-native-session"}
        times = iter(f"2026-11-03T09:00:{second:02d}.000Z" for second in range(10))
        capture_stream(stream(INIT, first), self.path, source="owned-cli", project="example",
                       clock=lambda: next(times), result_output=result_file)
        capture_stream(stream(next_init, next_result), self.path, source="owned-cli",
                       project="example", clock=lambda: next(times), result_output=result_file,
                       append=True)
        select_claude_stream(manifest, status_file=self.path, result_file=result_file,
                             source_id="owned-cli", project="example")
        self.assertTrue(check_selection(manifest)["all_readable"])
        status = self.rows()
        usage = claude_results([json.loads(line) for line in result_file.read_text().splitlines()],
                               "owned-cli", "example")
        self.assertEqual([row["state"] for row in status],
                         ["running", "completed", "running", "completed"])
        self.assertEqual(len(usage), 2)
        self.assertEqual(len({row["session"] for row in status + usage}), 2)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(result_file.stat().st_mode), 0o600)
        store = Store(Path(self.temp.name) / "append-ledger.sqlite")
        self.assertEqual(store.ingest(status + usage)["added"], 6)
        self.assertEqual(store.ingest(status + usage)["added"], 0)
        before = self.path.read_bytes(), result_file.read_bytes()
        with self.assertRaisesRegex(ValueError, "already captured"):
            capture_stream(stream(next_init, next_result), self.path, source="owned-cli",
                           project="example", result_output=result_file, append=True)
        self.assertEqual((self.path.read_bytes(), result_file.read_bytes()), before)

    def test_append_refuses_non_private_result_before_changing_status(self):
        result_file = Path(self.temp.name) / "results.jsonl"
        result_file.write_text("{}\n")
        self.path.write_text("")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "owned private"):
            capture_stream(stream(INIT, SUCCESS), self.path, source="owned-cli",
                           project="example", result_output=result_file, append=True)
        self.assertEqual(self.path.read_bytes(), before)
        result_file.chmod(0o600)
        result_file.write_text("{}")
        with self.assertRaisesRegex(ValueError, "incomplete final record"):
            capture_stream(stream(INIT, SUCCESS), self.path, source="owned-cli",
                           project="example", result_output=result_file, append=True)
        self.assertEqual(self.path.read_bytes(), before)

    def test_append_refuses_incomplete_status_file(self):
        self.path.write_text('{"schema":')
        self.path.chmod(0o600)
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "open an existing private event file"):
            capture_stream(stream(INIT, SUCCESS), self.path, source="owned-cli",
                           project="example", append=True)
        self.assertEqual(self.path.read_bytes(), before)

    def test_append_creates_missing_result_after_incomplete_first_invocation(self):
        result_file = Path(self.temp.name) / "later-result.jsonl"
        first_times = iter(f"2026-11-03T09:00:{second:02d}.000Z" for second in range(10))
        capture_stream(stream(INIT), self.path, source="owned-cli", project="example",
                       clock=lambda: next(first_times), result_output=result_file)
        self.assertFalse(result_file.exists())
        next_init = {**INIT, "session_id": "second-native", "uuid": "second-invocation"}
        next_result = {**SUCCESS, "session_id": "second-native", "modelUsage": {
            "claude-example": {"inputTokens": 1, "cacheReadInputTokens": 0,
                               "cacheCreationInputTokens": 0, "outputTokens": 1}}}
        second_times = iter(f"2026-11-03T09:01:{second:02d}.000Z" for second in range(10))
        receipt = capture_stream(stream(next_init, next_result), self.path,
                                 source="owned-cli", project="example",
                                 clock=lambda: next(second_times), result_output=result_file,
                                 append=True)
        self.assertEqual(receipt["result_summary"], "captured")
        self.assertEqual([row["state"] for row in self.rows()],
                         ["running", "unknown", "running", "completed"])
        self.assertEqual(stat.S_IMODE(result_file.stat().st_mode), 0o600)

    def test_result_target_is_checked_before_consuming_the_invocation(self):
        result_file = Path(self.temp.name) / "result.jsonl"
        result_file.write_text("keep")
        with self.assertRaisesRegex(ValueError, "separate new file"):
            capture_stream(stream(INIT, SUCCESS), self.path, source="owned-cli",
                           project="example", result_output=result_file)
        self.assertEqual(result_file.read_text(), "keep")
        self.assertFalse(self.path.exists())
        variant = self.path.with_name(self.path.name.upper())
        with self.assertRaisesRegex(ValueError, "separate new file"):
            capture_stream(stream(INIT, SUCCESS), self.path, source="owned-cli",
                           project="example", result_output=variant)
        self.assertFalse(self.path.exists())
        with self.assertRaisesRegex(ValueError, "separate new file"):
            capture_stream(stream(INIT, SUCCESS), self.path, source="owned-cli",
                           project="example", result_output=self.path)
        self.assertFalse(self.path.exists())

    def test_failed_result_publication_keeps_lifecycle_and_forwards_remainder(self):
        result_file = Path(self.temp.name) / "result.jsonl"
        result = {**SUCCESS, "modelUsage": {"claude-example": {
            "inputTokens": 1, "cacheReadInputTokens": 0,
            "cacheCreationInputTokens": 0, "outputTokens": 1}}}
        native = stream(INIT, result, {"type": "system", "subtype": "post-result"})
        original = native.getvalue()
        forwarded = BytesIO()
        with patch("monitoring_ai.claude_stream.write_result_frame", side_effect=FileExistsError):
            receipt = capture_stream(native, self.path, source="owned-cli", project="example",
                                     clock=lambda: next(self.times), forward=forwarded,
                                     result_output=result_file)
        self.assertEqual(receipt["terminal_state"], "completed")
        self.assertEqual(receipt["result_summary"], "failed")
        self.assertEqual(forwarded.getvalue(), original)
        self.assertEqual([row["state"] for row in self.rows()], ["running", "completed"])
        self.assertFalse(result_file.exists())

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
