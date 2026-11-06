import base64
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import unittest.mock
import urllib.error
import urllib.request

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'portable'))
import accounting as a
import usage_service as u
from test_accounting import record,CONFIG,INVENTORY


def raw_api(entries):
    def fetch(base,route,params,**options):
        first,last,limit=int(params['start']),int(params['end']),int(params['limit'])
        # Match the real inclusive Loki API, not the engine's half-open contract.
        values=[[str(t),line] for t,line in entries if first<=t<=last][:limit]
        return {'status':'success','data':{'resultType':'streams','result':[{'stream':{'job':'fortigate-syslog'},'values':values}] if values else []}}
    return fetch


class UsageServiceTests(unittest.TestCase):
    def test_reader_half_open_bounds_and_adaptive_paging(self):
        entries=[(i,'synthetic') for i in range(1,2401)]
        rows,budget=u.LokiReader('http://fixture',fetch=raw_api(entries)).read(0,2401)
        self.assertEqual([x['timestamp_ns'] for x in rows],list(range(1,2401)))
        self.assertEqual(budget['records'],2400)
        rows,_=u.LokiReader('http://fixture',fetch=raw_api([(100,'start'),(200,'end')])).read(100,200)
        self.assertEqual([x['line'] for x in rows],['start'])

    def test_unsplittable_timestamp_and_request_budget_refuse_partial_success(self):
        with self.assertRaises(u.ServiceError):u.LokiReader('http://fixture',fetch=raw_api([(100,'fixture')]*2500)).read(0,200)
        with self.assertRaises(u.ServiceError):u.LokiReader('http://fixture',requests=1,fetch=raw_api([(x,'fixture') for x in range(2400)])).read(0,2400)

    def test_storage_limit_expires_oldest_raw_days_before_stopping(self):
        store=a.Store(':memory:',{**CONFIG,'accounting_max_bytes':1000,'accounting_ledger_days':3},INVENTORY,storage_format='compact')
        self.addCleanup(store.close)
        store.ingest([record('2026-10-17T12:00:00Z',sessionid='old'),record('2026-10-20T11:00:00Z')])
        engine=u.Engine(store,fetch=raw_api([]))
        cursor=a.timestamp_ns('2026-10-20T12:00:00Z')
        sizes=iter([5000,5000,5000,800,800])
        calls=[]
        original=store.prune
        def prune(now,days,summary):calls.append(days);return original(now,days,summary)
        with unittest.mock.patch.object(engine,'used_bytes',lambda:next(sizes)),unittest.mock.patch.object(store,'prune',prune):
            engine.fit_storage(cursor)
        self.assertEqual(calls,[2,1])
        report=store.report('2026-10-17T00:00:00Z','2026-10-21T00:00:00Z')
        self.assertEqual(report['totals']['upload_bytes'],200)
        with unittest.mock.patch.object(engine,'used_bytes',lambda:5000):
            with self.assertRaisesRegex(u.ServiceError,'accounting_storage_limit'):engine.fit_storage(cursor)
        with unittest.mock.patch.object(engine,'used_bytes',lambda:999),unittest.mock.patch.object(store,'prune') as untouched:
            engine.fit_storage(cursor)
        untouched.assert_not_called()
        self.assertGreater(u.Engine(store,fetch=raw_api([])).used_bytes(),0)

    def test_failed_poll_does_not_advance_checkpoint(self):
        store=a.Store(':memory:',CONFIG,INVENTORY);self.addCleanup(store.close)
        with store.db:store.set_state('checkpoint_ns',100)
        def fail(*args,**kwargs):raise TimeoutError('SECRET SHOULD NOT APPEAR')
        engine=u.Engine(store,fetch=fail)
        with self.assertRaises(TimeoutError):engine.poll(1000)
        self.assertEqual(store.state('checkpoint_ns'),100)
        self.assertNotIn('SECRET',u.safe_error(TimeoutError('SECRET')))

    def test_empty_query_is_processing_evidence_not_zero_traffic(self):
        store=a.Store(':memory:',CONFIG,INVENTORY);self.addCleanup(store.close)
        engine=u.Engine(store,fetch=raw_api([]))
        self.assertTrue(engine.poll(1792497660))
        result=engine.report('today',at='2026-10-20T12:01:00Z')
        self.assertEqual(result['status'],'UNAVAILABLE')
        self.assertIsNone(result['totals']['upload_bytes'])
        self.assertTrue(engine.last_poll_ok)

    def test_fixed_today_month_year_and_calendar_week_are_explicit(self):
        first,last=a.period_bounds('week','2026-10-20T12:01:00Z','UTC')
        self.assertEqual(a.iso(first),'2026-10-14T00:00:00+00:00')
        self.assertEqual(a.iso(last),'2026-10-20T12:01:00+00:00')
        try:a.tz('Asia/Riyadh')
        except ValueError:pass # Windows Python ships no IANA data unless tzdata is installed.
        else:
            first,_=a.period_bounds('week','2026-10-20T00:30:00Z','Asia/Riyadh')
            self.assertEqual(a.iso(first),'2026-10-13T21:00:00+00:00')
        first,_=a.period_bounds('year','2026-10-20T12:01:00Z','UTC')
        self.assertEqual(a.iso(first),'2026-01-01T00:00:00+00:00')
        first,_=a.period_bounds('month','2026-10-20T12:01:00Z','UTC')
        self.assertEqual(a.iso(first),'2026-10-01T00:00:00+00:00')
        first,_=a.period_bounds('today','2026-10-20T12:01:00Z','UTC')
        self.assertEqual(a.iso(first),'2026-10-20T00:00:00+00:00')

    def test_vlan_and_device_views_reconcile_to_the_same_unique_contributions(self):
        store=a.Store(':memory:',CONFIG,INVENTORY);self.addCleanup(store.close)
        store.ingest([record(),record(sessionid='11',srcintf='guests'),record(sessionid='12',mastersrcmac='02:00:00:00:00:02')])
        result=store.report('2026-10-20T00:00:00Z','2026-10-21T00:00:00Z')
        for field in ('upload_bytes','download_bytes'):
            self.assertEqual(sum(x[field] for x in result['devices']),result['totals'][field])
            self.assertEqual(sum(x[field] for x in result['vlans']),result['totals'][field])
        self.assertEqual(len(result['devices']),2)
        self.assertEqual(len(result['vlans']),2)

    def test_malformed_input_is_quarantined_without_stalling_valid_records(self):
        store=a.Store(':memory:',CONFIG,INVENTORY);self.addCleanup(store.close)
        invalid={'timestamp':'2026-10-20T12:00:00Z','line':'type="traffic" bad="unterminated'}
        outcome=store.ingest([record(),invalid],checkpoint=123)
        self.assertEqual(outcome['new_events'],1)
        self.assertEqual(outcome['rejected_input_records'],1)
        self.assertEqual(store.state('checkpoint_ns'),123)
        store.ingest([invalid],checkpoint=124)
        self.assertEqual(store.diagnostics()['parser_rejections'],1)

    def test_http_auth_protects_reports_inventory_and_metrics(self):
        store=a.Store(':memory:',CONFIG,INVENTORY);self.addCleanup(store.close)
        engine=u.Engine(store)
        with tempfile.TemporaryDirectory() as root:
            Path(root,'reporting.html').write_text('<h1>FortiLens</h1>')
            server=ThreadingHTTPServer(('127.0.0.1',0),u.handler(engine,'fixture-password-123456',root))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                base='http://127.0.0.1:'+str(server.server_port)
                for path in ('/api/report','/api/inventory','/metrics'):
                    with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(base+path)
                    self.assertEqual(caught.exception.code,401)
                auth='Basic '+base64.b64encode(b'admin:fixture-password-123456').decode()
                request=urllib.request.Request(base+'/api/report?period=today',headers={'Authorization':auth})
                with urllib.request.urlopen(request) as response:
                    result=json.load(response)
                    self.assertEqual(result['traffic_coverage'],'UNKNOWN')
                    self.assertIsNone(result['totals']['download_bytes'])
                    self.assertEqual(response.headers['Cache-Control'],'no-store')
            finally:
                server.shutdown();server.server_close();thread.join()

    def test_metrics_escape_source_text_and_do_not_invent_missing_usage(self):
        escaped=u.metric_line('fixture',1,{'hostname':'quote"slash\\newline\n'})
        self.assertEqual(escaped.count('\n'),1)
        self.assertIn('\\n',escaped)
        store=a.Store(':memory:',CONFIG,INVENTORY);self.addCleanup(store.close)
        engine=u.Engine(store)
        output=u.metrics(engine)
        self.assertNotIn('fortilens_usage_bytes{',output)
        self.assertIn('fortilens_accounting_traffic_coverage_known 0',output)


if __name__=='__main__':unittest.main()
