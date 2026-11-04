"""Check quota observation integrity separately from usage accounting."""

import hashlib
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest

from monitoring_ai.metrics import Exchange, Pricing
from monitoring_ai.quota_history import QuotaHistory, QuotaHistoryFull
from monitoring_ai.server import Observatory, handler
from monitoring_ai.store import Store


def book(at="2026-11-04T00:00:00Z", used=40, account="synthetic", reset="2026-11-05T00:00:00Z"):
    """Keep aliases synthetic and source time separate from recording time."""
    return dict(schema="monitoring.quotas.v1", snapshots=[dict(source="synthetic-quota", provider="example",
        account=account, captured_at=at, provenance="synthetic", windows=[dict(id="weekly", label="Weekly",
        window_minutes=10080, used_percent=used, resets_at=reset)])])


class QuotaHistoryTests(unittest.TestCase):
    """Exercise whole observation books, pinned reads and source failures."""

    def test_atomic_replay_conflict_capacity_and_private_read_only(self):
        """Refuse conflicts and capacity overflow without modifying prior evidence."""
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"history.sqlite";history=QuotaHistory(path,max_samples=2)
            self.assertEqual(history.record(book(),"first-owner"),1)
            self.assertEqual(history.record(book(),"other-owner"),0)
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaises(ValueError):history.record(book(used=41),"first-owner")
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),digest)
            self.assertEqual(history.record(book("2026-11-04T00:01:00Z",41),"first-owner"),1)
            with self.assertRaises(QuotaHistoryFull):history.record(book("2026-11-04T00:02:00Z",42),"first-owner")
            self.assertEqual(history.summary()["observations"],2)
            readonly=QuotaHistory(path,read_only=True)
            self.assertEqual(readonly.view()["points"][0]["used_percent"],40)
            with self.assertRaises(ValueError):readonly.record(book(),"owner")
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            missing=Path(folder)/"missing.sqlite"
            with self.assertRaises(ValueError):QuotaHistory(missing,read_only=True)
            self.assertFalse(missing.exists())
            path.chmod(0o644)
            with self.assertRaises(ValueError):QuotaHistory(path,read_only=True)

    def test_exact_scopes_paging_gaps_decreases_and_future_times(self):
        """Retain discontinuities without inferring resets or filling unknown values."""
        with tempfile.TemporaryDirectory() as folder:
            history=QuotaHistory(Path(folder)/"history.sqlite")
            history.record(book(),"owner")
            history.record(book("2026-11-04T00:01:00Z",41),"owner")
            history.mark_gap()
            history.record(book("2026-11-04T00:02:00Z",42),"owner")
            history.record(book("2026-11-04T00:03:00Z",5,reset="2026-11-06T00:00:00Z"),"next-owner")
            history.record(book("2026-11-04T00:08:00Z",None,reset="2026-11-06T00:00:00Z"),"next-owner")
            first=history.view(limit=2,now="2026-11-04T00:07:00Z")
            self.assertFalse(first["complete_coverage"])
            self.assertEqual(first["points"][-1]["break_reasons"],["observation-gap","percentage-unavailable","future-source-time"])
            self.assertIsNone(first["points"][-1]["remaining_percent"])
            older=history.view(first["selected"],limit=2,before=first["next_before"],expected_revision=first["revision"])
            self.assertEqual([p["used_percent"]for p in older["points"]],[41,42])
            self.assertIn("collection-gap",older["points"][-1]["break_reasons"])
            full=history.view()
            self.assertEqual(full["points"][3]["break_reasons"],["collector-changed","reset-time-changed","reported-usage-decreased"])
            history.record(book(account="other-alias"),"owner")
            self.assertEqual(history.summary()["series_count"],2)
            with self.assertRaises(ValueError):history.view(expected_revision=first["revision"])
            self.assertEqual(history.view(full["selected"])["points"][0]["used_percent"],40)

    def test_source_and_recorder_health_are_independent(self):
        """Retain accepted current quota when history recording reaches capacity."""
        with tempfile.TemporaryDirectory() as folder:
            history=QuotaHistory(Path(folder)/"history.sqlite",max_samples=1)
            values=[book(),book("2026-11-04T00:01:00Z",41),None]
            def load():
                value=values.pop(0)
                if value is None:raise OSError("private source path")
                return value
            owner=Observatory(Store(Path(folder)/"events.sqlite"),Pricing(),Exchange(),quota_loader=load,quota_history=history)
            owner.refresh_quotas();owner.refresh_quotas()
            self.assertEqual(owner.quota_status["state"],"following")
            self.assertEqual(owner.quota_history_status["state"],"capacity")
            page=owner.read_quota_history()
            self.assertEqual(page["current_window"]["used_percent"],41)
            self.assertEqual(page["points"][0]["used_percent"],40)
            owner.refresh_quotas()
            self.assertEqual(owner.read_quota_history()["source_read_state"],"unavailable")
            self.assertEqual(owner.read_quota_history()["recording_status"]["state"],"waiting-source")
            self.assertEqual(owner.store.read()[0],0)

    def test_http_paging_scope_and_recorded_only_owner(self):
        """Pin owner and history revision independently of the usage ledger."""
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"history.sqlite";history=QuotaHistory(path)
            history.record(book(),"collector")
            history.record(book("2026-11-04T00:01:00Z",41),"collector")
            owner=Observatory(Store(Path(folder)/"events.sqlite"),Pricing(),Exchange(),quota_history=QuotaHistory(path,read_only=True))
            server=ThreadingHTTPServer(("127.0.0.1",0),handler(owner));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            def get(path):
                client=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=3);client.request("GET",path)
                response=client.getresponse();value=response.status,json.loads(response.read());client.close();return value
            try:
                self.assertEqual(get("/api/v1/capabilities")[1]["quota_history"],"/api/v1/quota-history")
                status,page=get("/api/v1/quota-history?limit=1");self.assertEqual(status,200)
                self.assertIsNone(page["current_window"])
                self.assertEqual(page["recording_status"]["state"],"recorded-only")
                suffix=f"series={page['selected']}&before={page['next_before']}"
                self.assertEqual(get("/api/v1/quota-history?"+suffix)[0],400)
                suffix+=f"&expected_identity={owner.identity}&expected_revision={page['revision']}"
                self.assertEqual(get("/api/v1/quota-history?"+suffix)[0],200)
                self.assertEqual(get("/api/v1/quota-history?expected_identity=wrong")[0],409)
                self.assertEqual(get("/api/v1/quota-history?provider=example")[0],400)
                history.record(book("2026-11-04T00:02:00Z",42),"collector")
                self.assertEqual(get("/api/v1/quota-history?"+suffix)[0],409)
                self.assertEqual(get("/api/v1/snapshot")[1]["quota_history_status"]["observations"],3)
            finally:server.shutdown();server.server_close();thread.join()


if __name__=="__main__":unittest.main()
