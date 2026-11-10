from copy import deepcopy
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'portable'))
import setup
import dashboard_style as style

LOKI_USAGE='sort_desc(\n  sum by (mastersrcmac, srcip, srcname) (\n    sum_over_time(\n      {job="fortigate-syslog"}\n      | logfmt\n      | unwrap bytes [$__range])))'
LOKI_QUALITY='sum by (usage_quality) (count_over_time(\n  {job="fortigate-syslog"}\n  | logfmt --strict\n  | label_format usage_quality=`x`\n  [$__range]\n))'


def loki_panel(pid,title,expr):
    return {'kind':'Panel','spec':{'id':pid,'title':title,'links':[],'data':{'kind':'QueryGroup','spec':{
        'queries':[{'kind':'PanelQuery','spec':{'refId':'A','hidden':False,'query':{'kind':'DataQuery','group':'loki','version':'v0',
            'datasource':{'name':'fm-loki'},'spec':{'expr':expr,'queryType':'instant'}}}}],'transformations':[],'queryOptions':{'timeFrom':'7d'}}},
        'vizConfig':{'kind':'VizConfig','group':'table','version':'13.2.2','spec':{'options':{},'fieldConfig':{'defaults':{},'overrides':[]}}}}}


def previous_release(dashboard):
    """A dashboard as releases up to v0.5.0 generated it, with the retired panels and rows."""
    old=deepcopy(dashboard);elements=old['spec']['elements']
    for pid,title in ((1,'Recorded device usage / Today'),(41,'Recorded device usage / Last 7 days'),(2,'Recorded device usage / This month')):
        elements['panel-'+str(pid)]=loki_panel(pid,title,LOKI_USAGE)
    for pid,title in ((43,'Today'),(44,'Last 7 Days'),(45,'This Month')):
        elements['panel-'+str(pid)]=loki_panel(pid,'Received Usage Records — '+title,LOKI_QUALITY)
    elements['panel-42']={'kind':'Panel','spec':{'id':42,'title':'Reporting Periods & Evidence','links':[],
        'data':{'kind':'QueryGroup','spec':{'queries':[],'transformations':[],'queryOptions':{}}},
        'vizConfig':{'kind':'VizConfig','group':'text','version':'13.2.2','spec':{'options':{'mode':'markdown','content':'Help'}}}}}
    rows=old['spec']['layout']['spec']['rows']
    rows[4:4]=[style.row('Device usage / Today',[style.item(1,0,0,24,10)]),
        style.row('Device usage / Last 7 days',[style.item(41,0,0,24,12)],collapsed=True),
        style.row('Device usage / This month',[style.item(2,0,0,24,12)],collapsed=True)]
    rows+=[style.row('Usage Data Quality / Received records',[style.item(43,0,0,24,8),style.item(44,0,8,24,8),style.item(45,0,16,24,8)],collapsed=True),
        style.row('Help / Reporting periods & evidence',[style.item(42,0,0,24,9)],collapsed=True)]
    return old


def layout_names(dashboard):
    return [i['spec']['element']['name'] for r in dashboard['spec']['layout']['spec']['rows'] for i in r['spec']['layout']['spec']['items']]


