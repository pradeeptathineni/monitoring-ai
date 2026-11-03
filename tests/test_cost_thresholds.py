"""Daily cost limits compare only known, converted evidence by basis."""

from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from monitoring_ai.cost_thresholds import evaluate_cost_thresholds, validate_limits
from monitoring_ai.events import validate
from monitoring_ai.metrics import Exchange, Pricing
from monitoring_ai.server import Observatory
from monitoring_ai.store import Store


NOW = "2026-11-03T12:00:00.000Z"


def usage(ident, at, *, cost=None, surface="local", project="example"):
    row = {"schema": "monitoring.event.v1", "id": ident, "source": "synthetic",
           "session": project, "entity": project, "parent": None,
           "entity_type": "session", "kind": "usage", "timestamp": at,
           "provider": "example", "model": "example", "surface": surface,
           "project": project, "tokens": {"input": 100}}
    if cost is not None:
        row["cost"] = cost
    return validate(row)


def cost(amount, basis="estimated", currency="USD"):
    return dict(amount=amount, basis=basis, currency=currency, provenance="fixture")


class CostThresholdTests(unittest.TestCase):
    def test_separate_bases_receipt_day_unknown_fx_and_future(self):
        rows = [
            usage("estimate", "2026-11-03T11:00:00Z", cost=cost("1.25")),
            usage("receipt", "2026-11-03T11:30:00Z", cost=cost("2.00"), surface="claude-code-result"),
            usage("billed", "2026-11-03T11:40:00Z", cost=cost("3", "billed")),
            usage("unknown", "2026-11-03T11:50:00Z"),
            usage("missing-fx", "2026-11-03T11:55:00Z", cost=cost("8", currency="EUR")),
            usage("future", "2026-11-03T12:01:00Z", cost=cost("50")),
            usage("prior", "2026-11-02T23:59:59Z", cost=cost("100")),
        ]
        value = evaluate_cost_thresholds(rows, limits={"estimated": "3", "billed": "4"},
                                         pricing=Pricing(), fx=Exchange(), now=NOW)
        self.assertFalse(value["complete_coverage"])
        self.assertEqual(value["window"]["until"], "2026-11-04T00:00:00.000Z")
        estimate, billed = value["items"]
        self.assertEqual((estimate["observed_amount"], estimate["state"]), ("3.25", "observed-reached"))
        self.assertEqual((estimate["covered_records"], estimate["receipt_time_records"]), (2, 1))
        self.assertEqual((estimate["unpriced_records"], estimate["unconverted_records"],
                          estimate["other_basis_records"], estimate["future_records"]), (1, 1, 1, 1))
        self.assertEqual((billed["observed_amount"], billed["state"], billed["covered_records"]),
                         ("3", "observed-below", 1))

    def test_no_converted_evidence_is_unknown_not_zero(self):
        value = evaluate_cost_thresholds([usage("missing", "2026-11-03T10:00:00Z")],
                                         limits={"estimated": "10"}, pricing=Pricing(), fx=Exchange(), now=NOW)
        self.assertEqual(value["items"][0]["state"], "no-observations")
        self.assertIsNone(value["items"][0]["observed_amount"])
        self.assertIsNone(value["items"][0]["remaining_observed_amount"])
        for invalid in ("0", "-1", "nan", "Infinity", "1000000001", "1e-7", "0.0000001", True):
            with self.assertRaises(ValueError):
                validate_limits({"estimated": invalid})

    def test_owner_threshold_is_independent_of_view_filter_and_prices(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            price = root / "prices.json"
            price.write_text(json.dumps({"schema": "monitoring.pricing.v1", "rates": [{
                "provider": "example", "model": "example", "currency": "USD",
                "from": "2026-11-03T00:00:00Z", "until": "2026-11-04T00:00:00Z",
                "provenance": "fixture-rate", "input": "1000000", "cache_read": "0",
                "cache_write": "0", "output": "0"}]}))
            store = Store(root / "events.sqlite")
            store.ingest([usage("one", "2026-11-03T10:00:00Z", project="one"),
                          usage("two", "2026-11-03T11:00:00Z", project="two")])
            owner = Observatory(store, Pricing(price), Exchange(), cost_thresholds={"estimated": "150"})
            with patch("monitoring_ai.metrics.now_iso", return_value=NOW):
                value = owner.read({"project": "one"})
            self.assertEqual(value["summary"]["costs"]["estimated"]["amount"], "100")
            self.assertEqual(value["cost_thresholds"]["items"][0]["observed_amount"], "200")
            self.assertEqual(value["cost_thresholds"]["items"][0]["state"], "observed-reached")

    def test_invalid_cli_limit_refuses_before_creating_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "would-create.sqlite"
            result = subprocess.run([sys.executable, "-m", "monitoring_ai", "top", "--db", str(path),
                                     "--estimated-cost-threshold", "nan"], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
