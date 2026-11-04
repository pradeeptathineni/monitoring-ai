import copy
import unittest

from monitoring_ai.receipt_evidence import inspect_receipt

SHA = "a" * 64


def make_receipt():
    return {
        "schema": "monitoring.codex-replay-export.v1",
        "observed_at": "2026-11-02T10:00:00+00:00",
        "events": 5,
        "bytes": 1234,
        "sha256": SHA,
        "sources": [
            {"source": "work:a@1", "copied_usage_records": 7,
             "exported_usage_records": 5, "ignored_response_records": 2},
            {"source": "empty.src", "copied_usage_records": 1,
             "exported_usage_records": 0, "ignored_response_records": 0},
        ],
        "complete_coverage": False,
        "coverage": "selected-proven-cumulative-prefixes",
        "costs": "unknown",
        "lifecycle": "not-exported",
        "compaction": "unsupported",
    }


class ValidMultiSourceTest(unittest.TestCase):
    def test_match_with_zero_export_alias_absent_or_present(self):
        for counts in ({"work:a@1": 5}, {"work:a@1": 5, "empty.src": 0}):
            r = make_receipt()
            before = copy.deepcopy(r)
            out = inspect_receipt(r, SHA, 1234, counts)
            self.assertEqual(r, before)
            self.assertEqual(out["consistency"], "matching")
            self.assertEqual(out["exported_records"], 5)
            self.assertEqual(out["copied_records"], 8)
            self.assertIs(out["complete_coverage"], False)
            self.assertEqual(out["producer_authenticity"], "unverified")
            self.assertEqual(out["schema"], "monitoring.receipt-evidence.v1")


class MismatchTest(unittest.TestCase):
    def check(self, sha=SHA, size=1234, counts=None):
        counts = {"work:a@1": 5} if counts is None else counts
        out = inspect_receipt(make_receipt(), sha, size, counts)
        self.assertEqual(out["consistency"], "mismatch")
        self.assertIsNone(out["copied_records"])
        self.assertIs(out["complete_coverage"], False)

    def test_digest(self):
        self.check(sha="b" * 64)

    def test_bytes(self):
        self.check(size=1235)

    def test_counts(self):
        self.check(counts={"work:a@1": 4})
        self.check(counts={})  # absent alias with nonzero export
        self.check(counts={"work:a@1": 5, "extra": 0})  # alias not in receipt
        self.check(counts={"work:a@1": 5, "empty.src": 1})


class StrictMalformedPrivacyTest(unittest.TestCase):
    def assert_rejected(self, receipt=None, sha=SHA, size=1234, counts=None):
        receipt = make_receipt() if receipt is None else receipt
        counts = {"work:a@1": 5} if counts is None else counts
        with self.assertRaises(ValueError) as cm:
            inspect_receipt(receipt, sha, size, counts)
        self.assertEqual(str(cm.exception), "Invalid receipt evidence input")

    def test_receipt_rejections(self):
        def mut(f):
            r = make_receipt()
            f(r)
            return r
        cases = [
            lambda r: r.update(extra=1),
            lambda r: r.pop("costs"),
            lambda r: r.update(complete_coverage=True),
            lambda r: r.update(events=6),
            lambda r: r.update(events=True),
            lambda r: r.update(bytes=33554433),
            lambda r: r.update(bytes=-1),
            lambda r: r.update(sha256="A" * 64),
            lambda r: r.update(sha256="a" * 63),
            lambda r: r.update(observed_at="2026-11-02T10:00:00"),
            lambda r: r.update(observed_at="2026-02-30T10:00:00+00:00"),
            lambda r: r.update(observed_at="2026-11-02"),
            lambda r: r.update(costs="known"),
            lambda r: r.update(sources=[]),
            lambda r: r["sources"].append(dict(r["sources"][0])),
            lambda r: r["sources"][0].update(source="bad alias"),
            lambda r: r["sources"][0].update(source="a" * 161),
            lambda r: r["sources"][0].update(exported_usage_records=True),
            lambda r: r["sources"][0].update(exported_usage_records=50001),
            lambda r: r["sources"][0].update(path="/x"),
        ]
        for f in cases:
            self.assert_rejected(receipt=mut(f))
        self.assert_rejected(receipt=[])

    def test_observed_rejections(self):
        self.assert_rejected(sha="x" * 64)
        self.assert_rejected(sha=None)
        self.assert_rejected(size=True)
        self.assert_rejected(size=33554433)
        self.assert_rejected(counts={"work:a@1": True})
        self.assert_rejected(counts={"bad/alias": 1})
        self.assert_rejected(counts={"work:a@1": -1})
        self.assert_rejected(counts=[("work:a@1", 5)])
        self.assert_rejected(counts={f"s{i}": 0 for i in range(33)})

    def test_error_and_output_do_not_echo_input(self):
        secret = "/Users/secret/token-sk-123"
        r = make_receipt()
        r["sources"][0]["source"] = secret
        with self.assertRaises(ValueError) as cm:
            inspect_receipt(r, SHA, 1234, {})
        self.assertNotIn("secret", str(cm.exception))
        r = make_receipt()
        r["unused"] = secret
        self.assert_rejected(receipt=r)
        out = inspect_receipt(make_receipt(), SHA, 1234, {"work:a@1": 5})
        text = repr(out)
        for forbidden in ("work:a@1", "empty.src", SHA, "/", "2026-11-02"):
            self.assertNotIn(forbidden, text.replace(
                "operator-supplied-receipt-and-selected-output", ""))
        self.assertEqual(len(out), 11)


if __name__ == "__main__":
    unittest.main()
