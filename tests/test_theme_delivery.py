import http.client
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

from monitoring_ai.metrics import Exchange, Pricing
from monitoring_ai.server import Observatory, handler
from monitoring_ai.store import Store

WEB = Path(__file__).parents[1] / "monitoring_ai" / "web"


class ThemeDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        store = Store(Path(self.temp.name) / "events.sqlite")
        self.owner = Observatory(store, Pricing(), Exchange())
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler(self.owner))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def get(self, path, *, host=None, origin=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        headers = {}
        if host is not None:
            headers["Host"] = host
        if origin is not None:
            headers["Origin"] = origin
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        body = response.read()
        value = (response.status, response.getheader("Content-Type"),
                 response.getheader("Content-Security-Policy"), body)
        connection.close()
        return value

    def test_whitelisted_theme_assets_are_delivered_exactly(self):
        for route, asset, mime in (
            ("/appearance.js", "appearance.js", "text/javascript; charset=utf-8"),
            ("/navigation.js", "navigation.js", "text/javascript; charset=utf-8"),
            ("/navigation.css", "navigation.css", "text/css; charset=utf-8"),
            ("/theme/index.js", "theme/index.js", "text/javascript; charset=utf-8"),
            ("/theme/workspace-navigation.css", "theme/workspace-navigation.css", "text/css; charset=utf-8"),
            ("/theme/LICENSE", "theme/LICENSE", "text/plain; charset=utf-8"),
        ):
            with self.subTest(route=route):
                status, content_type, csp, body = self.get(route)
                self.assertEqual(status, 200)
                self.assertEqual(content_type, mime)
                self.assertEqual(body, (WEB / asset).read_bytes())
                self.assertIn("script-src 'self'", csp)
                self.assertIn("connect-src 'self'", csp)
                self.assertIn("frame-ancestors 'none'", csp)

    def test_arbitrary_and_traversal_paths_are_not_served(self):
        for route in ("/theme/../server.py", "/theme/%2e%2e/server.py",
                      "/%2e%2e/monitoring_ai/server.py", "/theme/other.js",
                      "/theme/index.js/extra", "/theme/workspace-navigation.css/extra",
                      "/navigation.css.bak"):
            with self.subTest(route=route):
                status, _, _, _ = self.get(route)
                self.assertEqual(status, 404)

    def test_existing_host_and_origin_safeguards_cover_assets(self):
        status, _, csp, _ = self.get("/theme/index.js", host="attacker.invalid")
        self.assertEqual(status, 403)
        self.assertIn("default-src 'self'", csp)
        status, _, _, _ = self.get("/appearance.js", origin="https://attacker.invalid")
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
