"""Read explicit quota files or the installed Codex account allowance interface."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import stat
import subprocess
import time

from .events import label, timestamp
from .insights import validate_quota_book
from . import __version__

MAX_BYTES = 1024 * 1024


def quota_export(path):
    path = Path(path).absolute()
    if path.resolve() != path:
        raise ValueError("Select an exact quota file without symlinks")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            raise ValueError("Quota file must be regular and at most 1 MiB")
        content = stream.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise ValueError("Quota file exceeds 1 MiB")
    try:
        return json.loads(content)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("Invalid quota file") from None


def quota_file(path):
    return validate_quota_book(quota_export(path))


def codex_book(raw, *, captured_at, source="codex-quota", account="default"):
    """Normalize a provider response without retaining account IDs or credentials."""
    if not isinstance(raw, dict):
        raise ValueError("Invalid Codex allowance response")
    label(source, "source")
    label(account, "account")
    captured_at = timestamp(captured_at)
    # A present map, including an empty one, takes precedence over the legacy view
    if "rateLimitsByLimitId" in raw and raw["rateLimitsByLimitId"] is not None:
        mapping = raw["rateLimitsByLimitId"]
        if not isinstance(mapping, dict):
            raise ValueError("Invalid Codex rate-limit map")
    else:
        legacy = raw.get("rateLimits")
        mapping = {str(legacy.get("limitId") or "codex"): legacy} if isinstance(legacy, dict) else {}
    if len(mapping) > 100:
        raise ValueError("Codex allowance response exceeds the bucket limit")
    windows = []
    for bucket, value in sorted(mapping.items()):
        label(bucket, "bucket")
        if not isinstance(value, dict):
            raise ValueError("Invalid Codex allowance bucket")
        for lane in ("primary", "secondary"):
            window = value.get(lane)
            if window is None:
                continue
            if not isinstance(window, dict):
                raise ValueError("Invalid Codex allowance window")
            reset = window.get("resetsAt")
            if reset is not None:
                if type(reset) not in (int, float) or not 0 <= reset <= 253402300799:
                    raise ValueError("Invalid Codex reset timestamp")
                reset = datetime.fromtimestamp(reset, timezone.utc).isoformat()
            identity = label(f"{bucket}.{lane}", "window")
            windows.append(dict(id=identity, label=identity, used_percent=window.get("usedPercent"),
                                window_minutes=window.get("windowDurationMins"), resets_at=reset))
    return validate_quota_book(dict(schema="monitoring.quotas.v1", snapshots=[dict(
        source=source, provider="openai", account=account, captured_at=captured_at,
        provenance="codex.app-server.account-rateLimits-read", windows=windows,
    )]))


def _cleanup(process):
    # This process group was created by this collector; no existing daemon is targeted
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=2)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    for handle in (process.stdin, process.stdout):
        try:
            handle.close()
        except BrokenPipeError:
            pass


def read_codex_quota(*, executable=None, source="codex-quota", account="default", timeout=10):
    """Run a finite metadata-only RPC exchange using the existing native login."""
    command = executable or shutil.which("codex")
    if not command or not Path(command).is_absolute() or not Path(command).is_file():
        raise ValueError("Configure an installed Codex executable")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 30:
        raise ValueError("Quota read timeout must be at most 30 seconds")
    label(source, "source")
    label(account, "account")
    process = subprocess.Popen([str(command), "app-server", "--listen", "stdio://"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + timeout
    buffer, total = b"", 0
    selector = selectors.DefaultSelector()

    def send(message):
        process.stdin.write((json.dumps(message) + "\n").encode())
        process.stdin.flush()

    def response(identity):
        nonlocal buffer, total
        while True:
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                try:
                    message = json.loads(line)
                except (ValueError, UnicodeError, RecursionError):
                    raise ValueError("Invalid Codex RPC response") from None
                if not isinstance(message, dict):
                    raise ValueError("Invalid Codex RPC response")
                if message.get("id") != identity:
                    continue  # Notifications never authorize another request
                if "error" in message or not isinstance(message.get("result"), dict):
                    raise ValueError("Codex quota read unavailable or unsupported")
                return message["result"]
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise ValueError("Codex quota read timed out")
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                raise ValueError("Codex quota process exited before its response")
            total += len(chunk)
            if total > MAX_BYTES:
                raise ValueError("Codex quota response exceeds 1 MiB")
            buffer += chunk

    try:
        selector.register(process.stdout, selectors.EVENT_READ)
        send(dict(id=1, method="initialize", params=dict(clientInfo=dict(name="monitoring_ai", version=__version__))))
        response(1)
        send(dict(method="initialized", params={}))
        send(dict(id=2, method="account/rateLimits/read"))
        raw = response(2)
        captured = datetime.now(timezone.utc).isoformat()
        return codex_book(raw, captured_at=captured, source=source, account=account)
    except (OSError, ValueError):
        raise ValueError("Codex quota read failed. Existing login and protocol support are required") from None
    finally:
        selector.close()
        try:
            _cleanup(process)
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError("Owned quota process cleanup failed") from None
