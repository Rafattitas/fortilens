"""Management control-plane acceptance. All devices/credentials here are synthetic."""
from copy import deepcopy
import base64
import json
import argparse
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
try:
    from fastapi.testclient import TestClient
    import yaml
    from management.app import create_app,COOKIE
    from management.auth import Auth,password_hash,verify
    from management.common import Problem,atomic,load,setup
    from management.configuration import Configuration
    from management.models import Settings,Change
    from management.runtime import Docker,Operations,enabled_services,fortigate
except ImportError:
    raise unittest.SkipTest('Management tests require management/requirements-test.txt; the portable CLI remains dependency-free.')

TOKEN='synthetic-fortigate-token-never-return'
BOOTSTRAP='synthetic-bootstrap-0123456789-abcdef'
PASSWORD='Synthetic-admin-password-123'


class FakeDocker:
    """Records approved operations; never invokes an engine or a real device."""
    def __init__(self,c):self.c=c;self.calls=[];self.fail=False;self.down=False
    def validate(self,identity):
        self.calls.append(('validate',identity))
        folder,plan=self.c.plan(identity)
        compose=yaml.safe_load((folder/'candidate'/self.c.compose_name).read_text())
        assert 'grafana' in compose['services'] and 'prometheus-data' in compose['volumes']
    def run(self,args,timeout=180):
        self.calls.append(('command',list(args)))
        if self.down:raise Problem('docker_unavailable','Docker is unavailable.')
        return '[]' if 'inspect' in args else ''
    def command(self,*args):return ['docker','compose','-p',self.c.project,'-f',str(self.c.root/self.c.compose_name)]
    def start(self,enabled,disabled):
        self.calls.append(('start',list(enabled),list(disabled)))
        if self.fail:raise Problem('fixture_start_failed','Synthetic service startup failed.')
    def ready(self,enabled):return self.statuses()
    def statuses(self):
        if self.down:raise Problem('docker_unavailable','Docker is unavailable.')
        return [{'service':n,'status':'running','health':'healthy','id':'synthetic-'+n} for n in (self.c.compose() or {'services':{}})['services']]
    def http(self,service,port,path,**kwargs):
        if self.down:raise Problem('unavailable','Synthetic monitoring service is offline.')
        if service=='grafana':return {'database':'ok'}
        if service=='fortilens-usage':return {'last_poll_ok':False,'processing_lag_seconds':None}
        return {'status':'success','data':{'resultType':'vector','result':[]}}


def fake_device(settings,token,path,params,ca):
    if path.endswith('/status'):return {'status':'success','version':'test-FortiOS','serial':'FG-SYNTHETIC','token':token,'results':{'secret':token}}
    return {'status':'success','vdom':'root','results':[
        {'name':'uplink-a','vdom':'root','type':'physical','role':'wan','ip':'192.0.2.1/24','status':'up'},
        {'name':'staff','vdom':'root','type':'vlan','role':'lan','interface':'port1','vlanid':100,'ip':'198.51.100.1/24'}]}


