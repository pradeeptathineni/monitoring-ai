import unittest

from monitoring_ai.sessions import session_page


class SessionPageTests(unittest.TestCase):
    def test_lists_older_sessions_beyond_entity_bound_with_stable_scope(self):
        rows = [dict(source="fixture", session=f"session-{index:04}", entity="root", kind="usage",
                     timestamp=f"2026-11-03T{index // 60:02}:{index % 60:02}:00.000Z", project="project-a")
                for index in range(501)]
        rows.append(dict(rows[0], source="other", kind="status"))
        first = session_page(502, rows, {}, limit=100)
        self.assertEqual(first["total"], 502)
        self.assertEqual(first["next_offset"], 100)
        last = session_page(502, rows, {}, after=500)
        self.assertEqual(len(last["sessions"]), 2)
        self.assertIsNone(last["next_offset"])
        found = session_page(502, rows, {"source": "fixture"}, search="SESSION-0000")
        self.assertEqual(found["total"], 1)
        self.assertEqual(found["sessions"][0]["usage_records"], 1)
        self.assertEqual(session_page(502, rows, {"project": "absent"})["total"], 0)
        with self.assertRaises(ValueError):
            session_page(502, rows, {"state": "running"})
        with self.assertRaises(ValueError):
            session_page(502, rows, {}, search="/private/path")