class DashboardStyleTests(unittest.TestCase):
    def source(self):return setup.load(ROOT/'portable/templates/dashboard.json')

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
        titles=[r['spec']['title'] for r in rows]
        self.assertFalse([t for t in titles if t.startswith(('Device usage','Usage Data Quality','Help /'))])

    def test_no_generated_panel_scans_raw_logs(self):
        # Raw-log scans over whole periods outran the console gateway; the
        # console's Reports and Data quality pages read the accounting store.
        dashboard=setup.make_dashboard(setup.validate_config({'fortigate_host':'fw.example.test'}))
        self.assertFalse({'panel-'+str(i) for i in style.RETIRED_IDS}&set(dashboard['spec']['elements']))
        groups={q['spec']['query']['group'] for e in dashboard['spec']['elements'].values() for q in e['spec']['data']['spec']['queries']}
        self.assertEqual(groups,{'prometheus'})
        self.assertIn('Reports',dashboard['spec']['elements']['panel-46']['spec']['vizConfig']['spec']['options']['content'])

    def test_custom_panels_are_not_silently_dropped(self):
        source=self.source();source['spec']['elements']['panel-100']=deepcopy(source['spec']['elements']['panel-3'])
        with self.assertRaises(ValueError):style.polish_dashboard(source)

    def test_previous_release_dashboard_loses_only_the_retired_panels(self):
        current=style.polish_dashboard(self.source());old=previous_release(current)
        old['spec']['elements']['panel-100']=deepcopy(old['spec']['elements']['panel-3'])
        old['spec']['elements']['panel-100']['spec']['id']=100
        old['spec']['layout']['spec']['rows'][-1]['spec']['layout']['spec']['items'].append(style.item(100,0,9,24,5))
        old['spec']['layout']['spec']['rows'][1]['spec']['title']='Operator renamed overview'
        before=deepcopy(old);result,removed,kept=style.retire_usage_panels(old)
        self.assertEqual(old,before)
        self.assertEqual(removed,sorted('panel-'+str(i) for i in style.RETIRED_IDS));self.assertEqual(kept,[])
        self.assertEqual(set(result['spec']['elements']),set(current['spec']['elements'])|{'panel-100'})
        # The help row still holds the operator's own panel, so only its retired item goes.
        self.assertEqual(layout_names(result),[n for n in layout_names(old) if n not in removed])
        titles=[r['spec']['title'] for r in result['spec']['layout']['spec']['rows']]
        self.assertIn('Help / Reporting periods & evidence',titles);self.assertIn('Operator renamed overview',titles)
        self.assertFalse([t for t in titles if t.startswith(('Device usage','Usage Data Quality'))])
        for key in ('variables','timeSettings','annotations','links','tags'):self.assertEqual(result['spec'].get(key),old['spec'].get(key))
        for name,element in old['spec']['elements'].items():
            if name not in removed:self.assertEqual(result['spec']['elements'][name],element)
        self.assertEqual(style.retire_usage_panels(result),(result,[],[]))

    def test_edited_retired_panels_are_kept(self):
        old=previous_release(style.polish_dashboard(self.source()));elements=old['spec']['elements']
        elements['panel-41']['spec']['title']='Weekly usage for finance'
        elements['panel-2']['spec']['data']['spec']['queries'][0]['spec']['query']['group']='prometheus'
        elements['panel-42']['spec']['data']['spec']['queries']=[{'kind':'PanelQuery'}]
        result,removed,kept=style.retire_usage_panels(old)
        self.assertEqual(kept,['panel-2','panel-41','panel-42'])
        self.assertEqual(removed,['panel-1','panel-43','panel-44','panel-45'])
        self.assertTrue({'panel-2','panel-41','panel-42'}<=set(layout_names(result)))
        with self.assertRaises(ValueError):style.polish_dashboard(old)

    def test_generated_header_text_follows_and_an_edited_header_is_kept(self):
        old=previous_release(style.polish_dashboard(self.source()))
        options=old['spec']['elements']['panel-46']['spec']['vizConfig']['spec']['options']
        def header(dashboard):return dashboard['spec']['elements']['panel-46']['spec']['vizConfig']['spec']['options']['content']
        options['content']=style.PREVIOUS_HEADER_CONTENT
        self.assertEqual(header(style.retire_usage_panels(old)[0]),style.HEADER_CONTENT)
        options['content']=style.PREVIOUS_HEADER_CONTENT+' Call the network desk first.'
        self.assertEqual(header(style.retire_usage_panels(old)[0]),options['content'])
        # Nothing retired, nothing touched.
        current=style.polish_dashboard(self.source())
        current['spec']['elements']['panel-46']['spec']['vizConfig']['spec']['options']['content']=style.PREVIOUS_HEADER_CONTENT
        self.assertEqual(style.retire_usage_panels(current)[0],current)

    def test_prepare_style_from_a_previous_release_drops_retired_panels(self):
        old=previous_release(style.polish_dashboard(self.source()))
        self.assertEqual(style.polish_dashboard(old),style.polish_dashboard(self.source()))

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


