"""Serve a read-only loopback API and the bundled dashboard."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import signal
import threading
from urllib.parse import parse_qs, urlsplit
import uuid

from . import __version__
from .metrics import filters, now_iso, selected, snapshot
from .insights import validate_quota_book
from .reporting import detailed_report

WEB = Path(__file__).with_name("web")


class Observatory:
    def __init__(self, store, pricing, fx, loader=None, quota_loader=None, quota_book=None):
        self.store, self.pricing, self.fx, self.loader = store, pricing, fx, loader
        self.lock = threading.Lock()
        self.identity = str(uuid.uuid4())
        self.source_status = {"state": "static", "last_success_at": None, "error": None}
        self.quota_loader = quota_loader
        self.quota_book = validate_quota_book(quota_book) if quota_book else None
        self.quota_status = dict(state="static" if quota_book else "unconfigured", last_success_at=None, error=None)

    def refresh_quotas(self):
        if self.quota_loader is None:
            return
        with self.lock:
            self.quota_status["state"] = "reading"
        try:
            book = validate_quota_book(self.quota_loader())
            with self.lock:
                self.quota_book = book
                self.quota_status = dict(state="following", last_success_at=now_iso(), error=None)
        except (ValueError, OSError):
            with self.lock:
                self.quota_status = dict(state="unavailable", last_success_at=self.quota_status["last_success_at"],
                                         error="Quota source unavailable. Last supplied observations are retained.")

    def refresh(self):
        if self.loader is None:
            return
        with self.lock:
            try:
                result = self.store.ingest(self.loader())
                self.source_status = dict(state="following", last_success_at=now_iso(), error=None, **result)
            except (ValueError, OSError, sqlite3.Error):
                # Diagnostics never echo a native source path or a malformed record
                self.source_status = dict(
                    state="unavailable", last_success_at=self.source_status["last_success_at"],
                    error="Source read or validation failed. Last accepted events are retained.",
                )

    def read(self, query):
        with self.lock:
            revision, events = self.store.read()
            value = snapshot(revision, events, query, self.pricing, self.fx, quota_book=self.quota_book)
            value["insights"]["quotas"]["read_status"] = dict(self.quota_status)
            value.update(identity=self.identity, source_status=dict(self.source_status))
            return value

    def report(self, query):
        with self.lock:
            revision, events = self.store.read()
            value = detailed_report(revision, events, query, self.pricing, self.fx)
            value.update(identity=self.identity, source_status=dict(self.source_status))
            return value


def handler(observatory):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, data, content_type="application/json"):
            content = json.dumps(data, allow_nan=False).encode() if content_type == "application/json" else data
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            host = self.headers.get("Host", "")
            port = self.server.server_port
            if host not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
                self.send(403, {"error": "Loopback Host required"})
                return
            origin = self.headers.get("Origin")
            if origin and origin not in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}:
                self.send(403, {"error": "Cross-origin browser reads are refused"})
                return
            parsed = urlsplit(self.path)
            try:
                parameters = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=20)
                if any(len(values) != 1 for values in parameters.values()):
                    raise ValueError("Repeated query parameters are not supported")
                query = {key: values[0] for key, values in parameters.items()}
                if parsed.path == "/api/v1/capabilities":
                    if query:
                        raise ValueError("Capabilities takes no query")
                    self.send(200, dict(
                        schema="monitoring.capabilities.v1", version=__version__,
                        identity=observatory.identity, read_only=True,
                        snapshot="/api/v1/snapshot", events="/api/v1/events",
                        report="/api/v1/report", event_features=["tool-categories.v1"],
                        insights="monitoring.insights.v1", quota_observations="monitoring.quotas.v1",
                        filters=["provider", "model", "surface", "project", "source", "session", "since", "until", "state"],
                        limits={"read_events": 100000, "snapshot_entities": 500, "event_page": 500},
                    ))
                elif parsed.path == "/api/v1/snapshot":
                    self.send(200, observatory.read(query))
                elif parsed.path == "/api/v1/report":
                    self.send(200, observatory.report(query))
                elif parsed.path == "/api/v1/events":
                    limit = int(query.pop("limit", "100"))
                    after = int(query.pop("after", "0"))
                    expected = query.pop("expected_revision", None)
                    if not 1 <= limit <= 500 or after < 0 or "state" in query:
                        raise ValueError("Use limit 1–500 and a nonnegative offset. State is snapshot-only")
                    query = filters(query)
                    with observatory.lock:
                        revision, events = observatory.store.read()
                        if expected is not None and int(expected) != revision:
                            self.send(409, {"error": "Ledger changed. Restart pagination", "revision": revision})
                            return
                        matched = [e for e in events if selected(e, query)]
                        page = matched[after:after + limit]
                        self.send(200, dict(schema="monitoring.events.v1", identity=observatory.identity,
                            revision=revision, events=page, total=len(matched),
                            next_offset=after + limit if after + limit < len(matched) else None))
                elif parsed.path == "/health":
                    self.send(200, dict(schema="monitoring.health.v1", status="ok", source_status=observatory.source_status,
                                        quota_status=observatory.quota_status))
                else:
                    files = {"/": ("index.html", "text/html; charset=utf-8"),
                             "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                             "/style.css": ("style.css", "text/css; charset=utf-8")}
                    if parsed.path not in files:
                        self.send(404, {"error": "Not found"})
                        return
                    name, mime = files[parsed.path]
                    self.send(200, (WEB / name).read_bytes(), mime)
            except (ValueError, TypeError):
                self.send(400, {"error": "Invalid query or data exceeds documented limits"})
            except (OSError, sqlite3.Error):
                self.send(503, {"error": "Ledger unavailable"})

    return Handler


def serve(observatory, port):
    server = ThreadingHTTPServer(("127.0.0.1", port), handler(observatory))
    server.daemon_threads = True
    stop = threading.Event()
    previous_term = None
    if threading.current_thread() is threading.main_thread():
        def interrupt(signum, frame):
            raise KeyboardInterrupt
        previous_term = signal.signal(signal.SIGTERM, interrupt)

    def follow():
        while not stop.wait(2):
            observatory.refresh()

    def quotas():
        while not stop.is_set():
            observatory.refresh_quotas()
            if stop.wait(60):
                break

    observatory.refresh()
    worker = threading.Thread(target=follow, daemon=True)
    if observatory.loader:
        worker.start()
    quota_worker = None
    if observatory.quota_loader:
        quota_worker = threading.Thread(target=quotas)
        quota_worker.start()
    print(f"Monitoring AI → http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        if quota_worker:
            quota_worker.join(timeout=15)
        if previous_term is not None:
            signal.signal(signal.SIGTERM, previous_term)
