"""Source manifest tests using synthetic temporary files only."""

import dataclasses
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from monitoring_ai import adapters
from monitoring_ai.sources import Source, SourceManifestError, load_sources

SCHEMA = "monitoring.sources.v1"


class LoadSourcesTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.alias_root = temp.name
        self.root = Path(temp.name).resolve()

    def raw(self, content, name="sources.json"):
        path = self.root / name
        path.write_bytes(content)
        return path

    def manifest(self, entries, **top):
        document = {"schema": SCHEMA, "sources": entries}
        document.update(top)
        return self.raw(json.dumps(document).encode())

    def entry(self, name="events.jsonl", **fields):
        values = {"id": "alpha", "format": "normalized", "path": str(self.root / name)}
        values.update(fields)
        return values

    def rejects(self, manifest):
        with self.assertRaises(SourceManifestError) as caught:
            load_sources(manifest)
        message = str(caught.exception)
        for private in (str(self.root), self.alias_root, "SECRET"):
            self.assertNotIn(private, message)
        return caught.exception

    def test_normalized_source_delegates_without_project(self):
        manifest = self.manifest([self.entry(), self.entry("b.jsonl", id="beta", project=None)])
        alpha, beta = load_sources(manifest)
        self.assertEqual(alpha, Source("alpha", "normalized", self.root / "events.jsonl", None))
        self.assertIsNone(beta.project)
        with mock.patch.object(adapters, "load", return_value=["event"]) as load:
            self.assertEqual(alpha.load(), ["event"])
            alpha.load(live=False)
        self.assertEqual(load.call_args_list, [
            mock.call(self.root / "events.jsonl", "normalized", "alpha", None, live=True),
            mock.call(self.root / "events.jsonl", "normalized", "alpha", None, live=False),
        ])

    def test_native_sources_delegate_with_project_alias(self):
        manifest = self.manifest([
            self.entry("claude.jsonl", id="claude-main", format="claude", project="web-app"),
            self.entry("codex.jsonl", id="codex.cli", format="codex", project="api@v2"),
        ])
        sources = load_sources(manifest)
        self.assertIsInstance(sources, tuple)
        self.assertEqual(
            [(s.id, s.format, s.project) for s in sources],
            [("claude-main", "claude", "web-app"), ("codex.cli", "codex", "api@v2")],
        )
        with mock.patch.object(adapters, "load", return_value=[]) as load:
            for source in sources:
                source.load()
        self.assertEqual(load.call_args_list, [
            mock.call(self.root / "claude.jsonl", "claude", "claude-main", "web-app", live=True),
            mock.call(self.root / "codex.jsonl", "codex", "codex.cli", "api@v2", live=True),
        ])

    def test_source_is_frozen_and_repr_hides_path(self):
        source = Source("alpha", "claude", Path("/private/SECRET/session.jsonl"), "web")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            source.path = Path("/elsewhere")
        text = repr(source)
        self.assertNotIn("SECRET", text)
        self.assertNotIn("path", text)
        self.assertIn("alpha", text)
        self.assertIn("web", text)

    def test_missing_source_is_allowed_and_fails_only_when_loaded(self):
        (source,) = load_sources(self.manifest([self.entry("later.jsonl")]))
        self.assertEqual(source.path, self.root / "later.jsonl")
        with self.assertRaises(OSError):
            source.load()

    def test_validation_never_opens_source_data(self):
        (self.root / "present.jsonl").write_bytes(b"\xff not json SECRET\n")
        manifest = self.manifest([
            self.entry("present.jsonl"),
            self.entry("absent.jsonl", id="beta"),
        ])
        with (
            mock.patch("os.open", wraps=os.open) as opened,
            mock.patch.object(adapters, "read_jsonl", side_effect=AssertionError),
            mock.patch.object(adapters, "load", side_effect=AssertionError),
        ):
            sources = load_sources(manifest)
        self.assertEqual([s.id for s in sources], ["alpha", "beta"])
        self.assertEqual({Path(c.args[0]).name for c in opened.call_args_list}, {"sources.json"})

    def test_ids_and_project_aliases_must_be_labels(self):
        bad = ["", "two words", "a/b", "SECRET/../x", "x" * 161, 7, None, ["alpha"], True]
        for value in bad:
            with self.subTest(id=value):
                self.rejects(self.manifest([self.entry(id=value)]))
            with self.subTest(project=value):
                self.rejects(self.manifest([self.entry(format="claude", project=value)]))
        (source,) = load_sources(self.manifest([
            self.entry(id="a" * 160, format="codex", project="A.b_c:d@e-f"),
        ]))
        self.assertEqual((source.id, source.project), ("a" * 160, "A.b_c:d@e-f"))

    def test_native_requires_project_and_normalized_refuses_override(self):
        native = self.entry(format="claude")
        self.rejects(self.manifest([native]))
        self.rejects(self.manifest([self.entry(format="codex", project=None)]))
        self.rejects(self.manifest([self.entry(project="web")]))
        self.rejects(self.manifest([self.entry(project="")]))

    def test_manifest_shape_is_exact(self):
        good = self.entry()
        without = lambda key: {k: v for k, v in good.items() if k != key}
        documents = [
            [],
            SCHEMA,
            {"schema": SCHEMA},
            {"sources": [good]},
            {"schema": SCHEMA, "sources": [good], "version": 1},
            {"schema": "monitoring.sources.v2", "sources": [good]},
            {"schema": None, "sources": [good]},
            {"schema": SCHEMA, "sources": {"alpha": good}},
            {"schema": SCHEMA, "sources": []},
            {"schema": SCHEMA, "sources": [str(self.root / "events.jsonl")]},
            {"schema": SCHEMA, "sources": [{**good, "label": "x"}]},
            {"schema": SCHEMA, "sources": [without("path")]},
            {"schema": SCHEMA, "sources": [without("id")]},
            {"schema": SCHEMA, "sources": [without("format")]},
            {"schema": SCHEMA, "sources": [{**good, "format": "jsonl"}]},
            {"schema": SCHEMA, "sources": [{**good, "format": "Claude", "project": "p"}]},
            {"schema": SCHEMA, "sources": [{**good, "format": 1}]},
        ]
        for document in documents:
            with self.subTest(document=document):
                self.rejects(self.raw(json.dumps(document).encode()))

    def test_source_count_limit(self):
        entries = [self.entry(f"s{i}.jsonl", id=f"s{i}") for i in range(33)]
        self.rejects(self.manifest(entries))
        sources = load_sources(self.manifest(entries[:32]))
        self.assertEqual([s.id for s in sources], [f"s{i}" for i in range(32)])

    def test_duplicate_ids_and_paths_are_rejected(self):
        self.rejects(self.manifest([self.entry("a.jsonl"), self.entry("b.jsonl")]))
        self.rejects(self.manifest([self.entry("a.jsonl"), self.entry("a.jsonl", id="beta")]))

    def test_parent_aliases_are_canonicalized_and_deduplicated(self):
        real = self.root / "real"
        real.mkdir()
        alias = self.root / "alias"
        alias.symlink_to(real, target_is_directory=True)
        (source,) = load_sources(self.manifest([self.entry(path=str(alias / "a.jsonl"))]))
        self.assertEqual(source.path, real / "a.jsonl")
        self.rejects(self.manifest([
            self.entry(path=str(real / "a.jsonl")),
            self.entry(id="beta", path=str(alias / "a.jsonl")),
        ]))
        # System temporary directories may themselves be aliases, such as macOS /tmp
        self.rejects(self.manifest([
            self.entry("x.jsonl"),
            self.entry(id="beta", path=str(Path(self.alias_root) / "x.jsonl")),
        ]))

    def test_hard_links_to_one_file_are_duplicates(self):
        first = self.root / "first.jsonl"
        first.write_text("")
        os.link(first, self.root / "second.jsonl")
        self.rejects(self.manifest([
            self.entry("first.jsonl"),
            self.entry("second.jsonl", id="beta"),
        ]))

    def test_paths_must_be_absolute_local_file_targets(self):
        directory = self.root / "SECRET-dir"
        directory.mkdir()
        values = [
            "SECRET.jsonl",
            "./SECRET.jsonl",
            "~/SECRET.jsonl",
            str(self.root / "~SECRET.jsonl"),
            "file:///SECRET.jsonl",
            "https://example.com/SECRET.jsonl",
            str(self.root / "SECRET\x00.jsonl"),
            str(directory) + "/",
            str(directory) + "/.",
            str(directory) + "/../SECRET.jsonl",
            "/",
            "",
            "/" + "S" * 5000,
            None,
            42,
            ["/SECRET"],
        ]
        for value in values:
            with self.subTest(path=value):
                self.rejects(self.manifest([self.entry(path=value)]))

    def test_existing_symlink_or_nonregular_sources_are_refused(self):
        target = self.root / "target.jsonl"
        target.write_text("")
        (self.root / "SECRET-link.jsonl").symlink_to(target)
        (self.root / "SECRET-dangling.jsonl").symlink_to(self.root / "nowhere.jsonl")
        (self.root / "SECRET-dir").mkdir()
        names = ["SECRET-link.jsonl", "SECRET-dangling.jsonl", "SECRET-dir"]
        if hasattr(os, "mkfifo"):
            os.mkfifo(self.root / "SECRET-fifo")
            names.append("SECRET-fifo")
        for name in names:
            with self.subTest(name=name):
                self.rejects(self.manifest([self.entry(name)]))
        self.assertEqual(len(load_sources(self.manifest([self.entry("target.jsonl")]))), 1)

    def test_manifest_must_be_an_explicit_regular_file(self):
        real = self.manifest([self.entry()])
        link = self.root / "SECRET-link.json"
        link.symlink_to(real)
        directory = self.root / "SECRET-dir"
        directory.mkdir()
        candidates = [link, directory, self.root / "SECRET-missing.json", str(real) + "/", b"/x", 7]
        if hasattr(os, "mkfifo"):
            fifo = self.root / "SECRET-fifo.json"
            os.mkfifo(fifo)  # Must be refused without blocking for a writer
            candidates.append(fifo)
        for candidate in candidates:
            with self.subTest(candidate=candidate if isinstance(candidate, int) else os.path.basename(candidate)):
                self.rejects(candidate)
        self.assertEqual(len(load_sources(real)), 1)

    def test_manifest_parent_alias_is_accepted(self):
        config = self.root / "config"
        config.mkdir()
        (config / "sources.json").write_text(json.dumps({"schema": SCHEMA, "sources": [self.entry()]}))
        alias = self.root / "config-alias"
        alias.symlink_to(config, target_is_directory=True)
        self.assertEqual(load_sources(alias / "sources.json")[0].id, "alpha")
        self.manifest([self.entry(id="beta")])
        self.assertEqual(load_sources(Path(self.alias_root) / "sources.json")[0].id, "beta")

    def test_manifest_size_limit(self):
        content = json.dumps({"schema": SCHEMA, "sources": [self.entry()]}).encode()
        limit = 64 * 1024
        self.assertEqual(len(load_sources(self.raw(content + b" " * (limit - len(content))))), 1)
        self.rejects(self.raw(content + b" " * (limit + 1 - len(content))))

    def test_manifest_must_be_strict_utf8_json(self):
        path = json.dumps(str(self.root / "events.jsonl"))
        head = '{"schema":"' + SCHEMA + '","sources":['
        entry = '{"id":"alpha","format":"normalized","path":' + path + '}'
        valid = (head + entry + "]}").encode()
        self.assertEqual(len(load_sources(self.raw(valid))), 1)
        contents = [
            b"",
            b"{",
            b"SECRET",
            valid[:-1],
            b"\xef\xbb\xbf" + valid,
            valid.decode().encode("utf-16"),
            b'{"schema":"\xff","sources":[]}',
            (head + entry + '],"schema":"' + SCHEMA + '"}').encode(),
            (head + entry.replace('"id":"alpha"', '"id":"alpha","id":"alpha"') + "]}").encode(),
            (head + entry[:-1] + ',"project":NaN}]}').encode(),
            b"[" * 60000,
        ]
        for content in contents:
            with self.subTest(content=content[:40]):
                self.rejects(self.raw(content))


if __name__ == "__main__":
    unittest.main()
