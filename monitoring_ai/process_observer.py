"""Opt-in, read-only OS process samples; never inspect argv or environment."""

from datetime import datetime, timezone
from pathlib import PurePath
import re
import subprocess
import sys
import threading


PROCESS_NAMES = frozenset({"codex", "claude", "codeburnmenubar", "opencode", "aider", "gemini", "goose"})
PS_COLUMNS = "pid=,ppid=,%cpu=,rss=,lstart=,comm="
ROW = re.compile(r"^\s*(\d+)\s+(\d+)\s+([0-9]+(?:\.[0-9]+)?)\s+(\d+)\s+"
                 r"(\w{3}\s+\w{3}\s+\d{1,2}\s+\d\d:\d\d:\d\d\s+\d{4})\s+(.+?)\s*$")


def _ps():
    if sys.platform != "darwin":
        raise OSError("OS process sampling currently supports macOS")
    result = subprocess.run(["/bin/ps", "-axo", PS_COLUMNS], capture_output=True,
                            text=True, errors="replace", timeout=3, check=True,
                            env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"})
    return result.stdout


def parse_ps(output, *, names=PROCESS_NAMES):
    """Project only allowed executable basenames and fixed numeric/time columns."""
    if not isinstance(output, str) or len(output) > 4_000_000:
        raise ValueError("OS process sample exceeds limit")
    rows = []
    for line in output.splitlines():
        match = ROW.fullmatch(line)
        if not match:
            continue
        pid, ppid, cpu, rss, started, executable = match.groups()
        name = PurePath(executable).name
        if name.casefold() not in names:
            continue
        try:
            started_at = datetime.strptime(started, "%a %b %d %H:%M:%S %Y").astimezone(timezone.utc)
            cpu_percent, rss_kib = float(cpu), int(rss)
        except ValueError:
            continue
        if cpu_percent < 0 or not cpu_percent < float("inf") or rss_kib < 0:
            continue
        rows.append(dict(pid=int(pid), parent_pid=int(ppid), executable=name,
                         started_at=started_at.isoformat().replace("+00:00", "Z"),
                         cpu_percent=cpu_percent, rss_bytes=rss_kib * 1024,
                         session=None, worker=None))
    rows.sort(key=lambda row: (-row["cpu_percent"], row["executable"].casefold(), row["pid"]))
    return rows


class ProcessObserver:
    def __init__(self, reader=_ps, *, names=PROCESS_NAMES):
        self.reader, self.names = reader, frozenset(name.casefold() for name in names)
        self.lock = threading.Lock()
        self.value = dict(schema="monitoring.os-processes.v1", state="unread", sampled_at=None,
                          processes=[], no_longer_observed=[], changed_count=0, truncated=False,
                          coverage="matching-executable-names-only")

    def refresh(self):
        try:
            rows = parse_ps(self.reader(), names=self.names)
        except (OSError, ValueError, subprocess.SubprocessError):
            with self.lock:
                # A failed read cannot establish that a formerly seen process still exists.
                self.value = dict(schema="monitoring.os-processes.v1", state="unavailable", sampled_at=None,
                                  processes=[], no_longer_observed=[], changed_count=0, truncated=False,
                                  coverage="matching-executable-names-only")
            return self.snapshot()
        with self.lock:
            prior = {(r["pid"], r["started_at"]): r for r in self.value["processes"]}
            current = {(r["pid"], r["started_at"]) for r in rows}
            missing = [dict(pid=r["pid"], executable=r["executable"], started_at=r["started_at"])
                       for key, r in prior.items() if key not in current]
            changed = sum(1 for r in rows if (old := prior.get((r["pid"], r["started_at"])))
                          and (old["cpu_percent"], old["rss_bytes"]) != (r["cpu_percent"], r["rss_bytes"]))
            self.value = dict(schema="monitoring.os-processes.v1", state="following",
                              sampled_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                              processes=rows[:200], no_longer_observed=missing[:200],
                              changed_count=changed, truncated=len(rows) > 200 or len(missing) > 200,
                              coverage="matching-executable-names-only")
        return self.snapshot()

    def snapshot(self):
        with self.lock:
            return {**self.value, "processes": [dict(row) for row in self.value["processes"]],
                    "no_longer_observed": [dict(row) for row in self.value["no_longer_observed"]]}
