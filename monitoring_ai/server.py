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
from .events import label as event_label
from .metrics import filters, now_iso, selected, snapshot
from .insights import validate_quota_book
from .reporting import detailed_report

WEB = Path(__file__).with_name("web")


class Observatory:
    def __init__(self, store, pricing, fx, loader=None, quota_loader=None, quota_book=None, source_observer=None,
                 cached_usage_loader=None, codeburn_activity_loader=None):
        if loader is not None and source_observer is not None:
            raise ValueError("Choose one input configuration")
        self.store, self.pricing, self.fx, self.loader = store, pricing, fx, loader
        self.source_observer = source_observer
        self.lock = threading.Lock()
        self.identity = str(uuid.uuid4())
        self.source_status = {"state": "static", "last_success_at": None, "error": None}
        self.quota_loader = quota_loader
        self.quota_book = validate_quota_book(quota_book) if quota_book else None
        self.quota_status = dict(state="static" if quota_book else "unconfigured", last_success_at=None, error=None)
        self.cached_usage_loader = cached_usage_loader
        self.cached_usage = None
        self.cached_usage_status = dict(state="unread", last_success_at=None, error=None)
        self.codeburn_activity_loader = codeburn_activity_loader
        self.codeburn_activity = None
        self.codeburn_activity_status = dict(state="unread", last_success_at=None, error=None)

    def refresh_codeburn_activity(self):
        if self.codeburn_activity_loader is None:
            return
        try:
            value = self.codeburn_activity_loader()
            with self.lock:
                self.codeburn_activity = value
                self.codeburn_activity_status = dict(state="following", last_success_at=now_iso(), error=None)
        except (ValueError, OSError):
            with self.lock:
                self.codeburn_activity_status = dict(
                    state="unavailable", last_success_at=self.codeburn_activity_status["last_success_at"],
                    error="Selected CodeBurn status is unavailable. The last accepted observation is retained.",
                )

    def _include_codeburn_activity(self, value):
        if self.codeburn_activity_loader is not None:
            status = dict(self.codeburn_activity_status)
            value["codeburn_activity_status"] = status
            value["codeburn_activity"] = None if self.codeburn_activity is None else dict(
                self.codeburn_activity, read_status=status, retained=status["state"] != "following",
            )

    def refresh_cached_usage(self):
        if self.cached_usage_loader is None:
            return
        try:
            value = self.cached_usage_loader()
            with self.lock:
                self.cached_usage = value
                self.cached_usage_status = dict(state="following", last_success_at=now_iso(), error=None)
        except (ValueError, OSError):
            with self.lock:
                self.cached_usage_status = dict(
                    state="unavailable", last_success_at=self.cached_usage_status["last_success_at"],
                    error="CodeBurn cache unavailable. Last accepted daily totals are retained.",
                )

    def _include_cached_usage(self, value):
        # Called under the observation lock. These totals never enter event accounting.
        if self.cached_usage_loader is not None:
            status = dict(self.cached_usage_status)
            value["cached_usage_status"] = status
            value["cached_usage"] = None if self.cached_usage is None else dict(
                self.cached_usage, read_status=status, retained=status["state"] != "following",
            )

    @staticmethod
    def _source_status_for_inputs(inputs, previous):
        rows = inputs["inputs"]
        if not rows or inputs["unread_inputs"] == len(rows):
            return dict(previous)
        complete = inputs["healthy_inputs"] == len(rows)
        if complete:
            successful_reads = [row["last_success_at"] for row in rows if row["last_success_at"] is not None]
            return dict(state="following", degraded=False,
                        last_success_at=max(successful_reads) if successful_reads else None, error=None)
        return dict(
            state="unavailable",
            degraded=bool(inputs["healthy_inputs"]),
            last_success_at=previous.get("last_success_at"),
            error="Some configured inputs are unavailable. Accepted history is retained.",
        )

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
        if self.source_observer is not None:
            value = self.source_observer.refresh(self.store)
            with self.lock:
                self.source_status = self._source_status_for_inputs(value, self.source_status)
            return
        if self.loader is None:
            return
        try:
            result = self.store.ingest(self.loader())
            with self.lock:
                self.source_status = dict(state="following", last_success_at=now_iso(), error=None, **result)
        except (ValueError, OSError, sqlite3.Error):
            with self.lock:
                # Diagnostics never echo a native source path or a malformed record
                self.source_status = dict(
                    state="unavailable", last_success_at=self.source_status["last_success_at"],
                    error="Source read or validation failed. Last accepted events are retained.",
                )

    def input_status(self):
        return self.source_observer.snapshot() if self.source_observer is not None else None

    def read(self, query):
        with self.lock:
            inputs = self.input_status()
            revision, events = self.store.read()
            value = snapshot(revision, events, query, self.pricing, self.fx, quota_book=self.quota_book)
            value["insights"]["quotas"]["read_status"] = dict(self.quota_status)
            value.update(identity=self.identity, source_status=dict(self.source_status))
            self._include_cached_usage(value)
            self._include_codeburn_activity(value)
            if inputs is not None:
                value["source_status"] = self._source_status_for_inputs(inputs, self.source_status)
                self.source_status = dict(value["source_status"])
                value["inputs"] = inputs
                value["source_coverage"] = self.source_observer.coverage(now=value["observed_at"])
            return value

    def report(self, query):
        with self.lock:
            inputs = self.input_status()
            revision, events = self.store.read()
            value = detailed_report(revision, events, query, self.pricing, self.fx)
            value.update(identity=self.identity, source_status=dict(self.source_status))
            self._include_cached_usage(value)
            self._include_codeburn_activity(value)
            if inputs is not None:
                value["source_status"] = self._source_status_for_inputs(inputs, self.source_status)
                self.source_status = dict(value["source_status"])
                value["inputs"] = inputs
                value["source_coverage"] = self.source_observer.coverage(now=value["observed_at"])
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
                        input_observations="monitoring.inputs.v1",
                        source_coverage="monitoring.source-coverage.v1",
                        cached_usage="monitoring.cached-usage.v1",
                        codeburn_activity="monitoring.codeburn-activity.v1",
                        insights="monitoring.insights.v1", quota_observations="monitoring.quotas.v1",
                        filters=["provider", "model", "surface", "project", "source", "session", "since", "until", "state"],
                        event_filters=["provider", "model", "surface", "project", "source", "session", "since", "until", "entity"],
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
                    entity = query.pop("entity", None)
                    if entity is not None:
                        if not query.get("source") or not query.get("session"):
                            raise ValueError("Entity filters require exact source and session scope")
                        entity = event_label(entity, "entity")
                    query = filters(query)
                    if entity is not None:
                        query["entity"] = entity
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
                    with observatory.lock:
                        inputs = observatory.input_status()
                        source_status = dict(observatory.source_status)
                        if inputs is not None:
                            source_status = observatory._source_status_for_inputs(inputs, source_status)
                            observatory.source_status = dict(source_status)
                        value = dict(schema="monitoring.health.v1", status="ok", source_status=source_status,
                                     quota_status=dict(observatory.quota_status))
                        if observatory.cached_usage_loader is not None:
                            value["cached_usage_status"] = dict(observatory.cached_usage_status)
                        if observatory.codeburn_activity_loader is not None:
                            value["codeburn_activity_status"] = dict(observatory.codeburn_activity_status)
                    if inputs is not None:
                        value["inputs"] = inputs
                    self.send(200, value)
                else:
                    files = {"/": (WEB / "index.html", "text/html; charset=utf-8"),
                             "/app.js": (WEB / "app.js", "text/javascript; charset=utf-8"),
                             "/appearance.js": (WEB / "appearance.js", "text/javascript; charset=utf-8"),
                             "/navigation.js": (WEB / "navigation.js", "text/javascript; charset=utf-8"),
                             "/navigation.css": (WEB / "navigation.css", "text/css; charset=utf-8"),
                             "/style.css": (WEB / "style.css", "text/css; charset=utf-8"),
                             "/theme/index.js": (WEB / "theme" / "index.js", "text/javascript; charset=utf-8"),
                             "/theme/workspace-navigation.css": (WEB / "theme" / "workspace-navigation.css", "text/css; charset=utf-8"),
                             "/theme/LICENSE": (WEB / "theme" / "LICENSE", "text/plain; charset=utf-8")}
                    if parsed.path not in files:
                        self.send(404, {"error": "Not found"})
                        return
                    path, mime = files[parsed.path]
                    self.send(200, path.read_bytes(), mime)
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

    def cached_usage():
        while not stop.is_set():
            observatory.refresh_cached_usage()
            if stop.wait(5):
                break

    def codeburn_activity():
        while not stop.is_set():
            observatory.refresh_codeburn_activity()
            if stop.wait(5):
                break

    observatory.refresh()
    worker = threading.Thread(target=follow, daemon=True)
    if observatory.loader or observatory.source_observer is not None:
        worker.start()
    quota_worker = None
    if observatory.quota_loader:
        quota_worker = threading.Thread(target=quotas)
        quota_worker.start()
    cache_worker = None
    if observatory.cached_usage_loader is not None:
        cache_worker = threading.Thread(target=cached_usage, daemon=True)
        cache_worker.start()
    activity_worker = None
    if observatory.codeburn_activity_loader is not None:
        activity_worker = threading.Thread(target=codeburn_activity, daemon=True)
        activity_worker.start()
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
        if cache_worker:
            cache_worker.join(timeout=5)
        if activity_worker:
            activity_worker.join(timeout=5)
        if previous_term is not None:
            signal.signal(signal.SIGTERM, previous_term)
