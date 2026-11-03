"""CLI-reported rate-limit windows stay separate from usage and billing."""

from io import BytesIO
import json
from pathlib import Path
import stat
import tempfile
import unittest

from monitoring_ai.claude_rate_limits import claude_rate_limit_book
from monitoring_ai.claude_stream import capture_stream
from monitoring_ai.quota_sources import quota_file


INIT = {"type": "system", "subtype": "init", "session_id": "private-session",
        "uuid": "private-invocation", "model": "example-model"}
RATE = {"type": "rate_limit_event", "session_id": "private-session", "uuid": "private-event",
        "rate_limit_info": {"status": "allowed_warning", "isUsingOverage": False,
                            "overageDisabledReason": "private-account-detail",
                            "unifiedWindows": {
                                "five_hour": {"utilization": 0.27, "resetsAt": 1793690000},
                                "seven_day": {"utilization": 0.54, "resetsAt": 1794270000}}}}
RESULT = {"type": "result", "session_id": "private-session", "subtype": "success",
          "is_error": False, "result": "private reply"}


def stream(*rows):
    return BytesIO(b"".join((json.dumps(row) + "\n").encode() for row in rows))


class ClaudeRateLimitTests(unittest.TestCase):
    def test_stream_captures_two_windows_without_identity_or_overage(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            times = iter(("2026-11-03T09:00:00Z", "2026-11-03T09:00:01Z",
                          "2026-11-03T09:00:02Z"))
            status, quota = root / "status.jsonl", root / "quota.json"
            receipt = capture_stream(stream(INIT, RATE, RESULT), status,
                                     source="cli", project="example", quota_output=quota,
                                     clock=lambda: next(times))
            self.assertEqual(receipt["quota_observation"], "captured")
            self.assertEqual(stat.S_IMODE(quota.stat().st_mode), 0o600)
            book = quota_file(quota)
            self.assertEqual(len(book["snapshots"]), 2)
            self.assertTrue(all(s["captured_at"] == "2026-11-03T09:00:01.000Z"
                                for s in book["snapshots"]))
            self.assertTrue(all(s["provenance"] == "claude-cli-rate-limit-allowed_warning"
                                for s in book["snapshots"]))
            self.assertEqual([(w["id"], w["used_percent"], w["window_minutes"])
                              for snap in book["snapshots"] for w in snap["windows"]],
                             [("five-hour", 27.0, 300), ("seven-day", 54.0, 10080)])
            self.assertNotIn("private-session", quota.read_text())
            self.assertNotIn("overage", quota.read_text())
            self.assertEqual([json.loads(line)["state"] for line in status.read_text().splitlines()],
                             ["running", "completed"])

    def test_no_rate_frame_keeps_quota_unavailable(self):
        with tempfile.TemporaryDirectory() as root:
            status, quota = Path(root) / "status.jsonl", Path(root) / "quota.json"
            receipt = capture_stream(stream(INIT, RESULT), status, source="cli",
                                     project="example", quota_output=quota)
            self.assertEqual(receipt["quota_observation"], "unavailable")
            self.assertFalse(quota.exists())

    def test_invalid_latest_window_keeps_earlier_dated_windows(self):
        with tempfile.TemporaryDirectory() as root:
            status, quota = Path(root) / "status.jsonl", Path(root) / "quota.json"
            bad = {**RATE, "rate_limit_info": {**RATE["rate_limit_info"],
                   "unifiedWindows": {"five_hour": {"utilization": 1.5, "resetsAt": 1793690000}}}}
            receipt = capture_stream(stream(INIT, RATE, bad, RESULT), status,
                                     source="cli", project="example", quota_output=quota)
            self.assertEqual(receipt["quota_observation"], "partial")
            self.assertEqual(receipt["quota_rejected_events"], 1)
            self.assertEqual(len(quota_file(quota)["snapshots"]), 2)
            self.assertEqual([json.loads(line)["state"] for line in status.read_text().splitlines()],
                             ["running", "completed"])

    def test_later_window_update_keeps_other_windows_original_receipt_time(self):
        with tempfile.TemporaryDirectory() as root:
            status, quota = Path(root) / "status.jsonl", Path(root) / "quota.json"
            later = {**RATE, "rate_limit_info": {"status": "allowed", "unifiedWindows": {
                "five_hour": {"utilization": 0.31, "resetsAt": 1793690000}}}}
            times = iter(("2026-11-03T09:00:00Z", "2026-11-03T09:00:01Z",
                          "2026-11-03T09:00:02Z", "2026-11-03T09:00:03Z"))
            receipt = capture_stream(stream(INIT, RATE, later, RESULT), status,
                                     source="cli", project="example", quota_output=quota,
                                     clock=lambda: next(times))
            self.assertEqual(receipt["quota_observation"], "captured")
            snaps = {s["windows"][0]["id"]: s for s in quota_file(quota)["snapshots"]}
            self.assertEqual(snaps["five-hour"]["captured_at"], "2026-11-03T09:00:02.000Z")
            self.assertEqual(snaps["five-hour"]["windows"][0]["used_percent"], 31.0)
            self.assertEqual(snaps["seven-day"]["captured_at"], "2026-11-03T09:00:01.000Z")

    def test_documented_top_level_utilization_and_unknown_window(self):
        row = {"type": "rate_limit_event", "rate_limit_info": {
            "status": "allowed", "rateLimitType": "seven_day_sonnet",
            "utilization": 0.125, "resetsAt": 1794270000}}
        book = claude_rate_limit_book(row, captured_at="2026-11-03T09:00:00Z",
                                      source="cli", account="local")
        self.assertEqual(book["snapshots"][0]["windows"][0]["used_percent"], 12.5)
        with self.assertRaisesRegex(ValueError, "no supported window"):
            claude_rate_limit_book({"type": "rate_limit_event", "rate_limit_info": {
                "status": "allowed", "rateLimitType": "overage", "utilization": 0.5}},
                captured_at="2026-11-03T09:00:00Z", source="cli", account="local")


if __name__ == "__main__":
    unittest.main()
