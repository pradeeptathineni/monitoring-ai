"""Generate fictional sessions and appendable live activity without model calls."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time

from .events import SCHEMA


def demo_events(now=None):
    now = now or datetime.now(timezone.utc)
    specs = [
        ("atlas", "atlas", None, "session", "openai", "demo-reasoner", "codex", "orbit-api", "running"),
        ("atlas", "scout", "atlas", "agent", "anthropic", "demo-sonnet", "claude-code", "orbit-api", "running"),
        ("atlas", "checks", "scout", "worker", "local", "demo-local", "worker", "orbit-api", "waiting"),
        ("lumen", "lumen", None, "session", "anthropic", "demo-sonnet", "claude-code", "lumen-web", "completed"),
        ("index", "index", None, "session", "openai", "demo-fast", "api", "search-kit", "blocked"),
        ("design", "design", None, "session", "local", "demo-local", "ollama", "lumen-web", "unknown"),
    ]
    events = []
    for index, (session, entity, parent, entity_type, provider, model, surface, project, state) in enumerate(specs):
        base = dict(schema=SCHEMA, source="synthetic", session=session, entity=entity,
                    parent=parent, entity_type=entity_type, provider=provider, model=model,
                    surface=surface, project=project)
        started = now - timedelta(minutes=90 - index * 9)
        for turn in range(14):
            at = started + timedelta(minutes=turn * 3)
            event = dict(base, id=f"{entity}-usage-{turn}", timestamp=at.isoformat(), kind="usage",
                         tokens=dict(input=2300 + (index + turn) * 430, cache_read=4000 + turn * 900,
                                     cache_write=1000 if turn == 0 else 0, output=500 + turn * 92))
            if model != "demo-local":
                event["cost"] = dict(amount=str(round(.015 + (index + turn) * .0062, 6)), currency="USD",
                                     basis="billed" if entity == "lumen" else "estimated",
                                     provenance="fictional-invoice" if entity == "lumen" else "fictional-price")
            events.append(event)
        for turn in range(3):
            events.append(dict(base, id=f"{entity}-tool-{turn}", kind="tool",
                timestamp=(started + timedelta(minutes=turn * 12 + 1)).isoformat(),
                tool=["search", "read", "test"][turn], outcome="error" if entity == "index" and turn == 2 else "ok",
                duration_ms=400 + turn * 670))
        status_at = now - timedelta(seconds=12 + index) if state != "completed" else started + timedelta(minutes=43)
        events.append(dict(base, id=f"{entity}-status", kind="status", timestamp=status_at.isoformat(), state=state))
    return sorted(events, key=lambda e: (e["timestamp"], e["id"]))


def write_demo(path):
    path = Path(path)
    # Refuse overwriting a source selected by the operator
    with path.open("x") as stream:
        for event in demo_events():
            stream.write(json.dumps(event) + "\n")


def demo_quotas(now=None):
    now = now or datetime.now(timezone.utc)
    return dict(schema="monitoring.quotas.v1", snapshots=[dict(
        source="synthetic-quota", provider="example", account="fictional-account",
        captured_at=now.isoformat(), provenance="fictional-quota",
        windows=[dict(id="five-hour", label="five-hour", used_percent=42,
                      window_minutes=300, resets_at=(now + timedelta(hours=2)).isoformat()),
                 dict(id="weekly", label="weekly", used_percent=None,
                      window_minutes=10080, resets_at=None)],
    )])


def stream_demo(path, interval=3):
    write_demo(path)
    print("Synthetic producer running. Ctrl-C stops only this producer.", flush=True)
    try:
        for tick in range(1, 1000000):
            time.sleep(interval)
            now = datetime.now(timezone.utc).isoformat()
            common = dict(schema=SCHEMA, source="synthetic", session="atlas", entity="atlas", parent=None,
                          entity_type="session", provider="openai", model="demo-reasoner", surface="codex", project="orbit-api")
            rows = [dict(common, id=f"stream-{tick}", timestamp=now, kind="usage",
                         tokens=dict(input=500, cache_read=1700, cache_write=0, output=240),
                         cost=dict(amount="0.006", currency="USD", basis="estimated", provenance="fictional-price")),
                    dict(common, id=f"heartbeat-{tick}", timestamp=now, kind="status", state="running")]
            with Path(path).open("a") as stream:
                for row in rows:
                    stream.write(json.dumps(row) + "\n")
    except KeyboardInterrupt:
        pass
