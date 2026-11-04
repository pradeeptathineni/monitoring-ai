"""Check exact timeline scope and readonly receiving behavior over accepted records."""

import hashlib
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from monitoring_ai.metrics import Pricing, Exchange
from monitoring_ai.server import Observatory, handler
from monitoring_ai.store import Store
from test_session_timeline import baseline


class TimelineIntegrationTests(unittest.TestCase):
    """Verify the API and CLI preserve one owner/revision/source/session boundary."""

    def test_pinned_http_and_readonly_cli_share_the_same_markers(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'events.sqlite';store=Store(path);store.ingest(baseline())
            digest=hashlib.sha256(path.read_bytes()).hexdigest();owner=Observatory(store,Pricing(),Exchange())
            server=ThreadingHTTPServer(('127.0.0.1',0),handler(owner));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            def get(path):
                client=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3);client.request('GET',path)
                response=client.getresponse();value=response.status,json.loads(response.read());client.close();return value
            try:
                self.assertEqual(get('/api/v1/capabilities')[1]['session_timeline'],'/api/v1/session-timeline')
                scope=f'source=synthetic-timeline&session=fixture-session&expected_identity={owner.identity}&expected_revision=5'
                status,value=get('/api/v1/session-timeline?'+scope);self.assertEqual(status,200)
                self.assertEqual(len(value['markers']),5)
                self.assertEqual(get('/api/v1/session-timeline?source=synthetic-timeline&session=fixture-session')[0],400)
                self.assertEqual(get('/api/v1/session-timeline?'+scope.replace('fixture-session','another-session'))[1]['markers'],[])
                self.assertEqual(get('/api/v1/session-timeline?'+scope.replace(owner.identity,'another-owner'))[0],409)
                self.assertEqual(get('/api/v1/session-timeline?'+scope+'&state=running')[0],400)
                command=[sys.executable,'-m','monitoring_ai','session-timeline','--db',str(path),'--source','synthetic-timeline','--session','fixture-session','--json']
                cli=json.loads(subprocess.run(command,capture_output=True,text=True,check=True).stdout)
                self.assertEqual(cli['markers'],value['markers'])
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),digest)
                missing=Path(folder)/'missing.sqlite';command[5]=str(missing)
                self.assertNotEqual(subprocess.run(command,capture_output=True).returncode,0)
                self.assertFalse(missing.exists())
                store.ingest([dict(baseline()[0],id='later-status',timestamp='2026-11-04T01:01:00Z')])
                self.assertEqual(get('/api/v1/session-timeline?'+scope)[0],409)
            finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
