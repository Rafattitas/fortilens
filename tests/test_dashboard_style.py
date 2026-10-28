from copy import deepcopy
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'portable'))
import setup
import dashboard_style as style
from usage_quality import enhance_dashboard


class DashboardStyleTests(unittest.TestCase):
    def source(self):return enhance_dashboard(setup.load(ROOT/'portable/templates/dashboard.json'))

    def test_visual_transform_preserves_every_query_scope_and_time_default(self):
        source=self.source();candidate=style.polish_dashboard(source)
        self.assertEqual(source,self.source())
        for name,p in source['spec']['elements'].items():self.assertEqual(p['spec']['data'],candidate['spec']['elements'][name]['spec']['data'])
        self.assertEqual(source['spec']['variables'],candidate['spec']['variables'])
        self.assertEqual(source['spec']['timeSettings'],candidate['spec']['timeSettings'])
        self.assertEqual(style.polish_dashboard(candidate),candidate)

    def test_every_panel_has_one_layout_reference_and_repetition_is_preserved(self):
        candidate=style.polish_dashboard(self.source())
        rows=candidate['spec']['layout']['spec']['rows']
        refs=[i['spec']['element']['name'] for r in rows for i in r['spec']['layout']['spec']['items']]
        self.assertEqual(set(refs),set(candidate['spec']['elements']))
        self.assertEqual(len(refs),len(set(refs)))
        self.assertEqual(sum('repeat' in r['spec'] for r in rows),2)
        for row in rows:
            for item in row['spec']['layout']['spec']['items']:
                self.assertLessEqual(item['spec']['x']+item['spec']['width'],24)
        monthly=next(r for r in rows if r['spec']['title']=='Device usage / This month')
        self.assertTrue(monthly['spec']['collapse'])

    def test_custom_panels_are_not_silently_dropped(self):
        source=self.source();source['spec']['elements']['panel-100']=deepcopy(source['spec']['elements']['panel-1'])
        with self.assertRaises(ValueError):style.polish_dashboard(source)

    def test_existing_installation_gets_a_local_visual_candidate_only(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'installation'
            with contextlib.redirect_stdout(io.StringIO()):setup.render({'fortigate_host':'fw.example.test'},path,'FAKE_FIXTURE_TOKEN')
            original=(path/'grafana/dashboard.json').read_bytes()
            with patch.object(setup,'grafana_request',side_effect=AssertionError('No live mutation')),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(setup.main(['prepare-dashboard-style','--output',str(path)]),0)
            self.assertEqual((path/'grafana/dashboard.json').read_bytes(),original)
            self.assertEqual(len(list((path/'grafana').glob('dashboard.visual-*.json'))),1)

    def test_accounting_is_opt_in_and_generates_private_bounded_service(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'installation'
            with contextlib.redirect_stdout(io.StringIO()):setup.render({'fortigate_host':'fw.example.test','accounting_enabled':True},path,'FAKE_FIXTURE_TOKEN')
            compose=setup.load(path/'compose.yaml');service=compose['services']['fortilens-usage']
            self.assertEqual(service['ports'],['127.0.0.1:3002:8080'])
            self.assertTrue(service['read_only'])
            self.assertEqual(service['mem_limit'],'192m')
            self.assertIn('usage-data',compose['volumes'])
            self.assertNotIn('FAKE_FIXTURE_TOKEN',(path/'site.json').read_text())
            self.assertTrue((path/'usage/reporting.html').is_file())
            self.assertIn('use_incoming_timestamp = false',(path/'alloy/config.alloy').read_text())
            self.assertTrue(setup.load(path/'site.json')['loki_receive_timestamps'])


if __name__=='__main__':unittest.main()
