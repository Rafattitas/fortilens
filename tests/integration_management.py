"""Actual management -> helper -> Docker acceptance with synthetic TLS metadata.

Linux only. Every engine resource is uniquely named and disposable. Never accepts
an existing installation path. No real FortiGate or production data is used.
"""
import argparse
import base64
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from management.common import atomic,load,setup
from management.configuration import Configuration
from management.runtime import Operations

TOKEN='synthetic-integration-token'
BOOT='synthetic-one-time-bootstrap-0123456789-abcdef'
PASSWORD='Synthetic-test-password-12345'


def port():
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


def run(*args):
    r=subprocess.run(list(map(str,args)),text=True,capture_output=True)
    if r.returncode:raise AssertionError('Lab command failed: '+str(args[0])+' '+r.stderr[-2000:])
    return r.stdout.strip()


def wait(action,seconds=180):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        try:
            value=action()
            if value:return value
        except (OSError,ValueError,urllib.error.URLError):pass
        time.sleep(1)
    raise AssertionError('Isolated management readiness timed out')


def certificate(folder,host):
    cert,key=folder/'cert.pem',folder/'key.pem'
    run('openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',key,'-out',cert,'-days','1',
        '-subj','/CN=synthetic-management','-addext','subjectAltName=IP:'+host,'-addext','basicConstraints=critical,CA:TRUE')
    key.chmod(0o600)
    return cert,key


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installed',action='store_true',help='Ephemeral CI VM only: use the real install.sh-created system services')
    args=parser.parse_args()
    if sys.platform!='linux':raise SystemExit('Linux Docker Compose v2 and OpenSSL are required')
    if args.installed and (os.environ.get('GITHUB_ACTIONS')!='true' or os.geteuid()!=0):
        raise SystemExit('--installed is restricted to a root process on an ephemeral GitHub Actions VM')
    if not args.installed and os.geteuid()==0:raise SystemExit('Run the container isolation lab as a nonroot Docker-enabled test user')
    base=Path(tempfile.mkdtemp(prefix='fortilens-management-lab-')).resolve();base.chmod(0o755)
    project='fortilens-web-test-'+os.urandom(5).hex()
    image='fortilens-management-test:'+project
    webname=project+'-web';helper=None;fixture=None;web_id=None;log=None
    c=None;command=None
    try:
        bridge=run('docker','network','inspect','bridge','--format','{{(index .IPAM.Config 0).Gateway}}')
        certdir=base/'api';certdir.mkdir()
        cert,key=certificate(certdir,bridge)
        class Fixture(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                if self.headers.get('Authorization')!='Bearer '+TOKEN:
                    self.send_response(401);self.end_headers();return
                route=urlsplit(self.path).path
                if route=='/api/v2/monitor/system/status':data={'status':'success','serial':'FG-FIXTURE','version':'synthetic'}
                elif route=='/api/v2/cmdb/system/interface':data={'status':'success','vdom':'root','results':[
                    {'name':'uplink-a','vdom':'root','type':'physical','role':'wan','ip':'192.0.2.1/24','status':'up'},
                    {'name':'staff','vdom':'root','type':'vlan','role':'lan','vlanid':100,'interface':'port1','ip':'198.51.100.1/24'}]}
                else:self.send_response(404);self.end_headers();return
                body=json.dumps(data).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        fixture=ThreadingHTTPServer((bridge,0),Fixture)
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(cert,key)
        fixture.socket=ctx.wrap_socket(fixture.socket,server_side=True)
        threading.Thread(target=fixture.serve_forever,daemon=True).start()
        if args.installed:
            registry=load('/etc/fortilens/registry.json')
            root,control=Path(registry['installation']),Path(registry['control'])
            c=Configuration(root,control,project=registry['project'],compose_file=registry['compose_file'])
            project=c.project;origin='http://127.0.0.1:'+str(registry['management_port'])
            web=Path('/var/lib/fortilens/web');bootstrap=(web/'bootstrap.token').read_text().strip()
            context=None
        else:
            c=Configuration(base/'stack',base/'control',project=project)
            runtime=base/'runtime';runtime.mkdir(mode=0o755)
            secret=runtime/'helper.key';atomic(secret,os.urandom(32).hex().encode())
            web=base/'web';web.mkdir();atomic(web/'bootstrap.token',BOOT.encode());bootstrap=BOOT
            registry={'installation':str(c.root),'control':str(c.control),'project':project,'compose_file':'compose.yaml','socket':str(runtime/'helper.sock'),
                      'key':str(secret),'web_uid':os.getuid(),'web_gid':os.getgid()}
            atomic(base/'registry.json',registry)
            log=(base/'helper.log').open('w')
            helper=subprocess.Popen([sys.executable,'-m','management.helper','--registry',str(base/'registry.json')],cwd=ROOT,stdout=log,stderr=log)
            wait(lambda:(runtime/'helper.sock').is_socket())
            tlsdir=base/'webtls';tlsdir.mkdir();webcert,webkey=certificate(tlsdir,'127.0.0.1')
            origin='https://127.0.0.1:'+str(port());context=ssl.create_default_context(cafile=webcert)
            run('docker','build','-f',ROOT/'management/Dockerfile','-t',image,ROOT)
            web_id=run('docker','run','-d','--name',webname,'--label','fortilens.test='+project,'--user',f'{os.getuid()}:{os.getgid()}',
                '--read-only','--cap-drop','ALL','--security-opt','no-new-privileges:true','--tmpfs','/tmp:rw,noexec,nosuid,size=16m',
                '-p','127.0.0.1:'+str(urlsplit(origin).port)+':3030','-v',str(web)+':/web',
                '-v',str(runtime)+':/runtime:ro','-v',str(tlsdir)+':/tls:ro',image,'--data','/web','--bootstrap-file','/web/bootstrap.token',
                '--helper-socket','/runtime/helper.sock','--helper-key','/runtime/helper.key','--host','0.0.0.0','--port','3030',
                '--origin',origin,'--tls-cert','/tls/cert.pem','--tls-key','/tls/key.pem')
        cookie='';csrf=''
        def api(path,body=None,expected=200):
            nonlocal cookie,csrf
            headers={'X-FortiLens-Request':'1','Origin':origin}
            if cookie:headers['Cookie']=cookie
            if csrf:headers['X-CSRF-Token']=csrf
            if body is not None:headers['Content-Type']='application/json'
            req=urllib.request.Request(origin+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
            try:r=urllib.request.urlopen(req,context=context,timeout=60)
            except urllib.error.HTTPError as error:r=error
            with r:
                raw=r.read();status=r.status
                if r.headers.get('Set-Cookie'):cookie=r.headers['Set-Cookie'].split(';')[0]
            assert status==expected,(path,status,raw[:800])
            assert TOKEN.encode() not in raw and PASSWORD.encode() not in raw,'Secret in response'
            data=json.loads(raw) if raw else {}
            if data.get('csrf'):csrf=data['csrf']
            return data
        wait(lambda:api('/health')['independent_of_monitoring'])
        api('/api/settings',expected=401)
        api('/api/bootstrap',{'username':'operator','password':PASSWORD,'confirmation':PASSWORD,'bootstrap_token':bootstrap})
        api('/api/login',{'username':'operator','password':PASSWORD})
        view=api('/api/settings');assert view['configured'] is False
        s=view['settings'];s.update(site_name='Synthetic management lab',fortigate_host=bridge,api_port=fixture.server_port,
            grafana_port=port(),usage_port=port(),syslog_port=port(),expected_devid='FG-FIXTURE',monitoring_interval=15)
        # First test must expose the self-signed certificate problem, without
        # disabling verification or returning its request's secret.
        view=api('/api/settings',{'revision':view['revision'],'settings':s,'api_token':TOKEN,'replace_token':True})
        bad=api('/api/connection/test',{'revision':view['revision']},expected=400);assert bad['error']=='api_tls_failed',bad
        view=api('/api/settings',{'revision':view['revision'],'settings':s,'custom_ca':cert.read_text(),'replace_ca':True})
        assert api('/api/connection/test',{'revision':view['revision']})['tls_verified']
        inventory=api('/api/interfaces/discover',{'revision':view['revision']});assert len(inventory['interfaces'])==2
        s.update(wan_interfaces=['uplink-a'],wan_confirmed=True)
        view=api('/api/settings',{'revision':view['revision'],'settings':s})
        plan=api('/api/configuration/prepare',{'revision':view['revision']})
        def job_wait(job):
            def check():
                result=api('/api/jobs/'+job['job_id'])
                if result['status']=='failed':raise AssertionError(result)
                return result if result['status']=='complete' else None
            return wait(check,300)
        job=job_wait(api('/api/configuration/apply',{'revision':view['revision'],'plan_id':plan['plan_id'],'confirm':True}))
        assert len([r for r in job['services'] if r['status']=='running'])==8
        assert api('/api/settings')['configured']
        command=['docker','compose','-p',c.project,'-f',str(c.root/c.compose_name)]
        def docker(*arguments):return run(*command,*arguments)
        worker=docker('ps','-q','fortilens-usage')
        run('docker','exec',worker,'python','-c',"from pathlib import Path;Path('/data/management-preservation.txt').write_text('synthetic persistence marker')")
        mounts_before={r['Destination']:r['Source'] for r in json.loads(run('docker','inspect',worker))[0]['Mounts'] if r['Type']=='volume'}
        def preserved_data():
            worker=docker('ps','-q','fortilens-usage')
            mounts={r['Destination']:r['Source'] for r in json.loads(run('docker','inspect',worker))[0]['Mounts'] if r['Type']=='volume'}
            assert mounts==mounts_before
            assert run('docker','exec',worker,'python','-c',"from pathlib import Path;print(Path('/data/management-preservation.txt').read_text())")=='synthetic persistence marker'
            assert run('docker','exec',worker,'python','-c',"import sqlite3;print(sqlite3.connect('file:/data/usage.sqlite?mode=ro',uri=True).execute('PRAGMA quick_check').fetchone()[0])")=='ok'
        def console(path):
            req=urllib.request.Request(origin+'/console/'+path,headers={'Cookie':cookie})
            with urllib.request.urlopen(req,context=context,timeout=30) as r:return r.status,r.read()
        assert console('grafana/api/health')[0]==200
        assert console('api/report?period=today')[0]==200
        # Adopt the actual running project into a new registry without init.
        adopted=Configuration(c.root,base/'adopt-control',project=c.project)
        assert adopted.view()['configured']
        before_compose=c.compose();before_secrets={p.name:p.read_bytes() for p in (c.root/'secrets').iterdir() if p.is_file()}
        dashboard=setup.grafana_request(c.root,load(c.root/'site.json'),'/apis/dashboard.grafana.app/v2/namespaces/default/dashboards/forti-monitor')
        before_dashboard=dashboard['metadata']['resourceVersion']
        # A normal Settings edit does not require the live FortiGate to stay up.
        fixture.shutdown();fixture.server_close();fixture=None
        view=api('/api/settings');view['settings']['site_name']='Updated display name'
        view=api('/api/settings',{'revision':view['revision'],'settings':view['settings']})
        plan=api('/api/configuration/prepare',{'revision':view['revision']})
        job_wait(api('/api/configuration/apply',{'revision':view['revision'],'plan_id':plan['plan_id'],'confirm':True}))
        assert all((c.root/'secrets'/name).read_bytes()==data for name,data in before_secrets.items())
        after=c.compose();assert before_compose['volumes']==after['volumes']
        for name,service in before_compose['services'].items():assert service.get('volumes')==after['services'][name].get('volumes')
        dashboard=setup.grafana_request(c.root,load(c.root/'site.json'),'/apis/dashboard.grafana.app/v2/namespaces/default/dashboards/forti-monitor')
        assert dashboard['metadata']['resourceVersion']==before_dashboard
        preserved_data()
        assert console('api/report?period=today')[0]==200 # Gateway session refresh after recreation.
        job_wait(api('/api/configuration/rollback',{'revision':view['revision'],'plan_id':plan['plan_id'],'confirm':True}))
        preserved_data()
        # Management stays usable with core monitoring deliberately stopped.
        docker('stop','fortilens-portal','grafana','loki')
        assert api('/health')['independent_of_monitoring']
        assert api('/api/settings')['configured']
        diagnostic=api('/api/diagnostics');assert diagnostic['monitoring']['status']!='PASS'
        if web_id:
            inspect=json.loads(run('docker','inspect',web_id))[0]
            assert inspect['Config']['User'] not in ('','0','root')
            assert all('docker.sock' not in m['Destination'] and str(c.root) not in m['Source'] for m in inspect['Mounts'])
        print(json.dumps({'result':'PASS','services_started':8,'fresh_setup':True,'tls_verified':True,'adoption_preserved':True,
            'secrets_preserved':True,'dashboard_preserved':True,'rollback_kept_volumes':True,'management_survived_grafana_loki_failure':True,
            'source':'synthetic TLS API only; no production installation or real FortiGate tested'},indent=2))
    finally:
        if web_id:subprocess.run(['docker','rm','-f',webname],capture_output=True)
        if helper:helper.terminate();helper.wait(timeout=15)
        if log:log.close()
        if fixture:fixture.shutdown();fixture.server_close()
        if command:
            # Only the explicitly disposable test project created above.
            subprocess.run([*command,'down','--volumes','--remove-orphans'],capture_output=True)
            subprocess.run(['docker','image','rm','fortilens-usage:'+c.project],capture_output=True)
        if web_id:subprocess.run(['docker','image','rm',image],capture_output=True)
        shutil.rmtree(base)


if __name__=='__main__':main()
