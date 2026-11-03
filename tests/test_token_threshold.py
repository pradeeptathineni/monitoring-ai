"""Observed-token thresholds remain distinct from provider allowance and billing."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from monitoring_ai.events import validate
from monitoring_ai.metrics import Exchange, Pricing
from monitoring_ai.server import Observatory
from monitoring_ai.store import Store
from monitoring_ai.token_threshold import evaluate_token_threshold, validate_limit


NOW = "2026-11-03T12:00:00.000Z"


def usage(ident, when, tokens, *, project="example", surface="local"):
    return validate({"schema": "monitoring.event.v1", "id": ident, "source": "synthetic",
                     "session": project, "entity": project, "parent": None,
                     "entity_type": "session", "kind": "usage", "timestamp": when,
                     "provider": "example", "model": "example", "surface": surface,
                     "project": project, "tokens": tokens})


class TokenThresholdTests(unittest.TestCase):
    def test_elapsed_utc_day_and_disjoint_tokens_exclude_receipt_and_future_rows(self):
        rows = [
            usage("today", "2026-11-03T11:59:00Z", {"input": 2, "cache_read": 3, "output": 5}),
            usage("yesterday", "2026-11-02T23:59:59Z", {"input": 50}),
            usage("future", "2026-11-03T12:00:01Z", {"input": 80}),
            usage("receipt", "2026-11-03T10:00:00Z", {"input": 100}, surface="claude-code-result"),
        ]
        value = evaluate_token_threshold(rows, limit=10, now=NOW)
        self.assertEqual(value["state"], "observed-reached")
        self.assertEqual(value["observed_tokens"], 10)
        self.assertEqual(value["remaining_observed_tokens"], 0)
        self.assertEqual(value["usage_records"], 1)
        self.assertEqual(value["excluded_receipt_summaries"], 1)
        self.assertEqual(value["excluded_future_records"], 1)
        self.assertFalse(value["complete_coverage"])
        self.assertEqual(value["window"]["until"], "2026-11-04T00:00:00.000Z")

    def test_no_observation_and_invalid_limit_do_not_claim_under_budget(self):
        value = evaluate_token_threshold([], limit=1, now=NOW)
        self.assertEqual(value["state"], "no-observations")
        for bad in (0, -1, True, 1.2, 1_000_000_000_001):
            with self.assertRaises(ValueError):
                validate_limit(bad)

    def test_owner_threshold_uses_all_accepted_events_independent_of_view_filters(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "events.sqlite")
            store.ingest([
                usage("one", "2026-11-03T10:00:00Z", {"input": 7}, project="one"),
                usage("two", "2026-11-03T11:00:00Z", {"input": 8}, project="two"),
            ])
            owner = Observatory(store, Pricing(), Exchange(), token_threshold=12)
            with patch("monitoring_ai.metrics.now_iso", return_value=NOW):
                value = owner.read({"project": "one"})
            self.assertEqual(value["summary"]["total_tokens"], 7)
            self.assertEqual(value["token_threshold"]["observed_tokens"], 15)
            self.assertEqual(value["token_threshold"]["state"], "observed-reached")
            self.assertEqual(value["token_threshold"]["scope"], "all-accepted-events-unfiltered")

    def test_invalid_cli_threshold_refuses_before_creating_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "would-create.sqlite"
            result = subprocess.run([sys.executable, "-m", "monitoring_ai", "top", "--db", str(path),
                                     "--token-threshold", "0"], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