class SavedDashboardTests(unittest.TestCase):
    """The running dashboard: only the retired panels are removed, through Grafana's update API."""

    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.p=Path(folder.name)/'installation'
        with contextlib.redirect_stdout(io.StringIO()):setup.render({'fortigate_host':'fw.example.test'},self.p,'FAKE_FIXTURE_TOKEN')
        self.c=setup.load(self.p/'site.json')
        generated=setup.load(self.p/'grafana/dashboard.json')
        self.live={'apiVersion':generated['apiVersion'],'kind':generated['kind'],
            'metadata':{'name':'forti-monitor','resourceVersion':'7','generation':3,'annotations':{'grafana.app/folder':'operations'}},
            'spec':previous_release(generated)['spec']}
        self.calls=[]

    def grafana(self,missing=False):
        def request(p,c,path,method='GET',body=None,timeout=100):
            self.calls.append((method,path,deepcopy(body)))
            if method=='GET':
                if missing:raise urllib.error.HTTPError(path,404,'Not Found',None,None)
                return deepcopy(self.live)
            if method=='PUT':
                self.live=deepcopy(body);return {'metadata':{'name':'forti-monitor','resourceVersion':'8'}}
            raise AssertionError('Unexpected '+method)
        return patch.object(setup,'grafana_request',side_effect=request)

    def backups(self):return list((self.p/'grafana').glob('dashboard.before-retired-panels-*.json'))

    def test_retired_panels_are_removed_and_everything_else_is_sent_back(self):
        original=deepcopy(self.live)
        with self.grafana(),contextlib.redirect_stdout(io.StringIO()):result=setup.retire_saved_panels(self.p,self.c)
        self.assertEqual(result['removed'],sorted('panel-'+str(i) for i in style.RETIRED_IDS))
        self.assertEqual([c[0] for c in self.calls],['GET','PUT'])
        sent=self.calls[1][2]
        self.assertEqual(self.calls[1][1],setup.DASHBOARD_ROUTE+'/forti-monitor')
        self.assertEqual(sent['metadata'],original['metadata'])  # resourceVersion guards concurrent saves.
        self.assertEqual(sent['spec'],style.retire_usage_panels(original)[0]['spec'])
        self.assertEqual(sent['spec']['variables'],original['spec']['variables'])
        [backup]=self.backups();self.assertEqual(setup.load(backup),original)
        if os.name=='posix':self.assertEqual(backup.stat().st_mode&0o777,0o600)
        # Applying again changes nothing.
        self.calls.clear()
        with self.grafana(),contextlib.redirect_stdout(io.StringIO()):self.assertEqual(setup.retire_saved_panels(self.p,self.c)['removed'],[])
        self.assertEqual([c[0] for c in self.calls],['GET']);self.assertEqual(len(self.backups()),1)

    def test_import_keeps_an_existing_dashboard_and_only_retires_panels(self):
        with self.grafana(),contextlib.redirect_stdout(io.StringIO()):setup.import_dashboard(self.p,self.c)
        self.assertEqual([c[0] for c in self.calls],['GET','PUT'])

    def test_a_missing_dashboard_is_not_recreated_by_retirement(self):
        with self.grafana(missing=True),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(setup.retire_saved_panels(self.p,self.c),{'removed':[],'kept':[]})
        self.assertEqual([c[0] for c in self.calls],['GET']);self.assertEqual(self.backups(),[])

    def test_another_dashboard_under_the_uid_is_never_changed(self):
        self.live['spec']['tags']=['someone-else']
        with self.grafana(),self.assertRaises(setup.SetupError):setup.retire_saved_panels(self.p,self.c)
        self.assertEqual([c[0] for c in self.calls],['GET']);self.assertEqual(self.backups(),[])


if __name__=='__main__':unittest.main()
