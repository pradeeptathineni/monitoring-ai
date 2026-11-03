"""Keep optional recent-activity snapshots separate from accepted event history."""

import tempfile
import unittest
from pathlib import Path

from monitoring_ai.metrics import Exchange, Pricing
from monitoring_ai.server import Observatory
from monitoring_ai.store import Store


class CodeBurnActivityObservationTests(unittest.TestCase):
    def test_replacement_empty_and_failure_keep_event_revision_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            source = {"schema": "monitoring.codeburn-activity.v1", "sessions": [{"alias": "s-one"}]}

            def read():
                if source is None:
                    raise ValueError("private source path or content")
                return source

            owner = Observatory(Store(Path(temp) / "events.sqlite"), Pricing(), Exchange(),
                                codeburn_activity_loader=read)
            owner.refresh_codeburn_activity()
            first = owner.read({})
            self.assertEqual(first["codeburn_activity"]["sessions"], [{"alias": "s-one"}])
            revision = first["revision"]

            source = {"schema": "monitoring.codeburn-activity.v1", "sessions": []}
            owner.refresh_codeburn_activity()
            current = owner.read({})
            self.assertEqual(current["codeburn_activity"]["sessions"], [])
            self.assertEqual(current["revision"], revision)
            self.assertEqual(first["codeburn_activity"]["sessions"], [{"alias": "s-one"}])

            source = None
            owner.refresh_codeburn_activity()
            retained = owner.read({})
            self.assertEqual(retained["codeburn_activity_status"]["state"], "unavailable")
            self.assertTrue(retained["codeburn_activity"]["retained"])
            self.assertEqual(retained["codeburn_activity"]["sessions"], [])
            self.assertNotIn("private source", str(retained))
            self.assertEqual(retained["revision"], revision)

    def test_unconfigured_owner_omits_optional_activity(self):
        with tempfile.TemporaryDirectory() as temp:
            owner = Observatory(Store(Path(temp) / "events.sqlite"), Pricing(), Exchange())
            self.assertNotIn("codeburn_activity", owner.read({}))
            self.assertNotIn("codeburn_activity_status", owner.report({}))


if __name__ == "__main__":
    unittest.main()
