"""Qualify the explicit multi-file CLI through its actual loopback read contract."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import urlopen


def event(ident, tokens=10):
    return dict(schema="monitoring.event.v1", id=ident, source="producer-alias", session="session",
                entity="session", parent=None, entity_type="session", kind="usage",
                timestamp="2026-11-02T20:00:00Z", provider="example", model="fictional",
                surface="export", project="fictional-app", tokens={"input": tokens})


class MultiSourceJourney(unittest.TestCase):
    def test_partial_import_replay_recovery_and_startup_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            normalized, native = root / "private-export.jsonl", root / "private-native.jsonl"
            missing = root / "private-later.jsonl"
            normalized.write_text(json.dumps(event("first")) + "\n")
            native.write_text(json.dumps(dict(sessionId="fictional-session", timestamp="2026-11-02T20:01:00Z",
                message=dict(id="fictional-message", role="assistant", model="fictional-model",
                             usage={"input_tokens": 3, "output_tokens": 2}))) + "\n")
            config = dict(schema="monitoring.sources.v1", sources=[
                dict(id="export-input", format="normalized", path=str(normalized)),
                dict(id="native-input", format="claude", path=str(native), project="fictional-app"),
                dict(id="later-input", format="normalized", path=str(missing)),
            ])
            manifest = root / "private-config.json"
            manifest.write_text(json.dumps(config))
            process = subprocess.Popen([sys.executable, "-m", "monitoring_ai", "serve", "--sources", str(manifest),
                "--db", str(root / "ledger.sqlite"), "--port", "0"], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True)
            try:
                line = process.stdout.readline()
                self.assertIn("http://127.0.0.1:", line, "server did not start")
                base = line.strip().split()[-1]

                def read(route="snapshot"):
                    with urlopen(base + "/api/v1/" + route, timeout=2) as response:
                        return json.load(response)

                def wait_for(predicate):
                    end = time.monotonic() + 8
                    while time.monotonic() < end:
                        value = read()
                        if predicate(value):
                            return value
                        time.sleep(0.1)
                    self.fail("expected input transition was not observed")

                initial = read()
                self.assertEqual(initial["summary"]["total_tokens"], 15)
                self.assertEqual(initial["inputs"]["healthy_inputs"], 2)
                self.assertEqual(initial["inputs"]["unavailable_inputs"], 1)
                self.assertEqual(initial["source_status"]["state"], "unavailable")
                self.assertTrue(initial["source_status"]["degraded"])
                self.assertEqual(read("capabilities")["input_observations"], "monitoring.inputs.v1")
                self.assertEqual(read("report")["inputs"]["inputs"][0]["id"], "export-input")
                self.assertNotIn(directory, json.dumps(initial))

                # Recovery admits only the originally named files. Editing the manifest does not add inputs.
                manifest.write_text(json.dumps(dict(config, sources=[])))
                missing.write_text(json.dumps(dict(event("recovered"), source="later-producer")) + "\n")
                recovered = wait_for(lambda value: value["inputs"]["healthy_inputs"] == 3)
                self.assertEqual(recovered["summary"]["total_tokens"], 25)
                self.assertEqual(recovered["source_status"]["state"], "following")
                self.assertEqual(len(recovered["inputs"]["inputs"]), 3)

                # One conflicting input rolls back its whole attempt; another continues advancing.
                normalized.write_text(json.dumps(event("would-add")) + "\n" + json.dumps(event("first", 99)) + "\n")
                with missing.open("a") as stream:
                    stream.write(json.dumps(dict(event("next"), source="later-producer")) + "\n")
                partial = wait_for(lambda value: value["inputs"]["unavailable_inputs"] == 1
                                   and value["summary"]["total_tokens"] == 35)
                self.assertEqual(partial["inputs"]["healthy_inputs"], 2)
                page = read("events")
                self.assertNotIn("would-add", [row["id"] for row in page["events"]])
                self.assertNotIn(directory, json.dumps(partial))
                self.assertEqual(partial["revision"], 4)
            finally:
                process.terminate()
                process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
