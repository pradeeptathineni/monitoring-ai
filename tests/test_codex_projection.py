"""Native Codex records retain only the fields used for cumulative accounting."""

import json
from pathlib import Path
import tempfile
import unittest

from monitoring_ai.adapters import codex, codex_row, load, read_jsonl


SECRET = ("PROMPT-TEXT", "TOOL-ARGUMENT", "/synthetic/private/workdir", "PRIVATE-INSTRUCTIONS")
AT = "2026-11-02T20:00:00Z"
ROWS = [
    {"type": "session_meta", "timestamp": AT, "payload": {"id": "fictional-session",
     "cwd": "/synthetic/private/workdir", "base_instructions": {"text": "PRIVATE-INSTRUCTIONS"}}},
    {"type": "turn_context", "timestamp": AT, "payload": {"model": "fictional-model",
     "cwd": "/synthetic/private/workdir", "summary": "PROMPT-TEXT"}},
    {"type": "response_item", "timestamp": AT, "payload": {"type": "message", "content": "PROMPT-TEXT"}},
    {"type": "token_usage_record", "timestamp": AT, "payload": {"response_id": "fictional-response",
     "usage": {"input_tokens": 999}, "private": "TOOL-ARGUMENT"}},
    {"type": "event_msg", "timestamp": AT, "payload": {"type": "token_count", "info": {
     "total_token_usage": {"input_tokens": 10, "cached_input_tokens": 3,
                           "cache_write_input_tokens": 0, "output_tokens": 2,
                           "reasoning_output_tokens": 1, "total_tokens": 12},
     "last_token_usage": {"private": "TOOL-ARGUMENT"}}}},
    {"type": "event_msg", "timestamp": "2026-11-02T20:00:01Z", "payload": {"type": "token_count", "info": {
     "total_token_usage": {"input_tokens": 15, "cached_input_tokens": 5,
                           "cache_write_input_tokens": 0, "output_tokens": 4}}}},
]


class CodexProjectionTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "fictional.jsonl"
        self.path.write_text("".join(json.dumps(row) + "\n" for row in ROWS))

    def tearDown(self):
        self.temporary.cleanup()

    def test_projection_drops_content_paths_and_separate_response_usage(self):
        projected = read_jsonl(self.path, shape=codex_row)
        encoded = json.dumps(projected)
        for value in SECRET:
            self.assertNotIn(value, encoded)
        self.assertEqual(projected[2:4], [{}, {}])
        self.assertEqual(set(projected[4]["payload"]["info"]["total_token_usage"]),
                         {"input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens"})

    def test_projected_events_match_raw_cumulative_adapter_without_double_counting(self):
        projected = [codex_row(row) for row in ROWS]
        self.assertEqual(codex(ROWS, "chosen", "fictional-project"),
                         codex(projected, "chosen", "fictional-project"))
        events = load(self.path, "codex", "chosen", "fictional-project")
        self.assertEqual(len(events), 2)
        self.assertEqual([row["tokens"] for row in events], [
            {"input": 7, "cache_read": 3, "cache_write": 0, "output": 2},
            {"input": 3, "cache_read": 2, "cache_write": 0, "output": 2},
        ])
        for value in SECRET:
            self.assertNotIn(value, json.dumps(events))

    def test_nonzero_cache_write_refuses_until_its_native_semantics_are_qualified(self):
        rows = json.loads(json.dumps(ROWS))
        rows[4]["payload"]["info"]["total_token_usage"]["cache_write_input_tokens"] = 2
        self.path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        with self.assertRaises(ValueError):
            load(self.path, "codex", "chosen", "fictional-project")


if __name__ == "__main__":
    unittest.main()
