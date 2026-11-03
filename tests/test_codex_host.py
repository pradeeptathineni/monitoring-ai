import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from monitoring_ai.app_threads import read_app_threads
from monitoring_ai.codex_host import capture_host
from monitoring_ai.metrics import Exchange, Pricing
from monitoring_ai.server import Observatory
from monitoring_ai.store import Store


PROXY = r'''import json, sys
first = json.loads(sys.stdin.readline())
assert first["method"] == "initialize"
version = "0.159.0" if sys.argv[2] == "old-version" else "0.160.0"
print(json.dumps({"id": 1, "result": {"userAgent": "codex_cli_rs/" + version + " (fixture)"}}), flush=True)
assert json.loads(sys.stdin.readline()) == {"method": "initialized"}
request = json.loads(sys.stdin.readline())
assert request["method"] == "thread/list"
assert request["params"]["useStateDbOnly"] is True
assert request["params"]["limit"] == 20
assert request["params"]["cwd"] == sys.argv[1]
if sys.argv[2] == "error":
    print(json.dumps({"id": 2, "error": {"message": "private error"}}), flush=True)
else:
    rows = [{"id": "private-id-" + state, "status": {"type": state}, "updatedAt": 1793732400,
             "preview": "private prompt", "cwd": "/private/project" if sys.argv[2] == "wrong-scope" else sys.argv[1], "name": "private title"}
            for state in ["active", "idle", "notLoaded"]]
    print(json.dumps({"id": 2, "result": {"data": rows, "nextCursor": "private cursor"}}), flush=True)
'''


class CodexHostTests(unittest.TestCase):
    def test_fixed_database_only_projection_and_failure_preserves_capture(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); root.chmod(0o700)
            path = root / "host.sock"; output = root / "capture.json"
            with socket.socket(socket.AF_UNIX) as host:
                host.bind(str(path)); path.chmod(0o600)
                original_popen = subprocess.Popen
                def proxy(command, **kwargs):
                    self.assertEqual(command, ["codex", "app-server", "proxy", "--sock", str(path)])
                    return original_popen([sys.executable, "-u", "-c", PROXY, str(root.resolve()), mode], **kwargs)
                mode = "ok"
                with patch("monitoring_ai.codex_host.subprocess.Popen", side_effect=proxy):
                    capture_host(path, root, output)
                book = read_app_threads(output)
                self.assertEqual(book["source"], "codex-local-app-server")
                self.assertEqual(book["scope"], "selected-cwd-page")
                self.assertEqual(sorted(r["status"] for r in book["threads"]), ["active", "idle", "unknown"])
                self.assertFalse(book["complete_coverage"])
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
                for secret in ("private-id", "private prompt", "private title", "/private/project", "private cursor"):
                    self.assertNotIn(secret, output.read_text())
                owner = Observatory(Store(root / "ledger.sqlite"), Pricing(), Exchange(),
                                    app_threads_loader=lambda: read_app_threads(output))
                observed = owner.read({})
                self.assertEqual(observed["revision"], 0)
                self.assertEqual(observed["summary"]["active"], 0)
                before = output.read_bytes()
                for mode in ("error", "old-version", "wrong-scope"):
                    with patch("monitoring_ai.codex_host.subprocess.Popen", side_effect=proxy):
                        with self.assertRaises(ValueError) as failure:
                            capture_host(path, root, output, replace=True)
                    self.assertNotIn("private error", str(failure.exception))
                    self.assertEqual(output.read_bytes(), before)

    def test_missing_or_unsafe_socket_never_starts_proxy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path = root / "not-a-socket"
            with patch("monitoring_ai.codex_host.subprocess.Popen") as start:
                with self.assertRaises(OSError): capture_host(path, root, root / "output")
                path.write_text("not a socket")
                with self.assertRaises(ValueError): capture_host(path, root, root / "output")
                start.assert_not_called()
