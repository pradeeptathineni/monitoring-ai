from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from monitoring_ai.demo import demo_quotas
from monitoring_ai.metrics import Exchange, Pricing
from monitoring_ai.quota_sources import codex_book, quota_file, read_codex_quota
from monitoring_ai.server import Observatory
from monitoring_ai.store import Store


class QuotaSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def executable(self, body):
        path = self.root / "fake-codex"
        path.write_text(f"#!{sys.executable}\n" + body)
        path.chmod(0o700)
        return path

    def test_native_reader_uses_only_account_methods_and_discards_extra_account_fields(self):
        log = self.root / "methods.jsonl"
        raw = dict(rateLimitsByLimitId={"codex": dict(
            primary=dict(usedPercent=40, windowDurationMins=300, resetsAt=1793690000), secondary=None,
        )}, accountId="discard-account", accessToken="discard-extra-field")
        script = f"""import json,sys
for line in sys.stdin:
    message=json.loads(line)
    with open({str(log)!r},'a') as stream:stream.write(json.dumps(message)+'\\n')
    if message.get('id')==1:print(json.dumps({{'id':1,'result':{{'userAgent':'fixture'}}}}),flush=True)
    elif message.get('id')==2:print(json.dumps({{'id':2,'result':{raw!r}}}),flush=True)
"""
        book = read_codex_quota(executable=self.executable(script), source="fixture", account="alias")
        self.assertEqual(book["snapshots"][0]["windows"][0]["used_percent"], 40)
        methods = [json.loads(line)["method"] for line in log.read_text().splitlines()]
        self.assertEqual(methods, ["initialize", "initialized", "account/rateLimits/read"])
        self.assertNotIn("discard-account", json.dumps(book))
        self.assertNotIn("discard-extra-field", json.dumps(book))

    def test_authoritative_empty_map_does_not_revive_legacy_quota(self):
        raw = dict(rateLimitsByLimitId={}, rateLimits=dict(primary=dict(usedPercent=99)))
        book = codex_book(raw, captured_at="2026-11-02T20:00:00Z")
        self.assertEqual(book["snapshots"][0]["windows"], [])
        with self.assertRaises(ValueError):
            codex_book({"rateLimitsByLimitId": []}, captured_at="2026-11-02T20:00:00Z")

    def test_timeout_reaps_its_own_process_and_leaves_unrelated_process_running(self):
        pid_file = self.root / "child.pid"
        program = self.executable(f"import os,time\nopen({str(pid_file)!r},'w').write(str(os.getpid()))\ntime.sleep(20)\n")
        unrelated = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(20)"], start_new_session=True)
        try:
            started = time.monotonic()
            with self.assertRaises(ValueError):
                read_codex_quota(executable=program, timeout=1)
            self.assertLess(time.monotonic() - started, 4)
            self.assertIsNone(unrelated.poll())
            with self.assertRaises(ProcessLookupError):
                os.kill(int(pid_file.read_text()), 0)
        finally:
            unrelated.terminate()
            unrelated.wait(timeout=3)

    def test_quota_file_keeps_capture_time_and_rejects_symlink_and_extra_data(self):
        book = demo_quotas(datetime(2026, 11, 2, 20, tzinfo=timezone.utc))
        path = self.root / "quota.json"
        path.write_text(json.dumps(book))
        self.assertEqual(quota_file(path)["snapshots"][0]["captured_at"], "2026-11-02T20:00:00.000Z")
        link = self.root / "link.json"
        link.symlink_to(path)
        with self.assertRaises(ValueError):
            quota_file(link)
        path.write_text(json.dumps(dict(book, private="not accepted")))
        with self.assertRaises(ValueError):
            quota_file(path)

    def test_failed_quota_refresh_retains_dated_observations(self):
        book = demo_quotas(datetime(2026, 11, 2, 20, tzinfo=timezone.utc))
        owner = Observatory(Store(self.root / "store.sqlite"), Pricing(), Exchange(), quota_loader=lambda: book)
        owner.refresh_quotas()
        first = owner.read({})["insights"]["quotas"]
        self.assertEqual(first["read_status"]["state"], "following")
        owner.quota_loader = lambda: (_ for _ in ()).throw(ValueError("fixture failure"))
        owner.refresh_quotas()
        retained = owner.read({"project": "unmatched-project"})["insights"]["quotas"]
        self.assertEqual(retained["read_status"]["state"], "unavailable")
        self.assertEqual(retained["windows"], first["windows"])
        self.assertEqual(retained["windows"][0]["captured_at"], "2026-11-02T20:00:00.000Z")

    def test_slow_quota_read_does_not_block_usage_snapshots(self):
        entered, release = threading.Event(), threading.Event()
        def slow():
            entered.set()
            release.wait(3)
            return demo_quotas()
        owner = Observatory(Store(self.root / "store.sqlite"), Pricing(), Exchange(), quota_loader=slow)
        worker = threading.Thread(target=owner.refresh_quotas)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            started = time.monotonic()
            value = owner.read({})
            self.assertLess(time.monotonic() - started, .5)
            self.assertEqual(value["insights"]["quotas"]["read_status"]["state"], "reading")
        finally:
            release.set()
            worker.join(timeout=3)

    def test_server_termination_waits_for_inflight_owned_quota_process_cleanup(self):
        marker = self.root / "inflight.pid"
        script = f"""import os,json,sys,time
for line in sys.stdin:
    message=json.loads(line)
    if message.get('id')==1:print(json.dumps({{'id':1,'result':{{}}}}),flush=True)
    elif message.get('id')==2:
        open({str(marker)!r},'w').write(str(os.getpid()))
        time.sleep(1)
        print(json.dumps({{'id':2,'result':{{'rateLimitsByLimitId':{{}}}}}}),flush=True)
"""
        program = self.executable(script)
        server = subprocess.Popen([sys.executable, "-m", "monitoring_ai", "serve", "--db", str(self.root / "server.sqlite"),
            "--port", "0", "--codex-quota", "--codex-executable", str(program)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 4
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(marker.exists())
            started = time.monotonic()
            server.terminate()
            server.communicate(timeout=7)
            self.assertEqual(server.returncode, 0)
            self.assertGreater(time.monotonic() - started, .5)
            with self.assertRaises(ProcessLookupError):
                os.kill(int(marker.read_text()), 0)
        finally:
            if server.poll() is None:
                server.kill()
                server.communicate(timeout=3)


if __name__ == "__main__":
    unittest.main()
