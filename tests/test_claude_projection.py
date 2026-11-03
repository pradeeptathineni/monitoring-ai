"""Claude JSONL rows are projected to consumed metadata before accumulation."""

import json
from pathlib import Path
import tempfile
import unittest

from monitoring_ai.adapters import claude, claude_row, load, read_jsonl

SECRETS = ("PROMPT-TEXT", "ASSISTANT-TEXT", "THINKING-TEXT", "TOOL-ARGUMENT", "TOOL-RESULT",
           "/synthetic/workdir", "synthetic-branch", "EXTRA-METADATA")

ROWS = [
    {"type": "user", "sessionId": "session-a", "timestamp": "2026-01-01T00:00:00Z",
     "cwd": "/synthetic/workdir", "gitBranch": "synthetic-branch", "extra": "EXTRA-METADATA",
     "message": {"role": "user", "content": "PROMPT-TEXT"}},
    {"type": "assistant", "sessionId": "session-a", "timestamp": "2026-01-01T00:00:01Z",
     "cwd": "/synthetic/workdir",
     "message": {"role": "assistant", "id": "msg-1", "model": "claude-sonnet-5-5",
                 "usage": {"input_tokens": 10, "cache_read_input_tokens": 3,
                           "cache_creation_input_tokens": 2, "output_tokens": 7,
                           "server_tool_use": {"note": "EXTRA-METADATA"}},
                 "content": [{"type": "thinking", "thinking": "THINKING-TEXT"},
                             {"type": "text", "text": "ASSISTANT-TEXT"},
                             {"type": "tool_use", "id": "call-1", "name": "Bash",
                              "input": {"command": "TOOL-ARGUMENT"}}]}},
    {"type": "user", "sessionId": "session-a", "timestamp": "2026-01-01T00:00:02Z",
     "toolUseResult": {"stdout": "TOOL-RESULT"},
     "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-1",
                                              "is_error": True, "content": "TOOL-RESULT"}]}},
]


class ClaudeProjectionTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "synthetic.jsonl"
        self.path.write_text("".join(json.dumps(row) + "\n" for row in ROWS))

    def tearDown(self):
        self.directory.cleanup()

    def assertNoSecrets(self, value):
        dumped = json.dumps(value)
        for secret in SECRETS:
            self.assertNotIn(secret, dumped)

    def test_projection_drops_text_arguments_and_metadata(self):
        projected = [claude_row(row) for row in ROWS]
        self.assertNoSecrets(projected)
        self.assertEqual(projected[1]["message"]["content"], [{"type": "tool_use", "id": "call-1", "name": "Bash"}])
        self.assertEqual(projected[2]["message"]["content"],
                         [{"type": "tool_result", "tool_use_id": "call-1", "is_error": True}])
        self.assertEqual(set(projected[1]["message"]["usage"]),
                         {"input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens"})

    def test_projection_preserves_parsed_events(self):
        projected = [claude_row(row) for row in ROWS]
        self.assertEqual(claude(ROWS, "synthetic-source", "synthetic-project"),
                         claude(projected, "synthetic-source", "synthetic-project"))

    def test_claude_load_projects_before_accumulation(self):
        self.assertNoSecrets(read_jsonl(self.path, shape=claude_row))
        self.assertEqual(read_jsonl(self.path)[0]["cwd"], "/synthetic/workdir")  # Other formats read unchanged rows
        events = load(self.path, "claude", source="synthetic-source", project="synthetic-project")
        self.assertNoSecrets(events)
        usage = [event for event in events if event["kind"] == "usage"]
        tools = [event for event in events if event["kind"] == "tool"]
        self.assertEqual(len(usage), 1)
        self.assertEqual(usage[0]["tokens"], {"input": 10, "cache_read": 3, "cache_write": 2, "output": 7})
        self.assertEqual(len(tools), 1)
        self.assertEqual((tools[0]["tool"], tools[0]["tool_category"], tools[0]["outcome"]), ("Bash", "shell", "error"))

    def test_live_read_still_drops_incomplete_final_line(self):
        with self.path.open("a") as stream:
            stream.write('{"type": "assistant", "message": {"content": "PROMPT-TEXT"')
        self.assertEqual(len(read_jsonl(self.path, live=True, shape=claude_row)), len(ROWS))


if __name__ == "__main__":
    unittest.main()
