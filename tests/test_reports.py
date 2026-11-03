import json
from pathlib import Path
import tempfile
import unittest

from monitoring_ai.metrics import Exchange, Pricing
from monitoring_ai.reports import build_report

NOW = "2026-11-02T20:00:00.000Z"


def usage(ident, session="s1", *, project="app", model="model-a", tokens=None,
          cost=None, duration=None, timestamp=NOW):
    row = {"id": ident, "source": "synthetic", "session": session, "entity": session,
           "kind": "usage", "provider": "provider-a", "model": model, "project": project,
           "surface": "editor", "timestamp": timestamp,
           "tokens": tokens or {"input": 100, "cache_read": 50, "cache_write": 25, "output": 10}}
    if cost is not None:
        row["cost"] = cost
    if duration is not None:
        row["duration_ms"] = duration
    return row


def tool(ident, name, category=None, *, outcome="ok", duration=None, server=None, session="s1"):
    row = {"id": ident, "source": "synthetic", "session": session, "entity": session,
           "kind": "tool", "provider": "provider-a", "model": "model-a", "project": "app",
           "surface": "editor", "timestamp": NOW, "tool": name, "outcome": outcome}
    if category is not None:
        row["tool_category"] = category
    if duration is not None:
        row["duration_ms"] = duration
    if server is not None:
        row["tool_server"] = server
    return row


