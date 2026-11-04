"""Preview explicitly selected sources against a disposable ledger copy."""

import sqlite3

from .ledger_preview import snapshot_ledger
from .metrics import now_iso
from .source_coverage import source_summary
from .sources import load_sources
from .source_overlap import NativeOverlapError, NATIVE_FORMATS, MESSAGE
from .claude_lineage import ClaudeForkHistoryError, MESSAGE as CLAUDE_FORK_MESSAGE
from .codex_lineage import (CodexLineageError, MESSAGE as CODEX_LINEAGE_MESSAGE,
                            UnsupportedHistoryError, UNQUALIFIED_MESSAGE)

SCHEMA = "monitoring.source-readiness.v1"
MAX_PREVIEW_EVENTS = 100_000

_NOTE = (
    "All inputs are tested against the same private copy of the selected ledger, but files are read sequentially "
    "and can change before a later preview or import. Repeated native identities within selected Claude or Codex files refuse; other collector overlap under different source aliases remains unresolved."
)


def _manifest(path):
    try:
        return load_sources(path)
    except Exception:
        raise ValueError("Selected source manifest could not be read or validated.") from None


def _stored_keys(store):
    try:
        with store.connect() as db:
            return {(source, event_id) for source, event_id in db.execute(
                "SELECT source, event_id FROM events"
            )}
    except sqlite3.Error:
        raise


def _result(source, state, summary=None, proposed=None, error=None):
    return {
        "id": source.id,
        "format": source.format,
        "state": state,
        "summary": summary,
        "proposed": proposed,
        "error": error,
    }


def preview_selection(manifest, db=None):
    """Check whether selected inputs could be ingested, without changing ``db``.

    Sources are read in manifest order and attempted independently against a
    private writable copy of the supplied ledger. A failed or rejected source
    leaves that copy unchanged for the next source.
    """
    sources = _manifest(manifest)
    native_families = {source.id: source.format for source in sources if source.format in NATIVE_FORMATS}
    observed_at = now_iso()
    try:
        with snapshot_ledger(db) as preview:
            store = preview.store
            known = _stored_keys(store)
            inputs = []
            for source in sources:
                try:
                    rows = source.load(live=True)
                except ClaudeForkHistoryError:
                    inputs.append(_result(source, "rejected", error=CLAUDE_FORK_MESSAGE))
                    continue
                except UnsupportedHistoryError:
                    inputs.append(_result(source, "rejected", error=UNQUALIFIED_MESSAGE))
                    continue
                except CodexLineageError:
                    inputs.append(_result(source, "rejected", error=CODEX_LINEAGE_MESSAGE))
                    continue
                except OSError:
                    inputs.append(_result(
                        source, "unavailable", error="Selected file is unavailable. Check its existence and read access."
                    ))
                    continue
                except Exception:
                    inputs.append(_result(
                        source, "rejected", error="Selected file could not be processed using its declared format."
                    ))
                    continue

                try:
                    sampled_at = now_iso()
                    summary = source_summary(source, rows, sampled_at=sampled_at)
                    row_keys = {(row["source"], row["id"]) for row in rows}
                except Exception:
                    inputs.append(_result(
                        source, "rejected", error="Selected input contains invalid or conflicting event metadata."
                    ))
                    continue

                new_keys = row_keys - known
                if len(known) + len(new_keys) > MAX_PREVIEW_EVENTS:
                    inputs.append(_result(
                        source,
                        "rejected",
                        summary,
                        error="Selected input would exceed the 100,000-event preview limit.",
                    ))
                    continue

                try:
                    proposed = store.ingest(rows, overlap_sources=[alias for alias, family in native_families.items()
                                                                  if alias != source.id and family == native_families.get(source.id)])
                except NativeOverlapError:
                    inputs.append(_result(source, "rejected", summary, error=MESSAGE))
                    continue
                except Exception:
                    inputs.append(_result(
                        source,
                        "rejected",
                        summary,
                        error="Selected input conflicts with ledger history or hierarchy rules.",
                    ))
                    continue

                known.update(row_keys)
                inputs.append(_result(source, "ready", summary, proposed))

            kind = "supplied-ledger" if db is not None else "empty"
            ready = all(item["state"] == "ready" for item in inputs)
            return {
                "schema": SCHEMA,
                "observed_at": now_iso(),
                "scope": "explicit-inputs-and-ledger-snapshot",
                "read_only": True,
                "base": {"kind": kind, "revision": preview.revision, "events": preview.events},
                "ready": ready,
                "inputs": inputs,
                "note": _NOTE,
            }
    except Exception:
        raise ValueError("Selected ledger could not be safely previewed.") from None