class Helper:
    def __init__(self,operations):self.operations=operations
    def call(self,operation,data=None):return self.operations.dispatch(operation,data or {})


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        self.c=Configuration(self.base/'stack',self.base/'control',project='synthetic-management')
        self.docker=FakeDocker(self.c);self.ops=Operations(self.c,self.docker,fake_device)
        self.addCleanup(lambda:self.ops.executor.shutdown(wait=True))
        self.bootstrap=self.base/'web/bootstrap.token';atomic(self.bootstrap,(BOOTSTRAP+'\n').encode())
        self.app=create_app(self.base/'web',self.bootstrap,Helper(self.ops),origin='http://testserver')
        self.addCleanup(self.app.state.auth.close)
        self.client=TestClient(self.app);self.addCleanup(self.client.close)
        self.headers={'X-FortiLens-Request':'1','Origin':'http://testserver'}

    def administrator(self):
        r=self.client.post('/api/bootstrap',json={'username':'operator','password':PASSWORD,'confirmation':PASSWORD,'bootstrap_token':BOOTSTRAP},headers=self.headers)
        self.assertEqual(r.status_code,200,r.text)
        r=self.client.post('/api/login',json={'username':'operator','password':PASSWORD},headers=self.headers)
        self.assertEqual(r.status_code,200,r.text)
        self.csrf={**self.headers,'X-CSRF-Token':r.json()['csrf']}
        return r

    def save(self,**updates):
        config=self.c.view();settings=deepcopy(config['settings']);settings['fortigate_host']='192.0.2.1';settings.update(updates)
        body={'revision':config['revision'],'settings':settings}
        if not config['secret_status']['api_token_present']:body.update(api_token=TOKEN,replace_token=True)
        return self.c.save(body)

    def wait_job(self,job):
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            value=self.ops.dispatch('job',{'job_id':job['job_id']})
            if value['status'] in ('complete','failed'):return value
            time.sleep(.01)
        self.fail('Synthetic job did not complete')

    def existing(self):
        root=self.base/'existing'
        setup.render(setup.validate_config({'fortigate_host':'192.0.2.1','accounting_enabled':True,'portal_enabled':True}),root,TOKEN)
        c=Configuration(root,self.base/'adopt-control',project='existing-original-project')
        return c

    def test_fresh_portal_needs_no_monitoring_or_configuration(self):
        self.assertEqual(self.client.get('/').status_code,200)
        self.assertEqual(self.client.get('/health').json()['independent_of_monitoring'],True)
        self.assertFalse((self.c.root/'compose.yaml').exists())
        self.assertEqual(self.client.get('/api/settings').status_code,401)
        self.administrator()
        diagnostics=self.client.get('/api/diagnostics')
        self.assertEqual(diagnostics.status_code,200)
        self.assertTrue(diagnostics.json()['read_only'])

    def test_native_grafana_mount_preserves_router_and_plugin_urls(self):
        self.administrator()
        html='<base href="/grafana/"><script>window.settings={"appSubUrl":"/grafana","plugin":"public/plugins/grafana"}</script><a href="https://grafana.com/docs/grafana/latest/">Docs</a>'
        result={'status':200,'body':base64.b64encode(html.encode()).decode(),'content_type':'text/html'}
        with patch.object(self.app.state.helper,'call',return_value=result) as proxy:
            response=self.client.get('/grafana/d/fixture/?kiosk')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.text,html)
            self.assertEqual(proxy.call_args.args[1]['path'],'/grafana/d/fixture/?kiosk')
            response=self.client.get('/console/grafana/d/fixture/?kiosk',follow_redirects=False)
            self.assertEqual(response.status_code,307)
            self.assertEqual(response.headers['location'],'/grafana/d/fixture/?kiosk')
        self.assertEqual(self.ops.status()['links']['dashboard'],'/grafana/d/forti-monitor/?kiosk&theme=dark')

    def test_initial_html_stays_independent_and_never_exposes_session_data(self):
        with patch.object(self.app.state.helper,'call',side_effect=Problem('helper_offline','Synthetic offline helper.')) as helper:
            self.assertNotIn('data-initial-view=',self.client.get('/').text)
            self.administrator()
            response=self.client.get('/?view=devices')
            self.assertEqual(response.status_code,200)
            self.assertIn('data-initial-view="devices" src="/console/initial-view?view=devices"',response.text)
            self.assertNotIn(self.client.cookies[COOKIE],response.text)
            self.assertNotIn('data-initial-view=',self.client.get('/?view=settings').text)
            self.assertNotIn('data-initial-view=',self.client.get('/?view=%22%3E%3Cscript%3E').text)
            self.assertEqual(response.headers['cache-control'],'no-store')
            helper.assert_not_called()

    def test_initial_view_uses_only_authenticated_enabled_fixed_destinations(self):
        self.assertEqual(self.client.get('/console/initial-view',follow_redirects=False).status_code,401)
        self.administrator()
        with patch.object(self.app.state.helper,'call',return_value={'configured':False}):
            self.assertEqual(self.client.get('/console/initial-view',follow_redirects=False).status_code,503)
        view={'configured':True,'active_monitoring':{'accounting':True,'wan':True,'vlans':True}}
        with patch.object(self.app.state.helper,'call',return_value=view) as helper:
            response=self.client.get('/console/initial-view?view=devices',follow_redirects=False)
            self.assertEqual(response.status_code,307)
            self.assertEqual(response.headers['location'],'/console/?section=devices#overview')
            self.assertEqual(response.headers['x-frame-options'],'SAMEORIGIN')
            self.assertEqual(self.client.get('/console/initial-view?view=https://outside.invalid',follow_redirects=False).status_code,400)
            self.assertEqual(helper.call_count,1)
            view['active_monitoring']['accounting']=False
            self.assertEqual(self.client.get('/console/initial-view?view=quality',follow_redirects=False).status_code,503)

    def test_initial_monitoring_redirect_rejects_unexpected_destinations(self):
        self.administrator()
        view={'configured':True,'active_monitoring':{'wan':True}}
        status={'available':True,'path':'/grafana/d/fixture/?kiosk&theme=dark'}
        def result(operation,data=None):
            if operation=='view':return view
            self.assertEqual(operation,'console');self.assertEqual(data['path'],'/api/monitoring-status')
            return {'status':200,'body':base64.b64encode(json.dumps(status).encode()).decode()}
        with patch.object(self.app.state.helper,'call',side_effect=result):
            response=self.client.get('/console/initial-view',follow_redirects=False)
            self.assertEqual(response.status_code,307)
            self.assertEqual(response.headers['location'],status['path'])
            view['active_sla']='google'
            response=self.client.get('/console/initial-view',follow_redirects=False)
            self.assertEqual(response.headers['location'],status['path']+'&var-sla=google')
            for target in ('//outside.invalid/','/grafana/d/../admin/','/console/','/grafana/d/fixture/?redirect=https://outside.invalid'):
                status['path']=target
                self.assertEqual(self.client.get('/console/initial-view',follow_redirects=False).status_code,502)
            status['available']=False
            self.assertEqual(self.client.get('/console/initial-view',follow_redirects=False).status_code,503)
            status=[]
            self.assertEqual(self.client.get('/console/initial-view',follow_redirects=False).status_code,503)

    def test_hosted_reports_preserve_protected_routes_and_reject_style_input(self):
        self.administrator()
        html=(ROOT/'portable/reporting.html').read_text(encoding='utf-8')
        result={'status':200,'body':base64.b64encode(html.encode()).decode(),'content_type':'text/html'}
        with patch.object(self.app.state.helper,'call',return_value=result):
            response=self.client.get('/console/',params={'section':'devices'})
            self.assertEqual(response.status_code,200)
            self.assertIn("'/console/api/report?",response.text)
            self.assertIn("startsWith('/grafana/d/')",response.text)
            self.assertIn('#vlan-section{display:none!important}',response.text)
            from management.app import hosted_report
            self.assertEqual(response.text.count('id="managed-report-status"'),1)
            self.assertEqual(hosted_report(response.text),response.text)
            attack='</style><script>injected-style-input</script>'
            response=self.client.get('/console/',params={'section':attack})
            self.assertEqual(response.status_code,200)
            self.assertNotIn(attack,response.text)
            self.assertNotIn('#vlan-section{display:none!important}',response.text)
            self.assertEqual(response.headers['x-frame-options'],'SAMEORIGIN')

    def test_native_grafana_mount_keeps_auth_origin_and_write_boundaries(self):
        self.assertEqual(self.client.get('/grafana/api/user').status_code,401)
        self.administrator()
        self.assertEqual(self.client.post('/grafana/api/admin/users',json={},headers=self.csrf).status_code,403)
        result={'status':200,'body':base64.b64encode(b'{"results":{}}').decode(),'content_type':'application/json'}
        with patch.object(self.app.state.helper,'call',return_value=result):
            self.assertEqual(self.client.post('/grafana/api/ds/query',json={},headers={'Origin':'http://testserver'}).status_code,200)
            self.assertEqual(self.client.post('/grafana/api/ds/query',json={}).status_code,403)
            self.assertEqual(self.client.post('/grafana/api/ds/query',json={},headers={'Origin':'https://outside.invalid'}).status_code,403)

    def test_only_authenticated_fingerprinted_code_is_privately_cacheable(self):
        self.administrator()
        path='/grafana/public/build/runtime.1234abcdef56.js'
        result={'status':200,'body':base64.b64encode(b'/* static library fixture */').decode(),'content_type':'application/javascript'}
        with patch.object(self.app.state.helper,'call',return_value=result):
            self.assertEqual(self.client.get(path).headers['cache-control'],'private, max-age=86400, immutable')
            self.assertEqual(self.client.get('/grafana/api/user').headers['cache-control'],'no-store')
            result['content_type']='text/html'
            self.assertEqual(self.client.get(path).headers['cache-control'],'no-store')
            result.update(status=503,content_type='application/javascript')
            self.assertEqual(self.client.get(path).headers['cache-control'],'no-store')
            self.client.cookies.clear()
            response=self.client.get(path)
            self.assertEqual(response.status_code,401)
            self.assertEqual(response.headers['cache-control'],'no-store')

    def test_one_time_bootstrap_password_hash_and_no_default_credentials(self):
        r=self.client.post('/api/bootstrap',json={'username':'operator','password':PASSWORD,'confirmation':PASSWORD,'bootstrap_token':'wrong'},headers=self.headers)
        self.assertEqual(r.status_code,403)
        login=self.administrator()
        self.assertIn('HttpOnly',login.headers['set-cookie']);self.assertIn('SameSite=strict',login.headers['set-cookie'])
        row=self.app.state.auth.db.execute('SELECT password FROM admin').fetchone()[0]
        self.assertTrue(row.startswith('scrypt$'));self.assertNotIn(PASSWORD,row)
        self.assertFalse(self.bootstrap.exists())
        self.assertEqual(self.client.post('/api/bootstrap',json={'username':'other','password':PASSWORD,'confirmation':PASSWORD,'bootstrap_token':BOOTSTRAP},headers=self.headers).status_code,409)

    def test_csrf_origin_host_roles_and_logout(self):
        self.administrator()
        for headers in ({},self.headers,{**self.csrf,'Origin':'https://other.example'}):
            self.assertEqual(self.client.post('/api/logout',json={},headers=headers).status_code,403)
        self.assertEqual(self.client.get('/api/settings',headers={'Host':'attacker.example'}).status_code,403)
        saved=self.client.cookies.get(COOKIE)
        self.assertEqual(self.client.post('/api/logout',json={},headers=self.csrf).status_code,200)
        self.client.cookies.set(COOKIE,saved)
        self.assertEqual(self.client.get('/api/settings').status_code,401)
        self.assertEqual(self.client.get('/health').status_code,200)

    def test_non_admin_cannot_change_configuration(self):
        self.administrator()
        with self.app.state.auth.db:self.app.state.auth.db.execute("UPDATE admin SET role='viewer'")
        r=self.client.post('/api/configuration/apply',json={'revision':0,'plan_id':'a'*32,'confirm':True},headers=self.csrf)
        self.assertEqual(r.status_code,401)
        self.assertFalse(self.docker.calls)

    def test_secure_cookies_and_lan_http_rejected(self):
        with self.assertRaises(ValueError):create_app(self.base/'other',self.bootstrap,Helper(self.ops),origin='http://192.0.2.1:3030')
        other=self.base/'secure';atomic(other/'bootstrap.token',BOOTSTRAP.encode())
        app=create_app(other,other/'bootstrap.token',Helper(self.ops),origin='https://testserver')
        with TestClient(app,base_url='https://testserver') as client:
            headers={'X-FortiLens-Request':'1','Origin':'https://testserver'}
            client.post('/api/bootstrap',json={'username':'operator','password':PASSWORD,'confirmation':PASSWORD,'bootstrap_token':BOOTSTRAP},headers=headers)
            r=client.post('/api/login',json={'username':'operator','password':PASSWORD},headers=headers)
            self.assertIn('Secure',r.headers['set-cookie'])

    def test_validation_never_echoes_secrets_and_concurrency_conflicts(self):
        self.administrator();config=self.save()
        body={'revision':config['revision'],'settings':config['settings'],'api_token':TOKEN,'replace_token':True}
        body['settings']['api_port']=-1
        r=self.client.post('/api/settings',json=body,headers=self.csrf)
        self.assertEqual(r.status_code,422);self.assertNotIn(TOKEN,r.text)
        body['settings']['api_port']=443
        first=self.client.post('/api/settings',json=body,headers=self.csrf)
        self.assertEqual(first.status_code,200);self.assertNotIn(TOKEN,first.text)
        self.assertEqual(self.client.post('/api/settings',json=body,headers=self.csrf).status_code,409)
        self.assertNotIn(TOKEN,(self.c.control/'audit.jsonl').read_text())

    def test_api_token_replacement_is_explicit_and_insecure_tls_acknowledged(self):
        self.save();view=self.c.view()
        with self.assertRaises(Problem):self.c.save({'revision':view['revision'],'settings':view['settings'],'api_token':'new-token'})
        config=deepcopy(view['settings']);config['api_tls_verify']=False
        with self.assertRaises(Problem):self.c.save({'revision':view['revision'],'settings':config})
        saved=self.c.save({'revision':view['revision'],'settings':config,'acknowledge_insecure_tls':True})
        self.assertFalse(saved['settings']['api_tls_verify'])

    def test_read_only_device_testing_discovery_and_explicit_wan_confirmation(self):
        view=self.save();result=self.ops.dispatch('test',{'revision':view['revision']})
        self.assertNotIn(TOKEN,json.dumps(result));self.assertEqual(result['device']['serial'],'FG-SYNTHETIC')
        report=self.ops.dispatch('discover',{'revision':view['revision']})
        self.assertEqual({r['name'] for r in report['interfaces']},{'uplink-a','staff'})
        self.assertEqual(next(r for r in report['interfaces'] if r['name']=='uplink-a')['ip_address'],'192.0.2.1/24')
        settings=deepcopy(view['settings']);settings['wan_interfaces']=['uplink-a']
        with self.assertRaises(ValueError):Settings.model_validate(settings)
        settings['wan_confirmed']=True
        view=self.c.save({'revision':view['revision'],'settings':settings})
        # Saved revisions may change while the discovered connection identity stays the same.
        plan=self.ops.dispatch('prepare',{'revision':view['revision']})
        with patch.object(setup,'import_dashboard'),patch.object(setup,'connect_portal'):
            job=self.wait_job(self.ops.dispatch('apply',{'revision':view['revision'],'plan_id':plan['plan_id'],'confirm':True}))
        self.assertEqual(job['status'],'complete',job)

        import integration_live_fortigate as live
        arguments=['live','--config',str(self.c.root/'site.json'),'--token-file',str(self.c.root/'secrets/fortigate_api_token')]
        with patch('sys.argv',arguments),patch.object(live,'fortigate',fake_device),contextlib.redirect_stdout(io.StringIO()) as capture:
            self.assertEqual(live.main(),0)
        self.assertEqual(json.loads(capture.getvalue())['interface_count'],2)
        self.assertNotIn(TOKEN,capture.getvalue())
        with patch('sys.argv',arguments),patch.object(live,'fortigate',side_effect=Problem('api_permission_denied','Review read permissions.')),contextlib.redirect_stdout(io.StringIO()) as capture:
            self.assertEqual(live.main(),1)
        self.assertNotIn(TOKEN,capture.getvalue())

    def test_fresh_configuration_and_service_start_are_real_operations_not_ui_placeholders(self):
        view=self.save();plan=self.ops.dispatch('prepare',{'revision':view['revision']})
        folder,_=self.c.plan(plan['plan_id'])
        config=load(folder/'candidate/site.json')
        self.assertTrue(config['loki_receive_timestamps']);self.assertEqual(config['accounting_timezone'],'UTC')
        self.assertFalse((self.c.root/'compose.yaml').exists())
        with patch.object(setup,'import_dashboard') as dashboard,patch.object(setup,'connect_portal'):
            job=self.wait_job(self.ops.dispatch('apply',{'revision':view['revision'],'plan_id':plan['plan_id'],'confirm':True}))
        self.assertEqual(job['status'],'complete',job)
        dashboard.assert_called_once();self.assertTrue(self.c.view()['configured'])
        self.assertTrue(any(c[0]=='start' for c in self.docker.calls))
        self.assertFalse(self.c.view()['pending_changes'])

    def test_existing_secrets_dashboards_volumes_and_identity_are_preserved(self):
        c=self.existing();original={str(p.relative_to(c.root)):p.read_bytes() for p in c.root.rglob('*') if p.is_file()}
        sentinel=c.root/'accounting-data.sqlite';sentinel.write_bytes(b'synthetic persistent database: never migrate')
        value=c.view();value['settings']['site_name']='New company display name';value['settings']['retention']['loki_days']=90
        c.save({'revision':value['revision'],'settings':value['settings']})
        plan=c.prepare(c.state()['revision']);folder,_=c.plan(plan['plan_id'])
        c.install_files(plan['plan_id']);c.complete(plan['plan_id'])
        for name,data in original.items():
            if name.startswith(('secrets/','grafana/')):self.assertEqual((c.root/name).read_bytes(),data,name)
        self.assertEqual(sentinel.read_bytes(),b'synthetic persistent database: never migrate')
        before=yaml.safe_load(original['compose.yaml']);after=c.compose()
        self.assertEqual(before['volumes'],after['volumes'])
        for name,service in before['services'].items():
            self.assertEqual(service.get('image'),after['services'][name].get('image'))
            self.assertEqual(service.get('volumes'),after['services'][name].get('volumes'))
        c.restore(plan['plan_id'])
        for name,data in original.items():self.assertEqual((c.root/name).read_bytes(),data,name)

    def test_identity_and_retention_reductions_require_explicit_migration(self):
        c=self.existing();view=c.view()
        for field,value in [('accounting_timezone','Africa/Cairo'),('vdom','other'),('expected_devid','DIFFERENT')]:
            settings=deepcopy(view['settings']);settings[field]=value
            with self.assertRaises(Problem):c.save({'revision':view['revision'],'settings':settings})
        settings=deepcopy(view['settings']);settings['retention']['loki_days']=1
        with self.assertRaises(Problem):c.save({'revision':view['revision'],'settings':settings})

    def test_external_file_edits_and_path_traversal_fail_closed(self):
        view=self.save();plan=self.c.prepare(view['revision'])
        with self.assertRaises(Problem):self.c.plan('../outside')
        with self.assertRaises(Problem):self.ops.dispatch('shell',{'command':'whoami'})
        for path in ('/grafana/../api/admin','https://example.com/','/grafana/%2e%2e/admin'):
            with self.assertRaises(Problem):self.ops.dispatch('console',{'path':path})
        folder,_=self.c.plan(plan['plan_id']);(folder/'candidate/site.json').write_text('{}')
        with self.assertRaises(Problem):self.c.install_files(plan['plan_id'])
        self.assertFalse((self.c.root/'site.json').exists())

    def test_failed_apply_restores_config_and_keeps_portal_accessible(self):
        self.administrator();view=self.save();plan=self.ops.dispatch('prepare',{'revision':view['revision']});self.docker.fail=True
        job=self.wait_job(self.ops.dispatch('apply',{'revision':view['revision'],'plan_id':plan['plan_id'],'confirm':True}))
        self.assertEqual(job['status'],'failed');self.assertTrue(job['configuration_restored'])
        self.docker.down=True
        self.assertEqual(self.client.get('/health').status_code,200)
        self.assertEqual(self.client.get('/api/settings').status_code,200)
        self.assertEqual(self.client.get('/api/diagnostics').status_code,200)
        self.assertTrue((self.c.root/'compose.yaml').exists())  # Recovery files retained.

    def test_injection_and_unsupported_config_fields_rejected(self):
        for value in ('host; touch /tmp/pwned','$(whoami)','https://firewall','--project-name=other'):
            with self.assertRaises(ValueError):Settings(fortigate_host=value)
        with self.assertRaises(ValueError):Settings(fortigate_host='192.0.2.1',docker_socket='/var/run/docker.sock')
        with self.assertRaises(ValueError):Settings(fortigate_host='192.0.2.1',syslog_port=0)
        with self.assertRaises(Problem):fortigate(Settings(fortigate_host='127.0.0.1'),'synthetic','/api/v2/monitor/system/status')

    def test_disabled_collectors_keep_core_services_and_data(self):
        s=Settings(fortigate_host='192.0.2.1',monitoring={'wan':False,'sla':False,'interfaces':False,'server':False,'syslog':False,'accounting':False,'vlans':False})
        enabled,disabled=enabled_services(s,{'services':{n:{} for n in ('grafana','prometheus','loki','fortilens-portal')}})
        self.assertTrue({'grafana','prometheus','loki','fortilens-portal'}<=set(enabled))
        self.assertIn('fortilens-usage',disabled)

    def test_admin_recovery_revokes_sessions_without_changing_service_credentials(self):
        self.administrator();saved=self.c.secret_path.read_bytes() if self.c.secret_path.exists() else None
        self.app.state.auth.recover('New-synthetic-password-123')
        self.assertEqual(self.client.get('/api/session').status_code,401)
        self.assertFalse(self.client.get('/api/bootstrap/status').json()['required'])
        self.assertEqual(self.c.secret_path.read_bytes() if self.c.secret_path.exists() else None,saved)

    def test_login_rate_limit_and_session_survive_restart(self):
        self.administrator();saved=self.client.cookies.get(COOKIE)
        auth=Auth(self.base/'web',self.bootstrap)
        self.addCleanup(auth.close)
        self.assertEqual(auth.session(saved)['role'],'admin')
        for _ in range(10):
            with self.assertRaises(Problem):auth.login('operator','incorrect-password','synthetic-address')
        with self.assertRaises(Problem) as error:auth.login('operator',PASSWORD,'synthetic-address')
        self.assertEqual(error.exception.status,429)

    def test_stale_apply_secret_change_and_interrupted_job_recovery(self):
        view=self.save();plan=self.c.prepare(view['revision'])
        view['settings']['site_name']='Another saved revision'
        self.c.save({'revision':view['revision'],'settings':view['settings']})
        with self.assertRaises(Problem):self.c.install_files(plan['plan_id'])
        identity='a'*32
        atomic(self.c.control/'jobs'/(identity+'.json'),{'job_id':identity,'plan_id':plan['plan_id'],'status':'starting','events':[]})
        restarted=Operations(self.c,self.docker,fake_device)
        self.addCleanup(lambda:restarted.executor.shutdown(wait=True))
        recovered=restarted.dispatch('job',{'job_id':identity})
        self.assertEqual(recovered['status'],'failed');self.assertTrue(recovered['rollback_available'])
        self.assertEqual(restarted.status()['latest_job']['job_id'],identity)

    def test_occupied_monitoring_port_rejected_before_configuration_write(self):
        import socket
        self.save()
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1',0));occupied.listen()
            docker=Docker(self.c)
            with patch.object(docker,'statuses',return_value=[]),self.assertRaises(Problem) as error:
                docker.check_ports({'services':{'grafana':{'ports':['127.0.0.1:'+str(occupied.getsockname()[1])+':3000']}}})
            self.assertEqual(error.exception.code,'port_in_use')
        self.assertFalse((self.c.root/'compose.yaml').exists())

    def test_invalid_field_names_cannot_echo_a_submitted_secret(self):
        self.administrator();view=self.save()
        view['settings'][TOKEN]='invalid field'
        r=self.client.post('/api/settings',json={'revision':view['revision'],'settings':view['settings']},headers=self.csrf)
        self.assertEqual(r.status_code,422);self.assertNotIn(TOKEN,r.text)

    def test_service_unavailability_does_not_take_down_authentication(self):
        self.administrator()
        with patch.object(self.ops,'dispatch',side_effect=Problem('helper_unavailable','Management helper unavailable.',503)):
            self.assertEqual(self.client.get('/api/system').status_code,503)
            self.assertEqual(self.client.get('/api/session').status_code,200)
            self.assertEqual(self.client.get('/health').status_code,200)
            self.assertEqual(self.client.get('/').status_code,200)

    def test_installer_rejects_paths_injection_and_non_linux_hosts(self):
        from management.install import path_value,main,source_files
        for path in ('relative','/tmp/../etc','/tmp/a;id','/tmp/a\nExecStart=id'):
            with self.assertRaises(argparse.ArgumentTypeError):path_value(path)
        with patch('sys.argv',['install']),patch('sys.platform','win32'),self.assertRaises(SystemExit) as error:main()
        self.assertIn('Ubuntu',str(error.exception))
        manifest=source_files(ROOT);staged=self.base/'checkout'
        for name in manifest:
            file=staged/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes((ROOT/name).read_bytes())
        for name in ('portable/local-company/secrets/password','portable/.fortilens/site.json','management/private.txt'):
            file=staged/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_text('synthetic private value')
        self.assertEqual(source_files(staged),manifest)

    def test_short_monitoring_interval_produces_valid_accounting_timeout(self):
        view=self.save(monitoring_interval=15)
        plan=self.c.prepare(view['revision']);folder,_=self.c.plan(plan['plan_id'])
        prom=yaml.safe_load((folder/'candidate/prometheus/prometheus.yml').read_text())
        self.assertEqual(prom['global']['scrape_interval'],'15s')
        accounting=next(j for j in prom['scrape_configs'] if j['job_name']=='fortilens-accounting')
        self.assertEqual(accounting['scrape_timeout'],'14s')
        with self.assertRaises(ValueError):Settings(fortigate_host='192.0.2.1',grafana_port=3002,usage_port=3002)
        with self.assertRaises(ValueError):Settings(fortigate_host='192.0.2.1',syslog_port=3001)

    def legacy_existing(self):
        c=self.existing()
        auth=c.root/'secrets/fortigate-key.yaml'
        old=yaml.safe_load(auth.read_text());record=old.pop('https://192.0.2.1:443')
        record['probes']=['System','Interfaces'];old['https://192.0.2.1']=record
        auth.write_text(yaml.safe_dump(old))
        path=c.root/'prometheus/prometheus.yml';prom=yaml.safe_load(path.read_text())
        prom['global']={'scrape_interval':'15s','evaluation_interval':'15s'}
        prom['scrape_configs'][0]['static_configs'][0]['targets']=['https://192.0.2.1']
        prom['scrape_configs'][-1]['scrape_timeout']='10s'
        path.write_text(yaml.safe_dump(prom))
        compose=c.compose();compose['services']['alloy']['ports']=['1514:1514/tcp','12345:12345']
        (c.root/'compose.yaml').write_text(yaml.safe_dump(compose))
        # Simulate first registration after a CLI/manual legacy deployment.
        return Configuration(c.root,self.base/'legacy-control',project=c.project)

    def test_legacy_default_https_port_and_actual_interval_preserve_credentials(self):
        c=self.legacy_existing();view=c.view()
        self.assertTrue(view['secret_status']['api_token_present'])
        self.assertEqual(view['settings']['monitoring_interval'],15)
        before=(c.root/'secrets/fortigate-key.yaml').read_bytes()
        view['settings']['site_name']='Changed display only'
        saved=c.save({'revision':view['revision'],'settings':view['settings']})
        plan=c.prepare(saved['revision']);folder,_=c.plan(plan['plan_id'])
        self.assertNotIn('secrets/fortigate-key.yaml',plan['changed_files'])
        prom=yaml.safe_load((folder/'candidate/prometheus/prometheus.yml').read_text())
        self.assertEqual(prom['global'],{'scrape_interval':'15s','evaluation_interval':'15s'})
        self.assertEqual(prom['scrape_configs'][0]['static_configs'][0]['targets'],['https://192.0.2.1'])
        self.assertEqual((c.root/'secrets/fortigate-key.yaml').read_bytes(),before)
        view=c.view();view['settings']['syslog_port']=1515;view['settings']['syslog_protocol']='udp'
        saved=c.save({'revision':view['revision'],'settings':view['settings'],'api_token':'synthetic-replacement-token','replace_token':True})
        plan=c.prepare(saved['revision']);folder,_=c.plan(plan['plan_id'])
        auth=yaml.safe_load((folder/'candidate/secrets/fortigate-key.yaml').read_text())
        self.assertEqual(list(auth),['https://192.0.2.1'])
        self.assertEqual(auth['https://192.0.2.1'],{'token':'synthetic-replacement-token','probes':['System','Interfaces']})
        ports=yaml.safe_load((folder/'candidate/compose.yaml').read_text())['services']['alloy']['ports']
        self.assertEqual(ports,['1515:1514/udp','12345:12345'])
        self.assertEqual((c.root/'secrets/fortigate-key.yaml').read_bytes(),before)

    def test_ambiguous_https_credentials_are_not_guessed(self):
        c=self.legacy_existing();path=c.root/'secrets/fortigate-key.yaml'
        auth=yaml.safe_load(path.read_text());auth['https://192.0.2.1:443']={'token':'another-synthetic-token'}
        path.write_text(yaml.safe_dump(auth))
        with self.assertRaises(Problem):c.token()
        self.assertFalse(c.view()['secret_status']['api_token_present'])


if __name__=='__main__':unittest.main()
