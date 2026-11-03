from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from monitoring_ai.codeburn_status import read_codeburn_status


def row(**updates):
    value = {
        "id": "private-session-123",
        "provider": "claude-code",
        "project": "/private/project/path",
        "branch": "private-branch",
        "model": "claude-opus-4-5",
        "contextTokens": 12000,
        "contextWindow": 200000,
        "startedAt": "2026-11-03T08:00:00Z",
        "lastActivityAt": "2026-11-03T08:04:30Z",
        "idleSeconds": 30,
    }
    value.update(updates)
    return value


def status(block_marker=True, **updates):
    value = {"generated": "2026-11-03T08:05:00Z", "current": {"private": "ignore"}}
    if block_marker:
        value["liveSessions"] = {"windowSeconds": 600, "sessions": [row()]}
    value.update(updates)
    return value


class CodeBurnStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "status.json"
        self.salt = b"synthetic-salt-material"

    def tearDown(self):
        self.temp.cleanup()

    def write(self, value):
        self.path.write_text(json.dumps(value), encoding="utf-8")

    def test_projects_ephemeral_alias_and_safe_session_metadata_only(self):
        self.write(status())
        result = read_codeburn_status(self.path, alias_salt=self.salt)

        self.assertEqual(result["schema"], "monitoring.codeburn-activity.v1")
        self.assertEqual(result["coverage"], "recent-transcript-write-not-process-liveness")
        self.assertEqual(result["window_seconds"], 600)
        self.assertEqual(len(result["sessions"]), 1)
        session = result["sessions"][0]
        self.assertEqual(session, {
            "alias": read_codeburn_status(self.path, alias_salt=self.salt)["sessions"][0]["alias"],
            "provider": "claude-code", "model": "claude-opus-4-5",
            "started_at": "2026-11-03T08:00:00.000Z",
            "last_activity_at": "2026-11-03T08:04:30.000Z",
            "context_tokens": 12000, "context_window": 200000,
        })
        self.assertTrue(session["alias"].startswith("s-"))
        self.assertEqual(len(session["alias"]), 34)
        rendered = json.dumps(result)
        for private in ("private-session-123", "/private/project/path", "private-branch", "private\":\"ignore"):
            self.assertNotIn(private, rendered)
        self.assertTrue(result["sampled_at"].endswith("Z"))
        self.assertTrue(result["file_modified_at"].endswith("Z"))

    def test_alias_is_process_salted_and_distinguishes_native_ids(self):
        value = status()
        value["liveSessions"]["sessions"].append(row(id="different-private-id"))
        self.write(value)
        first = read_codeburn_status(self.path, alias_salt=self.salt)["sessions"]
        second = read_codeburn_status(self.path, alias_salt=b"another-synthetic-salt")["sessions"]
        self.assertNotEqual(first[0]["alias"], first[1]["alias"])
        self.assertNotEqual(first[0]["alias"], second[0]["alias"])

    def test_model_names_use_the_same_safe_alias_policy_as_daily_rollups(self):
        self.write(status(liveSessions={"windowSeconds": 600, "sessions": [row(model="Claude Sonnet 4.6 (route)")]}))
        model = read_codeburn_status(self.path, alias_salt=self.salt)["sessions"][0]["model"]
        self.assertTrue(model.startswith("Claude-Sonnet-4.6-route-"))
        self.assertNotIn(" ", model)

    def test_absent_live_block_is_unknown_but_empty_block_is_reported_zero(self):
        self.write(status(block_marker=False))
        absent = read_codeburn_status(self.path, alias_salt=self.salt)
        self.assertIsNone(absent["sessions"])
        self.assertIsNone(absent["window_seconds"])

        self.write(status(block_marker=False, liveSessions={"windowSeconds": 600, "sessions": []}))
        empty = read_codeburn_status(self.path, alias_salt=self.salt)
        self.assertEqual(empty["sessions"], [])
        self.assertEqual(empty["window_seconds"], 600)

    def test_age_comes_from_generation_and_activity_timestamps_not_stale_idle_seconds(self):
        now = datetime.now(timezone.utc)
        old = (now - timedelta(hours=2)).isoformat()
        self.write(status(generated=old))
        result = read_codeburn_status(self.path, alias_salt=self.salt)
        self.assertGreater(result["source_age_seconds"], 3600)
        self.assertEqual(result["sessions"][0]["last_activity_at"], "2026-11-03T08:04:30.000Z")
        self.assertNotIn("idle_seconds", result["sessions"][0])

        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        self.write(status(generated=future))
        future = read_codeburn_status(self.path, alias_salt=self.salt)
        self.assertLess(future["source_age_seconds"], 0)

    def test_rejects_malformed_live_block_fields_and_duplicate_aliases(self):
        missing_model = row()
        del missing_model["model"]
        cases = [
            status(liveSessions=None),
            status(liveSessions={"windowSeconds": 0, "sessions": []}),
            status(liveSessions={"windowSeconds": 600, "sessions": {}}),
            status(liveSessions={"windowSeconds": 600, "sessions": [row(contextTokens=True)]}),
            status(liveSessions={"windowSeconds": 600, "sessions": [row(contextWindow=0)]}),
            status(liveSessions={"windowSeconds": 600, "sessions": [missing_model]}),
            status(liveSessions={"windowSeconds": 600, "sessions": [row(provider="/private/path")]}),
            status(liveSessions={"windowSeconds": 600, "sessions": [row(lastActivityAt="no-time")] }),
        ]
        duplicate = status()
        duplicate["liveSessions"]["sessions"].append(row())
        cases.append(duplicate)
        for value in cases:
            with self.subTest(value=value):
                self.write(value)
                with self.assertRaises(ValueError) as error:
                    read_codeburn_status(self.path, alias_salt=self.salt)
                self.assertNotIn(str(self.path), str(error.exception))

    def test_rejects_duplicate_json_keys_bad_root_salt_and_oversized_file(self):
        self.path.write_text('{"generated":"2026-11-03T08:00:00Z","generated":"2026-11-03T08:00:00Z"}')
        with self.assertRaises(ValueError):
            read_codeburn_status(self.path, alias_salt=self.salt)
        self.write([])
        with self.assertRaises(ValueError):
            read_codeburn_status(self.path, alias_salt=self.salt)
        self.write(status(block_marker=False))
        for bad_salt in (None, b"short", "string-not-bytes"):
            with self.subTest(salt=bad_salt), self.assertRaises(ValueError):
                read_codeburn_status(self.path, alias_salt=bad_salt)
        with patch("monitoring_ai.codeburn_status.MAX_BYTES", 8):
            with self.assertRaises(ValueError):
                read_codeburn_status(self.path, alias_salt=self.salt)

    def test_rejects_symlink_and_nonregular_file(self):
        self.write(status(block_marker=False))
        link = self.path.with_name("status-link.json")
        link.symlink_to(self.path)
        with self.assertRaises(ValueError):
            read_codeburn_status(link, alias_salt=self.salt)
        with self.assertRaises(ValueError):
            read_codeburn_status(self.path.parent, alias_salt=self.salt)


if __name__ == "__main__":
    unittest.main()
