from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from monitoring_ai.codeburn_cache import read_codeburn_cache


def cache(days=None, **extra):
    value = {
        "version": 74,
        "savingsConfigHash": "discard-this",
        "tzKey": "Europe/Paris",
        "lastComputedDate": "2026-11-02",
        "complete": True,
        "watermarkTrusted": True,
        "days": days or [],
    }
    value.update(extra)
    return value


def day(date="2026-11-02", **extra):
    value = {
        "date": date,
        "cost": 1.25,
        "savingsUSD": 4.0,
        "calls": 3,
        "sessions": 2,
        "inputTokens": 10,
        "outputTokens": 20,
        "cacheReadTokens": 30,
        "cacheWriteTokens": 40,
        "models": {"/private/model": {"calls": 3, "cost": 1, "inputTokens": 10,
            "outputTokens": 20, "cacheReadTokens": 30, "cacheWriteTokens": 40}},
        "projects": {"/private/path": {"cost": 1}},
    }
    value.update(extra)
    return value


class CodeBurnCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "fixture.json"

    def tearDown(self):
        self.temp.cleanup()

    def write(self, value):
        self.path.write_text(json.dumps(value), encoding="utf-8")

    def test_projects_only_qualified_daily_fields_and_sorts_calendar_days(self):
        self.write(cache([day("2026-11-02", carried=True), day("2026-11-01", calls=0, sessions=0,
            inputTokens=0, outputTokens=0, cacheReadTokens=0, cacheWriteTokens=0, cost=0)]))

        result = read_codeburn_cache(self.path)

        self.assertEqual(result["schema"], "monitoring.cached-usage.v1")
        self.assertEqual(result["source"], "codeburn-daily-cache")
        self.assertEqual(result["format_version"], 74)
        self.assertEqual(result["date_basis"], "codeburn-local-calendar")
        self.assertTrue(result["complete"])
        self.assertTrue(result["watermark_trusted"])
        self.assertEqual(result["last_computed_date"], "2026-11-02")
        self.assertEqual([entry["date"] for entry in result["days"]], ["2026-11-01", "2026-11-02"])
        self.assertEqual({key: value for key, value in result["days"][1].items() if key != "breakdowns"}, {
            "date": "2026-11-02", "source_cost_usd": "1.25", "calls": 3, "sessions": 2,
            "tokens": {"input": 10, "output": 20, "cache_read": 30, "cache_write": 40}, "carried": True,
        })
        self.assertNotIn("savings", json.dumps(result))
        self.assertNotIn("/private/model", json.dumps(result))
        self.assertTrue(result["days"][1]["breakdowns"]["models"][0]["id"].startswith("opaque-"))
        self.assertIsNone(result["days"][1]["breakdowns"]["providers"])
        self.assertNotIn("/private/path", json.dumps(result))
        self.assertLessEqual(result["file_age_seconds"], 1)
        self.assertTrue(result["file_modified_at"].endswith("Z"))

    def test_missing_optional_watermark_metadata_is_explicitly_unknown_or_incomplete(self):
        self.write(cache([day()], complete=None, watermarkTrusted=None))
        with self.assertRaises(ValueError):
            read_codeburn_cache(self.path)
        self.write(cache([day()], **{}))
        raw = json.loads(self.path.read_text())
        del raw["complete"]
        del raw["watermarkTrusted"]
        self.path.write_text(json.dumps(raw))
        result = read_codeburn_cache(self.path)
        self.assertFalse(result["complete"])
        self.assertIsNone(result["watermark_trusted"])

    def test_rejects_ambiguous_or_unsupported_daily_records(self):
        cases = [
            cache([day(), day()]),
            cache([day("2026-02-30")]),
            cache([day(calls=True)]),
            cache([day(sessions=-1)]),
            cache([day(outputTokens=None)]),
            cache([day(cost=-0.01)]),
            cache([day(cost=float("inf"))]),
            cache([day(carried="yes")]),
            cache([day()], version=73),
        ]
        for value in cases:
            with self.subTest(value=value):
                self.write(value)
                with self.assertRaises(ValueError) as error:
                    read_codeburn_cache(self.path)
                self.assertNotIn(str(self.path), str(error.exception))

    def test_rejects_duplicate_json_keys_and_nonstandard_constants(self):
        self.path.write_text('{"version":74,"version":74,"days":[]}')
        with self.assertRaises(ValueError):
            read_codeburn_cache(self.path)
        self.path.write_text('{"version":74,"days":[],"cost":NaN}')
        with self.assertRaises(ValueError):
            read_codeburn_cache(self.path)

    def test_rejects_symlinks_nonregular_files_and_oversized_inputs(self):
        self.write(cache())
        link = self.root / "alias.json"
        link.symlink_to(self.path)
        with self.assertRaises(ValueError):
            read_codeburn_cache(link)
        with self.assertRaises(ValueError):
            read_codeburn_cache(self.root)
        with patch("monitoring_ai.codeburn_cache.MAX_BYTES", 16):
            with self.assertRaises(ValueError):
                read_codeburn_cache(self.path)

    def test_sample_and_file_times_are_utc_and_age_can_be_negative(self):
        self.write(cache())
        future = datetime.now(timezone.utc).timestamp() + 20
        os.utime(self.path, (future, future))
        result = read_codeburn_cache(self.path)
        self.assertLess(result["file_age_seconds"], 0)
        for key in ("sampled_at", "file_modified_at"):
            self.assertTrue(result[key].endswith("Z"))

    def test_breakdown_output_is_bounded_across_the_whole_cache(self):
        self.write(cache([day("2026-11-01"), day("2026-11-02")]))
        with patch("monitoring_ai.codeburn_cache.MAX_BREAKDOWN_ROWS", 1):
            with self.assertRaises(ValueError):
                read_codeburn_cache(self.path)


if __name__ == "__main__":
    unittest.main()