ESTIMATED_2 = {"amount": "2", "currency": "USD", "basis": "estimated", "provenance": "test-price"}
BILLED_8 = {"amount": "8", "currency": "USD", "basis": "billed", "provenance": "test-invoice"}


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.pricing = Pricing()
        self.fx = Exchange()

    def report(self, current, **kwargs):
        return build_report(current, pricing=self.pricing, fx=self.fx, now=NOW, **kwargs)

    def test_qualified_session_estimates_and_cache_share_denominator(self):
        rows = [
            usage("a", "s1", cost=ESTIMATED_2),
            usage("b", "s1", cost=ESTIMATED_2),
            usage("c", "s2", cost={**ESTIMATED_2, "amount": "6"}),
        ]
        result = self.report(rows)
        summary = result["summary"]
        self.assertEqual(summary["observed_sessions"], 2)
        self.assertEqual(summary["estimated_session_count"], 2)
        self.assertEqual(summary["known_estimate_per_priced_session"], "5")
        self.assertEqual(summary["cache_read_share_percent"], "28.57142857142857142857142857")

    def test_observed_sessions_include_tool_and_status_only_sessions(self):
        rows = [usage("a", "priced-a", cost=ESTIMATED_2),
                usage("b", "priced-b", cost={**ESTIMATED_2, "amount": "6"}),
                tool("tool-only", "shell-task", "shell", session="tool-session"),
                {"id": "status-only", "source": "synthetic", "session": "status-session",
                 "entity": "status-session", "kind": "status", "timestamp": NOW,
                 "state": "completed"}]
        result = self.report(rows)
        self.assertEqual(result["summary"]["observed_sessions"], 4)
        self.assertEqual(result["summary"]["estimated_session_count"], 2)
        self.assertEqual(result["summary"]["known_estimate_per_priced_session"], "4")
        self.assertEqual(result["comparisons"]["sessions"]["current"], 4)

    def test_mixed_basis_and_unknown_records_refuse_cost_comparisons(self):
        current = [usage("a", cost=ESTIMATED_2), usage("b", cost=BILLED_8), usage("c")]
        previous = [usage("p", cost=ESTIMATED_2)]
        result = self.report(current, previous_events=previous)
        estimated = result["comparisons"]["estimated_cost"]
        billed = result["comparisons"]["billed_cost"]
        self.assertEqual(estimated["current"], "2")
        self.assertIsNone(estimated["change"])
        self.assertEqual(estimated["coverage_reason"], "incomplete-cost-coverage")
        self.assertIsNone(billed["change"])
        self.assertEqual(result["coverage"]["unknown_cost_events"], 1)

    def test_comparison_zero_baseline_and_unavailable_previous(self):
        current = [usage("a", tokens={"output": 10}, cost={**ESTIMATED_2, "amount": "1"})]
        previous = [usage("p", tokens={"output": 0}, cost={**ESTIMATED_2, "amount": "0"})]
        result = self.report(current, previous_events=previous)
        self.assertEqual(result["comparisons"]["estimated_cost"]["change"], "1")
        self.assertIsNone(result["comparisons"]["estimated_cost"]["percent_change"])
        self.assertIsNone(result["comparisons"]["estimated_cost"]["coverage_reason"])
        absent = self.report(current)["comparisons"]["estimated_cost"]
        self.assertEqual(absent["coverage_reason"], "previous-period-unavailable")
        self.assertIsNone(absent["change"])

        empty_previous = self.report(current, previous_events=[])["comparisons"]["estimated_cost"]
        self.assertIsNone(empty_previous["previous"])
        self.assertIsNone(empty_previous["change"])
        self.assertEqual(empty_previous["coverage_reason"], "incomplete-cost-coverage")

    def test_explicit_fx_metadata_audits_converted_report_amounts(self):
        fictional_book = {"schema": "monitoring.fx.v1", "base": "USD", "as_of": NOW,
                          "provenance": "fictional-report-rate", "rates": {"EUR": "0.8"}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fictional-fx.json"
            path.write_text(json.dumps(fictional_book))
            fx = Exchange("EUR", path)
            current = [usage("a", cost=ESTIMATED_2),
                       usage("b", "s2", cost={**ESTIMATED_2, "amount": "1"})]
            previous = [usage("p", cost=ESTIMATED_2)]
            result = build_report(current, previous_events=previous, pricing=self.pricing,
                                  fx=fx, now=NOW)
        self.assertEqual(result["currency"], "EUR")
        self.assertEqual(result["fx"]["as_of"], NOW)
        self.assertEqual(result["fx"]["provenance"], "fictional-report-rate")
        self.assertEqual(result["summary"]["costs"]["estimated"]["amount"], "2.4")
        self.assertEqual(result["comparisons"]["estimated_cost"]["current"], "2.4")
        self.assertEqual(result["comparisons"]["estimated_cost"]["previous"], "1.6")
        self.assertEqual(result["comparisons"]["estimated_cost"]["change"], "0.8")

    def test_timing_uses_explicit_durations_and_positive_duration_rate_subset(self):
        rows = [
            usage("a", tokens={"output": 10}, duration=1000),
            usage("b", tokens={"output": 500}, duration=0),
            usage("c", tokens={"output": 50}),
        ]
        result = self.report(rows)
        timing = result["breakdowns"]["projects"]["rows"][0]["timing"]
        self.assertEqual(timing["records"], 2)
        self.assertEqual(timing["p50_ms"], 0)
        self.assertEqual(timing["p95_ms"], 1000)
        self.assertEqual(timing["output_rate_records"], 1)
        self.assertEqual(timing["output_tokens_per_second"], "10")
        self.assertEqual(result["coverage"]["missing_usage_duration_records"], 1)
        self.assertIn("not model streaming speed", result["coverage"]["duration_basis"])

    def test_tool_groups_use_explicit_categories_and_do_not_guess_names(self):
        rows = [
            tool("a", "read_file", "mcp", server="docs-server", outcome="error", duration=30),
            tool("b", "exec", "shell", duration=10),
            tool("c", "review-skill", "skill"),
            tool("d", "worker-start", "agent"),
            tool("e", "mcp__guessed__search"),
        ]
        tools = self.report(rows)["tools"]
        self.assertEqual(tools["records"], 5)
        self.assertEqual(tools["errors"], 1)
        self.assertEqual(tools["unknown_outcomes"], 0)
        self.assertEqual(tools["unclassified_records"], 1)
        self.assertEqual([row["key"] for row in tools["mcp_servers"]], ["docs-server"])
        self.assertEqual([row["key"] for row in tools["shell_commands"]], ["exec"])
        self.assertEqual([row["key"] for row in tools["skills"]], ["review-skill"])
        self.assertEqual([row["key"] for row in tools["agents"]], ["worker-start"])
        guessed_name = next(row for row in tools["names"] if row["key"] == "mcp__guessed__search")
        self.assertEqual(guessed_name["category"], "unclassified")

    def test_group_rows_are_bounded_without_losing_aggregate_totals(self):
        rows = [usage(f"e{i}", project=f"project-{i:03}", model=f"model-{i:03}", tokens={"output": 1})
                for i in range(100)]
        rows.append(usage("dominant", project="z-project", model="z-model", tokens={"output": 1000}))
        result = self.report(rows)
        self.assertEqual(result["summary"]["calls"], 101)
        self.assertEqual(result["summary"]["total_tokens"], 1100)
        self.assertEqual(len(result["breakdowns"]["projects"]["rows"]), 100)
        self.assertTrue(result["breakdowns"]["projects"]["truncated"])
        self.assertTrue(result["coverage"]["truncated"]["projects"])
        self.assertEqual(result["breakdowns"]["projects"]["rows"][0]["key"], "z-project")
        self.assertEqual(result["breakdowns"]["models"]["rows"][0]["key"], "z-model")
        self.assertEqual(result["coverage"]["selection_basis"]["daily"], "date_ascending")
        self.assertEqual(len(result["breakdowns"]["daily"]["rows"]), 1)

    def test_tool_breakdown_rows_are_also_bounded(self):
        rows = [tool(f"t{i}", f"cmd-{i:03}", "shell") for i in range(101)]
        rows.extend(tool(f"z{i}", "z-command", "shell") for i in range(5))
        result = self.report(rows)
        self.assertEqual(result["tools"]["records"], 106)
        self.assertEqual(len(result["tools"]["shell_commands"]), 100)
        self.assertTrue(result["tools"]["truncated"]["shell_commands"])
        self.assertTrue(result["coverage"]["truncated"]["tools_shell_commands"])
        self.assertEqual(result["tools"]["shell_commands"][0]["key"], "z-command")

    def test_ranges_currency_unsupported_metrics_and_duration_coverage(self):
        result = self.report([usage("a")], current_range={"from": "a", "until": "b"},
                             previous_range={"from": "c", "until": "d"})
        self.assertEqual(result["schema"], "monitoring.report.v1")
        self.assertEqual(result["range"], {"from": "a", "until": "b"})
        self.assertEqual(result["previous_range"], {"from": "c", "until": "d"})
        self.assertEqual(result["currency"], "USD")
        self.assertIsNone(result["comparisons"]["records"]["previous"])
        self.assertEqual(result["coverage"]["missing_usage_duration_records"], 1)
        self.assertEqual(result["coverage"]["unsupported"], [
            "context-overhead", "one-shot-rate", "corrections", "time-to-first-edit", "rework"])


if __name__ == "__main__":
    unittest.main()
