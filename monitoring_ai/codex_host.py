"""Capture metadata from one explicitly selected existing local Codex host."""

from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import selectors
import re
import stat
import subprocess
import time

from .adapters import unique_keys, reject_constant
from .app_threads import capture_app_threads
from .metrics import now_iso

MAX_FRAME = 1024 * 1024


def project_threads(result, cwd):
    if not isinstance(result, dict) or not isinstance(result.get("data"), list) or len(result["data"]) > 20:
        raise ValueError("Unsupported Codex metadata page")
    selected = []
    for row in result["data"]:
        if not isinstance(row, dict) or not isinstance(row.get("status"), dict):
            raise ValueError("Unsupported Codex runtime status")
        if row.get("cwd") != str(cwd):
            raise ValueError("Codex metadata page exceeded the selected project scope")
        updated = row.get("updatedAt")
        if type(updated) is not int or not 0 <= updated <= 253402300799:
            raise ValueError("Original thread update time is required")
        state = row["status"].get("type")
        if not isinstance(state, str):
            raise ValueError("Missing Codex runtime status")
        selected.append(dict(id=row.get("id"), kind="codex", host="local",
                             status=state if state in ("active", "idle") else "unknown",
                             updated_at=datetime.fromtimestamp(updated, timezone.utc).isoformat()))
    return selected


def _page(process, cwd, *, timeout=5):
    """Use a fixed RPC sequence; never resume a thread or repair stored logs."""
    deadline = time.monotonic() + timeout
    pending = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)

        def send(value):
            process.stdin.write((json.dumps(value, separators=(",", ":")) + "\n").encode())
            process.stdin.flush()

        def receive(ident):
            while True:
                if time.monotonic() >= deadline:
                    raise ValueError("Codex metadata read timed out")
                if b"\n" not in pending:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise ValueError("Codex metadata read timed out")
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        raise ValueError("Codex metadata connection closed")
                    pending.extend(chunk)
                    if len(pending) > MAX_FRAME:
                        raise ValueError("Codex metadata frame exceeds limit")
                    continue
                line, _, tail = pending.partition(b"\n")
                pending[:] = tail
                value = json.loads(line, object_pairs_hook=unique_keys, parse_constant=reject_constant)
                if not isinstance(value, dict):
                    raise ValueError("Unsupported Codex protocol frame")
                # No authentication, attestation or execution request is answered
                if "method" in value:
                    if "id" in value:
                        raise ValueError("Unexpected Codex server request")
                    continue
                if value.get("id") != ident or "error" in value or "result" not in value:
                    raise ValueError("Codex metadata request failed; no fallback was attempted")
                return value["result"]

        send(dict(id=1, method="initialize", params=dict(clientInfo=dict(
            name="monitoring_metadata_observer", version="0.8.41"))))
        initialized = receive(1)
        if (not isinstance(initialized, dict) or not isinstance(initialized.get("userAgent"), str)
                or not re.search(r"(?:^|[/ ])0\.160\.0(?:[ (]|$)", initialized["userAgent"])):
            raise ValueError("Codex server version is not qualified for database-only metadata reads")
        send(dict(method="initialized"))
        send(dict(id=2, method="thread/list", params=dict(useStateDbOnly=True, cwd=str(cwd),
             limit=20, sortKey="updated_at", archived=False,
             sourceKinds=["cli", "vscode", "exec", "appServer", "subAgent"])))
        return project_threads(receive(2), cwd)


def capture_host(socket_path, cwd, output, *, replace=False):
    """Connect once to an owned private socket; never start or change its host."""
    selected = Path(socket_path).absolute()
    info = selected.lstat()
    parent = selected.parent.stat()
    if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or parent.st_uid != os.getuid()
            or stat.S_IMODE(parent.st_mode) != 0o700):
        raise ValueError("Select an owned mode-0600 socket in an owned mode-0700 directory")
    project = Path(cwd).resolve(strict=True)
    if not project.is_dir():
        raise ValueError("Select an existing authorized project directory")
    process = subprocess.Popen(["codex", "app-server", "proxy", "--sock", str(selected)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        rows = _page(process, project)
        captured_at = now_iso()
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        process.stdout.close()
    return capture_app_threads(io.BytesIO(json.dumps(rows).encode()), output, captured_at,
                               replace=replace, source="codex-local-app-server")
