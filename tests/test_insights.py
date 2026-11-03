import unittest
from decimal import Decimal

from monitoring_ai.insights import build_insights, validate_quota_book
from monitoring_ai.metrics import Exchange

NOW = "2026-11-02T20:00:00Z"


def usage(ident, when, tokens=60, **extra):
    row = {"id": ident, "source": "synthetic", "timestamp": when, "kind": "usage",
           "provider": "openai", "model": "test", "tokens": {"input": tokens}}
    row.update(extra)
    return row


class Pricing:
    def cost(self, event):
        return event.get("cost")


class InsightsTests(unittest.TestCase):
    def test_window_rate_excludes_future_and_uses_full_window(self):
        events = [usage("old", "2026-11-02T19:00:00Z", 60),
                  usage("new", "2026-11-02T19:01:00Z", 60),
                  usage("future", "2026-11-02T20:00:01Z", 1000)]
        result = build_insights(events, now=NOW, pricing=Pricing(), fx=Exchange(), window_minutes=60)
        self.assertEqual(result["pace"]["records"], 2)
        self.assertEqual(result["pace"]["tokens"], 120)
        self.assertEqual(result["pace"]["tokens_per_minute"], "2")
        self.assertEqual(result["forecast"]["tokens"], 120)
        self.assertEqual(result["forecast"]["observed_span_seconds"], 60)
        self.assertEqual(result["forecast"]["confidence"], "low")

    def test_forecast_requires_two_distinct_timestamps_at_least_a_minute_apart(self):
        events = [usage("a", "2026-11-02T19:59:30Z"), usage("b", "2026-11-02T19:59:59Z")]
        result = build_insights(events, now=NOW, pricing=Pricing(), fx=Exchange())
        self.assertIsNone(result["forecast"]["tokens"])
        self.assertEqual(result["forecast"]["state"], "insufficient-data")
        self.assertEqual(result["forecast"]["confidence"], "insufficient")
        self.assertEqual(result["forecast"]["observed_span_seconds"], 29)
        self.assertEqual(result["pace"]["tokens_per_minute"], "2")

    def test_long_observed_span_gets_limited_confidence_but_full_window_rate(self):
        events = [usage("a", "2026-11-02T19:49:59Z", 60), usage("b", NOW, 60)]
        result = build_insights(events, now=NOW, pricing=Pricing(), fx=Exchange(), window_minutes=60)
        self.assertEqual(result["forecast"]["observed_span_seconds"], 601)
        self.assertEqual(result["forecast"]["confidence"], "limited")
        self.assertEqual(result["pace"]["tokens_per_minute"], "2")

    def test_estimated_cost_coverage_and_billed_exclusion(self):
        events = [usage("a", "2026-11-02T19:00:00Z", cost={"amount": "1.20", "currency": "USD", "basis": "estimated"}),
                  usage("b", "2026-11-02T19:02:00Z", cost={"amount": "9", "currency": "USD", "basis": "billed"}),
                  usage("c", "2026-11-02T19:03:00Z")]
        result = build_insights(events, now=NOW, pricing=Pricing(), fx=Exchange())
        self.assertEqual(result["pace"]["estimated_cost_per_hour"], "1.20")
        self.assertEqual(result["pace"]["estimated_priced_records"], 1)
        self.assertEqual(result["pace"]["unknown_cost_records"], 1)
        self.assertEqual(result["pace"]["billed_records"], 1)
        self.assertIsNone(result["forecast"]["estimated_cost"])
        self.assertEqual(result["forecast"]["state"], "partial")

    def test_unconverted_estimates_remain_explicit(self):
        event = usage("a", "2026-11-02T19:00:00Z", cost={"amount": "2", "currency": "EUR", "basis": "estimated"})
        events = [event, usage("b", "2026-11-02T19:02:00Z", cost={"amount": "1", "currency": "USD", "basis": "estimated"})]
        result = build_insights(events, now=NOW, pricing=Pricing(), fx=Exchange())
        self.assertEqual(result["pace"]["unconverted_cost_records"], 1)
        self.assertEqual(result["pace"]["estimated_priced_records"], 1)
        self.assertEqual(result["forecast"]["state"], "partial")

    def test_source_freshness_is_independent_and_marks_future(self):
        events = [usage("a", "2026-11-02T19:00:00Z"),
                  {"id": "b", "source": "future-src", "kind": "status", "timestamp": "2026-11-02T20:00:00.500Z"}]
        result = build_insights(events, now=NOW, pricing=Pricing(), fx=Exchange())
        by_source = {row["source"]: row for row in result["source_freshness"]}
        self.assertEqual(by_source["synthetic"]["state"], "stale")
        self.assertEqual(by_source["future-src"]["state"], "future")
        self.assertEqual(by_source["future-src"]["age_seconds"], 0)

    def test_quota_book_validation_and_reset_age_interpretation(self):
        book = {"schema": "monitoring.quotas.v1", "snapshots": [{
            "source": "manual", "provider": "openai", "account": "team-a", "captured_at": "2026-11-02T19:59:00Z",
            "provenance": "operator", "windows": [
                {"id": "weekly", "label": "week", "used_percent": 75, "window_minutes": 10080, "resets_at": "2026-11-03T00:00:00Z"},
                {"id": "daily", "label": "day", "used_percent": None, "window_minutes": None, "resets_at": "2026-11-02T19:59:30Z"},
            ]}]}
        result = build_insights([], now=NOW, pricing=Pricing(), fx=Exchange(), quota_book=book)
        windows = result["quotas"]["windows"]
        self.assertEqual(windows[0]["freshness"], "current")
        self.assertEqual(windows[0]["remaining_percent"], 25)
        self.assertIsInstance(windows[0]["used_percent"], (int, float))
        self.assertEqual(windows[1]["freshness"], "reset-passed")
        self.assertIsNone(windows[1]["remaining_percent"])
        self.assertIn("source-reported", result["quotas"]["note"].lower())
        self.assertIn("usage filters", result["quotas"]["note"])
        aged_book = {"schema": "monitoring.quotas.v1", "snapshots": [
            dict(book["snapshots"][0], captured_at="2026-11-02T19:54:59Z"),
            dict(book["snapshots"][0], source="future", captured_at="2026-11-02T20:00:00.500Z"),
        ]}
        aged = build_insights([], now=NOW, pricing=Pricing(), fx=Exchange(), quota_book=aged_book)["quotas"]["windows"]
        self.assertEqual(aged[0]["freshness"], "stale")
        self.assertEqual(aged[2]["freshness"], "future")
        self.assertEqual(aged[2]["age_seconds"], 0)
        with self.assertRaises(ValueError):
            validate_quota_book({**book, "private_path": "/secret"})
        bad = {**book, "snapshots": [dict(book["snapshots"][0], windows=[dict(book["snapshots"][0]["windows"][0], used_percent=101)])]}
        with self.assertRaises(ValueError):
            validate_quota_book(bad)

    def test_quota_book_rejects_duplicate_window_identity_and_excess_windows(self):
        snapshot = {"source": "manual", "provider": "openai", "account": "team-a",
                    "captured_at": NOW, "provenance": "operator", "windows": [
                        {"id": "daily", "label": "day", "used_percent": 10,
                         "window_minutes": 1440, "resets_at": None}]}
        duplicate = {"schema": "monitoring.quotas.v1", "snapshots": [snapshot, snapshot]}
        with self.assertRaises(ValueError):
            validate_quota_book(duplicate)
        many = dict(snapshot, windows=[dict(snapshot["windows"][0], id=f"w{i}") for i in range(501)])
        with self.assertRaises(ValueError):
            validate_quota_book({"schema": "monitoring.quotas.v1", "snapshots": [many]})


if __name__ == "__main__":
    unittest.main()
