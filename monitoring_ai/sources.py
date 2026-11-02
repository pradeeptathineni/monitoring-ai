"""Parse the explicit source manifest.

Source paths are private configuration. They are canonicalized and checked
with lstat only. Source data is never opened here, and errors never echo
paths or manifest content.
"""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import stat

from . import adapters, events

SCHEMA = "monitoring.sources.v1"
MAX_MANIFEST_BYTES = 64 * 1024
MAX_SOURCES = 32
MAX_PATH_CHARS = 4096
FORMATS = frozenset({"normalized", "claude", "codex"})
REQUIRED = frozenset({"id", "format", "path"})
ALLOWED = REQUIRED | {"project"}


class SourceManifestError(ValueError):
    """A rejected manifest. Messages are static and never include input."""


@dataclass(frozen=True)
class Source:
    id: str
    format: str
    path: Path = field(repr=False)  # Private configuration, never displayed
    project: str | None = None

    def load(self, live=True):
        return adapters.load(self.path, self.format, self.id, self.project, live=live)


def load_sources(path):
    """Read one explicit manifest and return validated sources in manifest order."""
    return _validate(_decode(_read_manifest(path)))


def _read_manifest(path):
    if isinstance(path, bytes) or not isinstance(path, (str, os.PathLike)):
        raise SourceManifestError("Source manifest path must be a string path")
    raw = os.fspath(path)
    if not isinstance(raw, str) or not raw or "\x00" in raw or raw.endswith("/"):
        raise SourceManifestError("Source manifest path must name a file")
    selected = Path(raw).absolute()
    try:
        # Canonicalize directory aliases such as macOS /tmp; never follow the final component
        parent = selected.parent.resolve(strict=True)
    except (OSError, RuntimeError):
        raise SourceManifestError("Source manifest is unavailable") from None
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(parent / selected.name, flags)
    except OSError:
        raise SourceManifestError(
            "Source manifest must be a readable regular file, not a symlink"
        ) from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise SourceManifestError("Source manifest must be a regular file")
        if info.st_size > MAX_MANIFEST_BYTES:
            raise SourceManifestError("Source manifest exceeds 64 KiB")
        chunks, total = [], 0
        while total <= MAX_MANIFEST_BYTES:
            chunk = os.read(fd, MAX_MANIFEST_BYTES + 1 - total)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
    except OSError:
        raise SourceManifestError("Source manifest could not be read") from None
    finally:
        os.close(fd)
    if total > MAX_MANIFEST_BYTES:
        raise SourceManifestError("Source manifest exceeds 64 KiB")
    return b"".join(chunks)


def _unique_keys(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise SourceManifestError("Source manifest repeats a JSON object key")
        document[key] = value
    return document


def _no_constants(_):
    raise SourceManifestError("Source manifest contains a non-JSON number")


def _decode(content):
    try:
        return json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_unique_keys,
            parse_constant=_no_constants,
        )
    except SourceManifestError:
        raise
    except (ValueError, RecursionError):
        raise SourceManifestError("Source manifest must be UTF-8 JSON") from None


def _validate(document):
    if type(document) is not dict or set(document) != {"schema", "sources"}:
        raise SourceManifestError("Source manifest must contain exactly schema and sources")
    if type(document["schema"]) is not str or document["schema"] != SCHEMA:
        raise SourceManifestError("Unsupported source manifest schema")
    entries = document["sources"]
    if type(entries) is not list or not 1 <= len(entries) <= MAX_SOURCES:
        raise SourceManifestError("Source manifest must list 1 to 32 sources")
    sources, ids, paths, files = [], set(), set(), set()
    for entry in entries:
        source, identity = _entry(entry)
        if source.id in ids:
            raise SourceManifestError("Source IDs must be unique")
        if source.path in paths or (identity is not None and identity in files):
            raise SourceManifestError("Source paths must be unique")
        ids.add(source.id)
        paths.add(source.path)
        if identity is not None:
            files.add(identity)
        sources.append(source)
    return tuple(sources)


def _label(value, message):
    try:
        return events.label(value, "value")
    except ValueError:
        raise SourceManifestError(message) from None


def _entry(entry):
    if type(entry) is not dict:
        raise SourceManifestError("Each source must be an object")
    keys = set(entry)
    if not REQUIRED <= keys or not keys <= ALLOWED:
        raise SourceManifestError("Sources need id, format and path, with optional project only")
    identifier = _label(entry["id"], "Source id must be a short identifier")
    kind = entry["format"]
    if type(kind) is not str or kind not in FORMATS:
        raise SourceManifestError("Source format must be normalized, claude or codex")
    project = entry.get("project")
    if kind == "normalized":
        if project is not None:
            raise SourceManifestError(
                "Normalized sources keep their event project; omit project or use null"
            )
    else:
        if project is None:
            raise SourceManifestError("Native sources require a project alias")
        project = _label(project, "Source project must be a short identifier")
    path, identity = _source_path(entry["path"])
    return Source(identifier, kind, path, project), identity


def _source_path(value):
    invalid = "Source path must be an absolute local file path"
    if type(value) is not str or not value or len(value) > MAX_PATH_CHARS:
        raise SourceManifestError(invalid)
    if "\x00" in value or "~" in value or "://" in value:
        raise SourceManifestError(invalid)
    if not value.startswith("/"):
        raise SourceManifestError(invalid)
    parts = value.split("/")[1:]
    if any(part in ("", ".", "..") for part in parts):
        raise SourceManifestError(invalid)
    selected = Path(value)
    try:
        # Canonicalize parent aliases only; the final component is never resolved
        parent = selected.parent.resolve()
    except (OSError, RuntimeError):
        raise SourceManifestError(invalid) from None
    canonical = parent / selected.name
    try:
        info = os.lstat(canonical)
    except FileNotFoundError:
        return canonical, None  # Configured but currently unavailable input
    except OSError:
        raise SourceManifestError("Source path is not accessible") from None
    if stat.S_ISLNK(info.st_mode):
        raise SourceManifestError("Source path must not be a symlink")
    if not stat.S_ISREG(info.st_mode):
        raise SourceManifestError("Source path must be a regular file")
    return canonical, (info.st_dev, info.st_ino)
