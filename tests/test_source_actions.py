import unittest

from monitoring_ai.source_actions import source_actions, ERROR_MESSAGE

CLAUDE = ("Declared Claude fork history requires separate accounting qualification. "
          "This input was not imported; accepted history is retained.")
CODEX_META = ("Declared Codex history metadata requires separate accounting qualification. "
              "This input was not imported; accepted history is retained.")
CODEX_PARENT = ("Declared Codex parent history requires replay reconciliation. "
                "This input was not imported; accepted history is retained.")
OVERLAP = "Overlapping native records in another selected source. Accepted history is retained."


def c(alias, state="readable", error=None, fmt="claude", summary=None):
    return {"id": alias, "format": fmt, "state": state, "error": error, "summary": summary}


def doc(*checks):
    return {"schema": "monitoring.source-check.v1", "checks": list(checks),
            "all_readable": all(x["state"] == "readable" for x in checks)}


class ActionableJourney(unittest.TestCase):
    def test_mixed_sources_get_specific_actions(self):
        out = source_actions(doc(
            c("a", "invalid", CLAUDE),
            c("b", "invalid", CODEX_META, "codex"),
            c("d", "invalid", CODEX_PARENT, "codex"),
            c("e", "invalid", OVERLAP, "normalized"),
            c("f", "unavailable", None, "otlp-jsonl"),
            c("g"),
        ))
        self.assertEqual(out["schema"], "monitoring.source-actions.v1")
        self.assertEqual(out["scope"], "explicit-selected-inputs")
        self.assertIs(out["complete_coverage"], False)
        self.assertIs(out["ready_for_preview"], False)
        self.assertEqual(set(out), {"schema", "scope", "complete_coverage", "items",
                                    "ready_for_preview"})
        by = {i["id"]: i for i in out["items"]}
        for k in "abde":
            self.assertIn("Retain the existing ledger", by[k]["action"])
        self.assertIn("independent supported source", by["a"]["action"])
        self.assertIn("independent supported source", by["b"]["action"])
        self.assertIn("Complete the selected Codex family export", by["d"]["action"])
        self.assertIn("qualified legacy prefixes", by["d"]["action"])
        self.assertIn("Review the selection", by["e"]["action"])
        self.assertNotIn("alias", by["e"]["action"].lower())
        self.assertIn("file selected in this manifest", by["f"]["action"])
        self.assertIn("exists and is readable", by["f"]["action"])
        for i in out["items"]:
            self.assertNotIn("remove", i["action"].lower())
            self.assertEqual(set(i), {"id", "format", "state", "code", "action"})

    def test_all_readable_is_ready_with_preview_advice(self):
        out = source_actions(doc(c("a"), c("b", fmt="codex")))
        self.assertIs(out["ready_for_preview"], True)
        self.assertIs(out["complete_coverage"], False)
        for i in out["items"]:
            self.assertIn("Preview the existing sources", i["action"])
            self.assertIn("deliberate owner restart", i["action"])
            low = i["action"].lower()
            for bad in ("ledger accept", "live worker", "billing"):
                self.assertNotIn(bad, low)


class PrivacyJourney(unittest.TestCase):
    def test_unknown_errors_and_summaries_never_echoed(self):
        secret = "PermissionError: /Users/someone/secret/token.jsonl sk-123"
        out = source_actions(doc(
            c("a", "invalid", secret, summary={"path": "/Users/x/private"}),
            c("b", "invalid", CLAUDE + " extra", "codex"),
            c("d", "invalid", None),
            c("e", "unavailable", secret),
        ))
        text = repr(out)
        for leak in ("PermissionError", "/Users", "sk-123", "private", "extra", "token"):
            self.assertNotIn(leak, text)
        by = {i["id"]: i for i in out["items"]}
        for k in "abd":
            self.assertEqual(by[k]["code"], "unrecognized_error")
            self.assertIn("documented format", by[k]["action"])
        self.assertEqual(by["e"]["code"], "unavailable")

    def test_no_command_generation(self):
        out = source_actions(doc(c("a", "invalid", OVERLAP), c("b", "unavailable")))
        for i in out["items"]:
            for tok in ("$", "`", "sudo", "rm ", "--"):
                self.assertNotIn(tok, i["action"])


class StrictJourney(unittest.TestCase):
    def assertRefused(self, value):
        with self.assertRaises(ValueError) as cm:
            source_actions(value)
        self.assertEqual(str(cm.exception), ERROR_MESSAGE)

    def test_malformed_inputs_refused_with_constant_error(self):
        good = doc(c("a"))
        self.assertRefused(None)
        self.assertRefused([])
        self.assertRefused({**good, "extra": 1})
        self.assertRefused({**good, "schema": "other"})
        self.assertRefused({**good, "checks": []})
        self.assertRefused({**good, "checks": "x"})
        self.assertRefused({**good, "all_readable": 1})
        self.assertRefused({**good, "all_readable": False})  # inconsistent
        for bad in ("", "a b", "a/b", "a\n", "é", "x" * 161, 5, None):
            self.assertRefused(doc(c(bad)))
        self.assertRefused(doc(c("a", fmt="yaml")))
        self.assertRefused(doc(c("a", fmt=None)))
        self.assertRefused(doc(c("a", state="ok")))
        self.assertRefused(doc(c("a", "invalid", 5)))
        self.assertRefused(doc(c("a", summary="text")))
        self.assertRefused(doc(c("a", "readable", "boom")))
        extra = c("a")
        extra["path"] = "/tmp/x"
        self.assertRefused(doc(extra))
        missing = c("a")
        del missing["summary"]
        self.assertRefused(doc(missing))

    def test_duplicates_and_limits(self):
        self.assertRefused(doc(c("a"), c("a", fmt="codex")))
        ok = source_actions(doc(*[c("s%d" % n) for n in range(32)]))
        self.assertEqual(len(ok["items"]), 32)
        self.assertRefused(doc(*[c("s%d" % n) for n in range(33)]))
        valid = "Aa0_.:@-" * 20
        self.assertEqual(source_actions(doc(c(valid)))["items"][0]["id"], valid)


if __name__ == "__main__":
    unittest.main()
