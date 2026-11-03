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
from .sessions import session_page

WEB = Path(__file__).with_name("web")


class Observatory:
    def __init__(self, store, pricing, fx, loader=None, quota_loader=None, quota_book=None, source_observer=None,
                 cached_usage_loader=None, codeburn_activity_loader=None, session_cache_loader=None, process_observer=None,
                 token_threshold=None, cost_thresholds=None, app_threads_loader=None, app_turns_loader=None):
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
        self.session_cache_loader = session_cache_loader
        self.session_cache = None
        self.session_cache_status = dict(state="unread", last_success_at=None, error=None)
        self.process_observer = process_observer
        self.app_threads_loader = app_threads_loader
        self.app_threads = None
        self.app_turns_loader, self.app_turns = app_turns_loader, None
        if token_threshold is not None:
            from .token_threshold import validate_limit
            validate_limit(token_threshold)
        self.token_threshold = token_threshold
        if cost_thresholds:
            from .cost_thresholds import validate_limits
            self.cost_thresholds = validate_limits(cost_thresholds)
        else:
            self.cost_thresholds = None

    def _include_app_threads(self, value):
        if self.app_threads_loader is None:
            return
        from .app_threads import dated_app_snapshot
        self.app_threads = dated_app_snapshot(self.app_threads_loader, self.app_threads, value["observed_at"])
        value["app_threads"] = self.app_threads

    def _include_app_turns(self, value):
        if self.app_turns_loader is None:
            return
        from .app_threads import dated_app_snapshot
        self.app_turns = dated_app_snapshot(self.app_turns_loader, self.app_turns, value["observed_at"],
                                           schema="monitoring.codex-app-turns.v1", rows="tasks")
        value["codex_app_turns"] = self.app_turns

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

    def refresh_session_cache(self):
        if self.session_cache_loader is None:
            return
        try:
            book = self.session_cache_loader()
            with self.lock:
                self.session_cache = book
                self.session_cache_status = dict(state="following", last_success_at=now_iso(), error=None)
        except (ValueError, OSError):
            with self.lock:
                self.session_cache_status = dict(
                    state="unavailable", last_success_at=self.session_cache_status["last_success_at"],
                    error="Selected cache member unavailable. Last accepted turn metadata is retained.")

    def _include_session_cache(self, value):
        if self.session_cache_loader is not None:
            status = dict(self.session_cache_status)
            value["selected_cache_status"] = status
            value["selected_cache_session"] = None if self.session_cache is None else dict(
                self.session_cache, read_status=status, retained=status["state"] != "following")

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
            result = self.store.ingest(self.loader(),
                                       claude_corrections=getattr(self.loader, "correct_usage", False))
            if hasattr(self.loader, "commit"):
                self.loader.commit()
            with self.lock:
                self.source_status = dict(state="following", last_success_at=now_iso(), error=None, **result)
        except (ValueError, OSError, sqlite3.Error):
            if hasattr(self.loader, "discard"):
                self.loader.discard()
            with self.lock:
                # Diagnostics never echo a native source path or a malformed record
                self.source_status = dict(
                    state="unavailable", last_success_at=self.source_status["last_success_at"],
                    error="Source read or validation failed. Last accepted events are retained.",
                )

    def input_status(self):
        return self.source_observer.snapshot() if self.source_observer is not None else None

    def _include_responses(self, value):
        if self.source_observer is not None:
            book = self.source_observer.responses()
            if book["configured_sources"]:
                value["claude_responses"] = book
        elif getattr(self.loader, "supports_responses", False):
            book = self.loader.responses()
            value["claude_responses"] = dict(book, configured_sources=1,
                unavailable_sources=int(self.source_status["state"] == "unavailable"),
                rows=[dict(row, source_state=self.source_status["state"]) for row in book["rows"]])

    def _include_turns(self, value):
        if self.source_observer is not None:
            book = self.source_observer.turns()
            if book["configured_sources"]:
                value["codex_turns"] = book
        elif getattr(self.loader, "supports_turns", False):
            book = self.loader.turns()
            value["codex_turns"] = dict(schema=book["schema"], truncated=book["truncated"],
                ambiguous_turns=book["ambiguous_turns"], configured_sources=1,
                unavailable_sources=int(self.source_status["state"] == "unavailable"),
                rows=[dict(row, source_state=self.source_status["state"]) for row in book["rows"]])

    def read(self, query):
        with self.lock:
            inputs = self.input_status()
            revision, events = self.store.read()
            value = snapshot(revision, events, query, self.pricing, self.fx, quota_book=self.quota_book)
            self._include_app_threads(value)
            self._include_app_turns(value)
            if self.token_threshold is not None:
                from .token_threshold import evaluate_token_threshold
                value["token_threshold"] = evaluate_token_threshold(events, limit=self.token_threshold,
                                                                        now=value["observed_at"])
            if self.cost_thresholds is not None:
                from .cost_thresholds import evaluate_cost_thresholds
                value["cost_thresholds"] = evaluate_cost_thresholds(events, limits=self.cost_thresholds,
                    pricing=self.pricing, fx=self.fx, now=value["observed_at"])
            value["insights"]["quotas"]["read_status"] = dict(self.quota_status)
            value.update(identity=self.identity, source_status=dict(self.source_status))
            self._include_cached_usage(value)
            self._include_codeburn_activity(value)
            self._include_session_cache(value)
            self._include_responses(value)
            self._include_turns(value)
            if self.process_observer is not None:
                value["os_processes"] = self.process_observer.snapshot()
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
            self._include_session_cache(value)
            self._include_responses(value)
            self._include_turns(value)
            if self.process_observer is not None:
                value["os_processes"] = self.process_observer.snapshot()
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
                    capabilities = dict(
                        schema="monitoring.capabilities.v1", version=__version__,
                        identity=observatory.identity, read_only=True,
                        snapshot="/api/v1/snapshot", events="/api/v1/events",
                        sessions="/api/v1/sessions",
                        report="/api/v1/report", event_features=["tool-categories.v1"],
                        input_observations="monitoring.inputs.v1",
                        source_coverage="monitoring.source-coverage.v1",
                        cached_usage="monitoring.cached-usage.v1",
                        codeburn_activity="monitoring.codeburn-activity.v1",
                        insights="monitoring.insights.v1", quota_observations="monitoring.quotas.v1",
                        filters=["provider", "model", "surface", "project", "source", "session", "since", "until", "state"],
                        event_filters=["provider", "model", "surface", "project", "source", "session", "since", "until", "entity"],
                        session_filters=["provider", "model", "surface", "project", "source", "session", "since", "until"],
                        limits={"read_events": 100000, "snapshot_entities": 500, "event_page": 500},
                    )
                    if observatory.app_threads_loader is not None:
                        capabilities["app_threads"] = "monitoring.app-threads.v1"
                    if observatory.app_turns_loader is not None:
                        capabilities["codex_app_turns"] = "monitoring.codex-app-turns.v1"
                    if observatory.process_observer is not None:
                        capabilities["os_processes"] = "monitoring.os-processes.v1"
                    if observatory.session_cache_loader is not None:
                        capabilities["selected_cache_session"] = "monitoring.selected-cache-session.v1"
                    if observatory.token_threshold is not None:
                        capabilities["token_threshold"] = "monitoring.token-threshold.v1"
                    if observatory.cost_thresholds is not None:
                        capabilities["cost_thresholds"] = "monitoring.cost-thresholds.v1"
                    if ((observatory.source_observer is not None and
                         observatory.source_observer.responses()["configured_sources"]) or
                            getattr(observatory.loader, "supports_responses", False)):
                        capabilities["claude_responses"] = "monitoring.claude-responses.v1"
                    if ((observatory.source_observer is not None and
                         observatory.source_observer.turns()["configured_sources"]) or
                            getattr(observatory.loader, "supports_turns", False)):
                        capabilities["codex_turns"] = "monitoring.codex-turns.v1"
                    self.send(200, capabilities)
                elif parsed.path == "/api/v1/snapshot":
                    self.send(200, observatory.read(query))
                elif parsed.path == "/api/v1/report":
                    self.send(200, observatory.report(query))
                elif parsed.path == "/api/v1/sessions":
                    limit = int(query.pop("limit", "100"))
                    after = int(query.pop("after", "0"))
                    expected = query.pop("expected_revision", None)
                    search = query.pop("search", "")
                    if after and expected is None:
                        raise ValueError("Later session pages require the first revision")
                    with observatory.lock:
                        revision, events = observatory.store.read()
                        if expected is not None and int(expected) != revision:
                            self.send(409, {"error": "Ledger changed. Restart session list", "revision": revision})
                            return
                        page = session_page(revision, events, query, limit=limit, after=after, search=search)
                        self.send(200, dict(page, identity=observatory.identity))
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
                        if observatory.session_cache_loader is not None:
                            value["selected_cache_status"] = dict(observatory.session_cache_status)
                        if observatory.process_observer is not None:
                            value["os_process_status"] = observatory.process_observer.snapshot()["state"]
                    if inputs is not None:
                        value["inputs"] = inputs
                    self.send(200, value)
                else:
                    files = {"/": (WEB / "index.html", "text/html; charset=utf-8"),
                             "/quota-watch.js": (WEB / "quota-watch.js", "text/javascript; charset=utf-8"),
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

    def selected_cache():
        while not stop.is_set():
            observatory.refresh_session_cache()
            if stop.wait(5):
                break

    def processes():
        while not stop.is_set():
            observatory.process_observer.refresh()
            if stop.wait(observatory.process_observer.interval_seconds):
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
    session_cache_worker = None
    if observatory.session_cache_loader is not None:
        session_cache_worker = threading.Thread(target=selected_cache, daemon=True)
        session_cache_worker.start()
    process_worker = None
    if observatory.process_observer is not None:
        process_worker = threading.Thread(target=processes, daemon=True)
        process_worker.start()
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
        if session_cache_worker:
            session_cache_worker.join(timeout=5)
        if process_worker:
            process_worker.join(timeout=5)
        if previous_term is not None:
            signal.signal(signal.SIGTERM, previous_term)
