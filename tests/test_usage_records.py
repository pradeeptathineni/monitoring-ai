import hashlib
import http.client
from decimal import Decimal
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from monitoring_ai.metrics import Exchange, Pricing
from monitoring_ai.server import Observatory, handler
from monitoring_ai.store import Store
from monitoring_ai.usage_records import usage_page


def event(ident, model="priced", cost=None):
    row = dict(schema="monitoring.event.v1", source="synthetic", id=ident, session="fixture-session", entity="fixture-session",
               parent=None, entity_type="session", kind="usage", timestamp="2026-11-03T10:00:00.000Z",
               provider="example", model=model, surface="test", project="fixture-project",
               tokens=dict(input=1, cache_read=2, cache_write=3, output=4))
    if cost is not None:row["cost"] = cost
    return row


def cost(amount="0", currency="USD", basis="billed"):
    return dict(amount=amount, currency=currency, basis=basis, provenance="synthetic-cost")


def book(root):
    path = root / "prices.json"
    path.write_text(json.dumps(dict(schema="monitoring.pricing.v1", rates=[dict(provider="example", model="priced", currency="USD",
        **{"from":"2026-11-03T00:00:00Z", "until":"2026-11-04T00:00:00Z"}, provenance="synthetic-price",
        input="1", cache_read="2", cache_write="3", output="4")])))
    return Pricing(path)


class UsageRecordsTests(unittest.TestCase):
    def test_cost_sources_keep_zero_unknown_missing_conversion_and_decimal_formula_distinct(self):
        with tempfile.TemporaryDirectory() as folder:
            pricing = book(Path(folder))
            rows = [event("estimate"), event("billed-zero", cost=cost()), event("no-price", model="missing"),
                    event("missing-conversion", cost=cost("2", "EUR", "estimated"))]
            page = usage_page(4, rows, {}, pricing, Exchange())
            self.assertEqual(page["cost_coverage"], dict(estimated=1,billed=1,unpriced=1,unconverted=1))
            by_id = {row["event"]["id"]:row for row in page["records"]}
            billed = by_id["billed-zero"]
            self.assertEqual(billed["pricing_method"], "recorded")
            self.assertEqual(billed["display_cost"]["amount"], "0")
            self.assertIsNone(billed["rate"])
            self.assertIsNone(by_id["no-price"]["native_cost"])
            self.assertIsNone(by_id["no-price"]["display_cost"])
            self.assertEqual(by_id["missing-conversion"]["native_cost"]["currency"], "EUR")
            self.assertIsNone(by_id["missing-conversion"]["display_cost"])
            estimate = by_id["estimate"]
            self.assertEqual(Decimal(estimate["display_cost"]["amount"]), Decimal("0.00003"))
            self.assertEqual(estimate["bucket_costs"]["cache_write"], "0.000009")
            self.assertEqual(estimate["rate"]["provenance"], "synthetic-price")
            self.assertEqual(usage_page(4, rows, {}, pricing, Exchange(), cost_state="unpriced")["total"], 1)

    def test_fx_and_dated_price_boundaries_keep_original_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); pricing = book(root); fx_path = root / "fx.json"
            fx_path.write_text(json.dumps(dict(schema="monitoring.fx.v1",base="USD",as_of="2026-11-03T00:00:00Z",
                                               provenance="synthetic-fx",rates=dict(USD="1",EUR="2"))))
            row = event("foreign", cost=cost("2", "EUR", "estimated"))
            original = json.dumps(row, sort_keys=True)
            converted = usage_page(1, [row], {}, pricing, Exchange("USD", fx_path))["records"][0]
            self.assertEqual(converted["display_cost"]["amount"], "1")
            self.assertEqual(converted["native_cost"]["amount"], "2")
            self.assertEqual(json.dumps(row, sort_keys=True), original)
            boundary = dict(event("boundary"), timestamp="2026-11-04T00:00:00.000Z")
            self.assertEqual(usage_page(1, [boundary], {}, pricing, Exchange())["records"][0]["cost_state"], "unpriced")
            future = dict(event("future"), timestamp="2026-11-03T10:00:00.001Z")
            self.assertEqual(usage_page(1,[future],{},pricing,Exchange(),now="2026-11-03T10:00:00Z")["future_dated_records"],1)
            with self.assertRaises(ValueError):usage_page(1, [row], {"state":"running"}, pricing, Exchange())

    def test_read_only_cli_pages_beyond_snapshot_window_without_changing_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path = root / "ledger.sqlite"
            Store(path).ingest([event(f"record-{i:03}",model="missing")for i in range(125)])
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            command = [sys.executable,"-m","monitoring_ai","usage-records","--db",str(path),"--json","--cost-state","unpriced","--limit","50"]
            first = json.loads(subprocess.run(command,capture_output=True,text=True,check=True).stdout)
            later = json.loads(subprocess.run(command+["--after","100","--expected-revision",str(first["revision"])],capture_output=True,text=True,check=True).stdout)
            self.assertEqual(first["total"],125)
            self.assertEqual(len(later["records"]),25)
            self.assertIsNone(later["next_offset"])
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)
            missing = root / "missing.sqlite"
            self.assertNotEqual(subprocess.run(command[:5]+[str(missing),"--json"],capture_output=True).returncode,0)
            self.assertFalse(missing.exists())

    def test_http_pages_require_owner_and_revision_and_reject_changed_or_unsupported_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder)/"ledger.sqlite");store.ingest([event("one"),event("two")])
            owner = Observatory(store,Pricing(),Exchange())
            server = ThreadingHTTPServer(("127.0.0.1",0),handler(owner));thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            def get(path):
                client=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=3);client.request("GET",path)
                response=client.getresponse();result=response.status,json.loads(response.read());client.close();return result
            try:
                self.assertEqual(get("/api/v1/capabilities")[1]["usage_records"],"/api/v1/usage-records")
                status,first=get("/api/v1/usage-records?limit=1");self.assertEqual(status,200)
                self.assertEqual(get("/api/v1/usage-records?after=1&expected_revision=2")[0],400)
                suffix=f"after=1&expected_revision=2&expected_identity={owner.identity}"
                self.assertEqual(get("/api/v1/usage-records?limit=1&"+suffix)[0],200)
                self.assertEqual(get("/api/v1/usage-records?state=running")[0],400)
                self.assertEqual(get("/api/v1/usage-records?cost_state=wrong")[0],400)
                store.ingest([event("three")])
                self.assertEqual(get("/api/v1/usage-records?limit=1&"+suffix)[0],409)
                self.assertEqual(get("/api/v1/usage-records?expected_identity=wrong")[0],409)
            finally:server.shutdown();server.server_close();thread.join()


if __name__=="__main__":unittest.main()
