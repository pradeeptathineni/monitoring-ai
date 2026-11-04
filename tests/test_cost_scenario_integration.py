"""Keep hypothetical pricing behind explicit selection and owner/revision pins."""

import hashlib
import http.client
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from monitoring_ai.metrics import Pricing, Exchange
from monitoring_ai.scenario_config import read_scenario
from monitoring_ai.server import Observatory, handler
from monitoring_ai.store import Store


# Synthetic rates deliberately make no claim about any provider's actual prices

def prices(path):
    path.write_text(json.dumps(dict(schema='monitoring.pricing.v1',rates=[dict(provider='example',model='fixture-model',currency='USD',
        **{'from':'2026-11-01T00:00:00Z','until':'2026-12-01T00:00:00Z'},provenance='synthetic-alternative',
        input='2',cache_read='1',cache_write='3',output='4')])))
    return read_scenario(path)


def event():
    return dict(schema='monitoring.event.v1',source='synthetic',id='fixture-usage',session='fixture-session',entity='fixture-session',
        parent=None,entity_type='session',kind='usage',timestamp='2026-11-03T10:00:00Z',provider='example',model='fixture-model',
        surface='test',project='fixture',tokens=dict(input=1000000,cache_read=0,cache_write=0,output=0),
        cost=dict(amount='1',currency='USD',basis='estimated',provenance='synthetic-recorded'))


class CostScenarioIntegrationTests(unittest.TestCase):
    """Check the usable API and readonly terminal path over one unchanged ledger."""

    def test_owner_revision_and_scenario_pins_preserve_original_costs(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);store=Store(root/'events.sqlite');store.ingest([event()])
            book=prices(root/'prices.json');owner=Observatory(store,Pricing(),Exchange(),cost_scenario_pricing=book)
            server=ThreadingHTTPServer(('127.0.0.1',0),handler(owner));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            def get(path):
                client=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3);client.request('GET',path)
                response=client.getresponse();result=response.status,json.loads(response.read());client.close();return result
            before=hashlib.sha256((root/'events.sqlite').read_bytes()).hexdigest()
            try:
                self.assertEqual(get('/api/v1/capabilities')[1]['cost_scenario'],'/api/v1/cost-scenario')
                self.assertEqual(get('/api/v1/cost-scenario')[0],400)
                snapshot=get('/api/v1/snapshot')[1];config=snapshot['cost_scenario_status']
                suffix=f"expected_identity={owner.identity}&expected_revision=1&expected_scenario={config['id']}"
                status,value=get('/api/v1/cost-scenario?'+suffix);self.assertEqual(status,200)
                self.assertEqual(value['baseline']['estimated']['amount'],'1')
                self.assertEqual(value['alternative']['amount'],'2')
                self.assertEqual(value['comparable']['difference'],'1')
                self.assertEqual(value['scenario']['kind'],'counterfactual')
                self.assertEqual(get('/api/v1/cost-scenario?'+suffix+'&state=running')[0],400)
                self.assertEqual(get('/api/v1/cost-scenario?'+suffix.replace(owner.identity,'other-owner'))[0],409)
                self.assertEqual(get('/api/v1/cost-scenario?'+suffix.replace(config['id'],'0'*64))[0],409)
                self.assertEqual(hashlib.sha256((root/'events.sqlite').read_bytes()).hexdigest(),before)
                self.assertEqual(store.read()[1][0]['cost']['amount'],'1')
                store.ingest([dict(event(),id='another-usage')])
                self.assertEqual(get('/api/v1/cost-scenario?'+suffix)[0],409)
            finally:server.shutdown();server.server_close();thread.join()

    def test_cli_does_not_initialize_or_modify_ledgers_and_configuration_is_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);rates=root/'rates.json';prices(rates);path=root/'events.sqlite';Store(path).ingest([event()])
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            command=[sys.executable,'-m','monitoring_ai','cost-scenario','--db',str(path),'--scenario-pricing',str(rates),'--json']
            result=json.loads(subprocess.run(command,capture_output=True,text=True,check=True).stdout)
            self.assertEqual(result['baseline']['estimated']['amount'],'1')
            self.assertEqual(result['alternative']['amount'],'2')
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),digest)
            self.assertNotEqual(subprocess.run(command+['--expected-revision','999'],capture_output=True).returncode,0)
            missing=root/'missing.sqlite';command[5]=str(missing)
            self.assertNotEqual(subprocess.run(command,capture_output=True).returncode,0)
            self.assertFalse(missing.exists())
            too_big=root/'large.json';too_big.write_bytes(b' '* (1024*1024+1))
            with self.assertRaises(ValueError):read_scenario(too_big)
            if hasattr(os,'mkfifo'):
                fifo=root/'pipe';os.mkfifo(fifo)
                with self.assertRaises(ValueError):read_scenario(fifo)


if __name__=='__main__':unittest.main()
