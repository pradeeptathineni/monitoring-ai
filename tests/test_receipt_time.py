import unittest

from monitoring_ai.insights import build_insights
from monitoring_ai.metrics import Exchange, Pricing, snapshot
from monitoring_ai.reporting import detailed_report

NOW = "2026-11-02T20:00:00.000Z"


def usage(ident, when, *, project="app", surface="editor", tokens=60, cost=None):
    row = {
        "schema": "monitoring.event.v1", "id": ident, "source": "synthetic",
        "session": f"session-{ident}", "entity": f"session-{ident}", "parent": None,
        "entity_type": "session", "timestamp": when, "kind": "usage",
        "provider": "anthropic", "model": "model-a", "surface": surface,
        "project": project, "tokens": {"input": tokens, "cache_read": 0,
                                         "cache_write": 0, "output": 0},
    }
    if cost is not None:
        row["cost"] = {"amount": cost, "currency": "USD", "basis": "estimated",
                       "provenance": "synthetic-estimate"}
    return row


class ReceiptTimeTests(unittest.TestCase):
    def setUp(self):
        self.pricing = Pricing()
        self.fx = Exchange()

    def test_summary_records_do_not_distort_pace_or_cost_forecast(self):
        events = [
            usage("a", "2026-11-02T19:59:00Z", tokens=60, cost="0.02"),
            usage("summary", "2026-11-02T19:59:30Z", surface="claude-code-result",
                  tokens=900000, cost="99"),
            usage("b", NOW, tokens=60, cost="0.02"),
        ]
        result = build_insights(events, now=NOW, pricing=self.pricing, fx=self.fx)
        self.assertEqual(result["pace"]["records"], 2)
        self.assertEqual(result["pace"]["excluded_summary_records"], 1)
        self.assertEqual(result["pace"]["tokens"], 120)
        self.assertEqual(result["pace"]["tokens_per_minute"], "2")
        self.assertEqual(result["pace"]["estimated_cost_per_hour"], "0.04")
        self.assertEqual(result["forecast"]["tokens"], 120)
        self.assertEqual(result["forecast"]["estimated_cost"], "0.04")

    def test_summary_only_has_no_rate_or_forecast_but_remains_visible_in_freshness(self):
        events = [usage("a", "2026-11-02T19:00:00Z", surface="claude-code-result", tokens=100000),
                  usage("b", "2026-11-02T19:01:00Z", surface="claude-code-result", tokens=200000)]
        result = build_insights(events, now=NOW, pricing=self.pricing, fx=self.fx)
        self.assertEqual(result["pace"]["records"], 0)
        self.assertEqual(result["pace"]["excluded_summary_records"], 2)
        self.assertEqual(result["pace"]["tokens"], 0)
        self.assertEqual(result["pace"]["tokens_per_minute"], "0")
        self.assertIsNone(result["pace"]["estimated_cost_per_hour"])
        self.assertIsNone(result["forecast"]["tokens"])
        self.assertEqual(result["forecast"]["state"], "insufficient-data")
        self.assertEqual(result["source_freshness"][0]["source"], "synthetic")

    def test_snapshot_and_detailed_report_count_only_matching_current_summaries(self):
        events = [
            usage("a", "2026-11-02T19:59:00Z", tokens=60, cost="0.02"),
            usage("summary-current", "2026-11-02T19:59:30Z", surface="claude-code-result",
                  tokens=900000, cost="99"),
            usage("b", NOW, tokens=60, cost="0.02"),
            usage("summary-other-project", "2026-11-02T19:59:40Z", project="other",
                  surface="claude-code-result", tokens=500000),
            usage("summary-old", "2026-11-02T18:00:00Z", surface="claude-code-result", tokens=400000),
        ]
        snap = snapshot(12, events, query={"project": "app"}, pricing=self.pricing, fx=self.fx, now=NOW)
        self.assertEqual(snap["coverage"]["receipt_time_summary_records"], 2)
        self.assertEqual(snap["summary"]["calls"], 4)
        self.assertEqual(snap["summary"]["total_tokens"], 1300120)
        self.assertEqual(snap["insights"]["pace"]["records"], 2)
        self.assertEqual(snap["insights"]["pace"]["excluded_summary_records"], 1)

        report = detailed_report(
            12, events,
            {"project": "app", "since": "2026-11-02T19:58:00Z", "until": NOW},
            self.pricing, self.fx, now=NOW,
        )
        self.assertEqual(report["coverage"]["receipt_time_summary_records"], 1)
        self.assertEqual(report["summary"]["calls"], 3)
        self.assertEqual(report["summary"]["total_tokens"], 900120)


if __name__ == "__main__":
    unittest.main()
