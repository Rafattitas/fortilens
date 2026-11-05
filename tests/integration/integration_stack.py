"""Real Docker pipeline with synthetic logs, private volumes and loopback ports.

Never points at a user's installation. Requires Linux Docker Compose v2/OpenSSL.
"""
import base64
import contextlib
from datetime import datetime,timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'portable'))
spec=importlib.util.spec_from_file_location('lab_setup',ROOT/'portable/setup.py')
s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
import upgrade
import alerts


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


def replay_processed(usage,password,sent):
    # Unchanged totals prove nothing until accounting has polled past the replay.
    wait_for(lambda:(request(usage,'/api/diagnostics',password).get('checkpoint_ns') or 0)>sent+5*10**9)


def wait_for(action,seconds=150):
    deadline=time.monotonic()+seconds
    while True:
        try:
            value=action()
            if value:return value
        except (OSError,ValueError,KeyError,urllib.error.URLError):pass
        if time.monotonic()>=deadline:raise AssertionError('Integration readiness/acceptance timeout')
        time.sleep(2)


def request(base,path,password=None):
    headers={}
    if password:headers['Authorization']='Basic '+base64.b64encode(('admin:'+password).encode()).decode()
    req=urllib.request.Request(base+path,headers=headers)
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=15) as response:
        body=response.read()
        return body.decode() if path=='/metrics' else json.loads(body)


def traffic_fields(stamp,session,interface,mac,sent,received):
    return f'devid="FG-FIXTURE" vd="root" type="traffic" subtype="forward" logid="0000000020" sessionid="{session}" srcip="192.0.2.{session}" dstip="198.51.100.1" srcport="1234" dstport="443" proto="6" srcintf="{interface}" dstintf="uplink-a" srcintfrole="lan" dstintfrole="wan" mastersrcmac="{mac}" srcname="fixture-{session}" sentdelta="{sent}" rcvddelta="{received}" sentbyte="{sent}" rcvdbyte="{received}" duration="60" eventtime="{stamp}"'


def native_udp_pipeline(temporary,certificate):
    """A second disposable project checks the optional native receiver end to end."""
    root=temporary/'native-installation'
    command=None
    try:
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
            sock.bind(('127.0.0.1',0));udp_port=sock.getsockname()[1]
        config=s.validate_config({'site_name':'Synthetic native UDP integration','fortigate_host':'fixture-api','api_port':8443,
            'api_ca_file':str(certificate),'api_tls_verify':True,'vdom':'root','sla':'fixture-check',
            'wan_interfaces':['uplink-a'],'confirmed_wan_interfaces':['uplink-a'],
            'grafana_bind':'127.0.0.1','grafana_port':free_port(),'syslog_bind':'127.0.0.1','syslog_port':udp_port,
            'syslog_protocol':'udp','syslog_format':'fortigate','accounting_enabled':True,'expected_devid':'FG-FIXTURE',
            'usage_bind':'127.0.0.1','usage_port':free_port(),'accounting_poll_seconds':5,'accounting_settle_seconds':1})
        with contextlib.redirect_stdout(io.StringIO()):
            s.render(config,root,'synthetic-integration-token','synthetic-grafana-password')
        config=s.load(root/'site.json');compose=s.load(root/'compose.yaml')
        compose['services']['fixture-api']={'image':'fortilens-usage:local','entrypoint':['python','/fixture/fixture_api.py'],
            'volumes':[str(temporary)+':/fixture:ro'],'platform':'linux/amd64'}
        # Resolve the allowed fixture sender only after its private DNS record exists.
        compose['services']['fortilens-syslog']['depends_on'].append('fixture-api')
        prom_port=free_port()
        compose['services']['prometheus']['ports']=[f'127.0.0.1:{prom_port}:9090']
        (root/'compose.yaml').write_text(s.dumps(compose))
        command=s.compose_command(root)
        def docker(*args,**kwargs):return subprocess.run([*command,*args],cwd=root,check=True,**kwargs)
        docker('config','--quiet')
        docker('build','fortilens-usage','fortilens-syslog')
        docker('run','--rm','--no-deps','alloy','validate','/etc/alloy/config.alloy')
        docker('run','--rm','--no-deps','--entrypoint','promtool','prometheus','check','config','/etc/prometheus/prometheus.yml')
        docker('up','-d','--no-build')
        usage=f"http://127.0.0.1:{config['usage_port']}"
        password=(root/'secrets/usage_admin_password').read_text().strip()
        def receiver_request(path):
            # Internal HTTP remains unpublished; the fixture sees only generic counters.
            result=subprocess.run([*command,'exec','-T','fixture-api','python','-c',
                "import sys,urllib.request; print(urllib.request.urlopen('http://fortilens-syslog:8080'+sys.argv[1],timeout=5).read().decode())",path],
                cwd=root,capture_output=True,text=True,check=False,timeout=15)
            if result.returncode:return None
            return result.stdout if path=='/metrics' else json.loads(result.stdout)
        wait_for(lambda:receiver_request('/health'))
        wait_for(lambda:request(usage,'/api/diagnostics',password).get('inventory_available'))
        time.sleep(2)
        now=datetime.now(timezone.utc);stamp=int(now.timestamp()*1e9)
        prefix='<134>date='+now.strftime('%Y-%m-%d')+' time='+now.strftime('%H:%M:%S')+' '
        first=prefix+traffic_fields(stamp,10,'staff','02:00:00:00:00:01',1000000000,2000000000)
        second=prefix+traffic_fields(stamp,11,'guests','02:00:00:00:00:02',300000000,700000000)
        # The published UDP port must reject the host as a different sender.
        rejected_before=receiver_request('/health')['rejected_reasons']['source']
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
            sock.sendto(first.encode(),('127.0.0.1',config['syslog_port']))
        def source_rejected():
            health=receiver_request('/health')
            return health if health and health['rejected_reasons']['source']>rejected_before else None
        wait_for(source_rejected,30)
        def send(*payloads):
            # The native receiver permits the resolved FortiGate address. Send
            # from that fixture container, never pretend host loopback is it.
            docker('exec','-T','fixture-api','python','-c',
                "import base64,socket,sys; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); [s.sendto(base64.b64decode(p),('fortilens-syslog',1514)) for p in sys.argv[1:]]; s.close()",
                *(base64.b64encode(payload.encode()).decode() for payload in payloads))
        send(first,second)
        def complete():
            report=request(usage,'/api/report?period=today',password)
            return report if report['totals']['upload_bytes']==1300000000 else None
        report=wait_for(complete,180)
        assert report['totals']['download_bytes']==2700000000
        assert len(report['devices'])==2 and {row['vlan']['vlan_id'] for row in report['vlans']}=={100,200}
        assert request(usage,'/api/diagnostics',password)['last_received_event_ns']==stamp
        for period in ('week','month'):
            assert request(usage,'/api/report?period='+period,password)['totals']==report['totals']
        health=receiver_request('/health')
        assert health['ready'] and health['handed_to_tcp']>=2
        assert health['storage_verified'] is False # The accounting result above establishes storage separately.
        counters=receiver_request('/metrics')
        assert 'fortilens_native_syslog_handed_to_tcp_total' in counters
        assert 'fortilens_native_syslog_tcp_send_errors_total' in counters
        sent=time.time_ns();send(first)
        replay_processed(usage,password,sent)
        assert request(usage,'/api/report?period=today',password)['totals']==report['totals']
        docker('restart','fortilens-usage')
        wait_for(lambda:request(usage,'/api/diagnostics',password).get('last_poll_ok'))
        assert request(usage,'/api/report?period=today',password)['totals']==report['totals']
        query='/api/v1/query?'+urllib.parse.urlencode({'query':'up{job="fortilens-syslog"}'})
        wait_for(lambda:any(row['value'][1]=='1' for row in request('http://127.0.0.1:'+str(prom_port),query)['data']['result']),130)
        print(json.dumps({'synthetic_only':True,'native_udp_pipeline':'PASS','source_filter':'fixture-api',
            'upload_bytes':1300000000,'download_bytes':2700000000,'vlans':[100,200],
            'source_eventtime_preserved':True,'disallowed_host_sender':'REJECTED',
            'replay_and_worker_restart':'PASS','native_receiver_scrape':'PASS'},sort_keys=True))
    finally:
        if command:
            subprocess.run([*command,'logs','--tail','12','fortilens-syslog','fortilens-usage','fixture-api'],cwd=root,check=False)
            subprocess.run([*command,'down','--volumes','--remove-orphans'],cwd=root,check=False)


def compact_storage_pipeline(command, root, compose, usage, password, expected, replay):
    """Exercise an explicit copy, container restart and rollback on this lab only."""
    report_path='/api/report?'+urllib.parse.urlencode({'period':'custom',
        'start':expected['start_utc'],'end':expected['end_utc']})
    def docker(*args,**kwargs):
        return subprocess.run([*command,*args],cwd=root,check=True,**kwargs)
    worker=docker('ps','-q','fortilens-usage',capture_output=True,text=True).stdout.strip()
    assert worker
    image=compose['services']['fortilens-usage']['image']
    docker('stop','fortilens-usage')
    oneoff=['docker','run','--rm','--network','none','--volumes-from',worker,
            '--mount','type=bind,src='+str(ROOT/'portable')+',dst=/tools,readonly',
            '--entrypoint','python',image]
    result=subprocess.run([*oneoff,'/tools/accounting_migrate.py','/data/usage.sqlite',
                           '/data/compact-candidate.sqlite'],capture_output=True,text=True,check=True)
    manifest=json.loads(result.stdout)
    assert manifest['destination_format']=='compact' and manifest['verified']['events']['rows']>=2
    # Sidecars belong to their corresponding DB. Keep the original pair intact;
    # never apply this fixture-only switch to a registered installation.
    switch="""from pathlib import Path
p=Path('/data')
for suffix in ('','-wal','-shm'):
 old=p/('usage.sqlite'+suffix)
 if old.exists():old.rename(p/('compact-qa-legacy.sqlite'+suffix))
(p/'compact-candidate.sqlite').rename(p/'usage.sqlite')
"""
    subprocess.run([*oneoff,'-c',switch],check=True)
    docker('start','fortilens-usage')
    wait_for(lambda:request(usage,'/api/diagnostics',password).get('last_poll_ok'))
    assert request(usage,report_path,password)['totals']==expected['totals']
    sent=time.time_ns();replay()
    replay_processed(usage,password,sent)
    assert request(usage,report_path,password)['totals']==expected['totals']
    docker('restart','fortilens-usage')
    wait_for(lambda:request(usage,'/api/diagnostics',password).get('last_poll_ok'))
    assert request(usage,report_path,password)['totals']==expected['totals']
    docker('stop','fortilens-usage')
    rollback="""from pathlib import Path
p=Path('/data')
for suffix in ('','-wal','-shm'):
 current=p/('usage.sqlite'+suffix)
 if current.exists():current.rename(p/('compact-qa-tested.sqlite'+suffix))
 old=p/('compact-qa-legacy.sqlite'+suffix)
 if old.exists():old.rename(p/('usage.sqlite'+suffix))
"""
    subprocess.run([*oneoff,'-c',rollback],check=True)
    docker('start','fortilens-usage')
    wait_for(lambda:request(usage,'/api/diagnostics',password).get('last_poll_ok'))
    assert request(usage,report_path,password)['totals']==expected['totals']
    print(json.dumps({'synthetic_only':True,'compact_storage_copy_replay_restart_rollback':'PASS',
                      'logical_tables_verified':len(manifest['verified'])},sort_keys=True))


def main():
    if sys.platform!='linux':raise SystemExit('This isolated Docker check requires Linux')
    temporary=Path(tempfile.mkdtemp(prefix='fortilens-integration-')).resolve()
    temporary.chmod(0o755) # Only synthetic fixture files are exposed to lab users.
    p=temporary/'installation'
    command=None
    try:
        certificate=temporary/'cert.pem';key=temporary/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(certificate),
            '-days','1','-subj','/CN=fixture-api','-addext','subjectAltName=DNS:fixture-api','-addext','basicConstraints=critical,CA:TRUE'],
            check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        key.chmod(0o644) # Synthetic one-day lab key; nonroot fixture container.
        c=s.validate_config({'site_name':'Synthetic integration','fortigate_host':'fixture-api','api_port':8443,
            'api_ca_file':str(certificate),'api_tls_verify':True,'vdom':'root','sla':'fixture-check',
            'wan_interfaces':['uplink-a'],'confirmed_wan_interfaces':['uplink-a'],
            'grafana_bind':'127.0.0.1','grafana_port':free_port(),'syslog_bind':'127.0.0.1','syslog_port':free_port(),
            'usage_bind':'127.0.0.1','usage_port':free_port(),'accounting_poll_seconds':5,'accounting_settle_seconds':1})
        with contextlib.redirect_stdout(io.StringIO()):
            s.render(c,p,'synthetic-integration-token','synthetic-grafana-password')
            c=s.load(p/'site.json')
            plan=upgrade.prepare(s,p,c,enable_accounting=True,zone='UTC',expected_devid='FG-FIXTURE')
        before_dashboard=(p/'grafana/dashboard.json').read_bytes()
        before_secret=(p/'secrets/grafana_admin_password').read_bytes()
        upgrade.apply(s,p,plan['id'])
        c=s.load(p/'site.json')
        assert before_dashboard==(p/'grafana/dashboard.json').read_bytes()
        assert before_secret==(p/'secrets/grafana_admin_password').read_bytes()
        compose=s.load(p/'compose.yaml')
        paused=alerts.candidates(c)['rules']
        outage=paused['groups'][0]['rules'][0]['data'][0]['model']['expr']
        tests=[]
        for down,up,probe,expected in [(1,1,1,1),(0,1,1,0),(1,0,1,None),(1,1,0,None),(None,1,1,None)]:
            series=[{'series':'up{job="fortigate",instance="fixture-api"}','values':str(up)+'+0x5'},
                    {'series':'probe_success{job="fortigate",instance="fixture-api"}','values':str(probe)+'+0x5'}]
            if down is not None:
                series.append({'series':'fortigate_virtual_wan_status{job="fortigate",instance="fixture-api",vdom="root",sla="fixture-check",interface="uplink-a",state="down"}','values':str(down)+'+0x5'})
            tests.append({'interval':'1m','input_series':series,'promql_expr_test':[{'expr':'sum('+outage+')','eval_time':'1m',
                'exp_samples':[] if expected is None else [{'labels':'{}','value':expected}]}]})
        (temporary/'alert-test.json').write_text(s.dumps({'evaluation_interval':'1m','tests':tests}))
        compose['services']['prometheus']['volumes'].append(str(temporary)+':/fixture:ro')
        (p/'grafana/provisioning/alerting').mkdir()
        (p/'grafana/provisioning/alerting/rules.json').write_text(s.dumps(paused))
        compose['services']['grafana']['volumes'].append('./grafana/provisioning/alerting:/etc/grafana/provisioning/alerting:ro')
        compose['services']['fixture-api']={'image':'fortilens-usage:local','entrypoint':['python','/fixture/fixture_api.py'],
            'volumes':[str(temporary)+':/fixture:ro'],'platform':'linux/amd64'}
        shutil.copyfile(ROOT/'tests/integration/fixture_api.py',temporary/'fixture_api.py')
        for service,port in [('loki',3100),('prometheus',9090)]:
            compose['services'][service]['ports']=[f'127.0.0.1:{free_port()}:{port}']
        (p/'compose.yaml').write_text(s.dumps(compose))
        # The generated setup uses an internal network; only this lab adds these
        # loopback diagnostic ports. Every resource has its unique project name.
        command=s.compose_command(p)
        def docker(*args,**kwargs):return subprocess.run([*command,*args],cwd=p,check=True,**kwargs)
        docker('config','--quiet')
        docker('build','fortilens-usage')
        # Validate the actual pinned applications, not only JSON parsing.
        docker('run','--rm','--no-deps','--entrypoint','promtool','prometheus','check','config','/etc/prometheus/prometheus.yml')
        docker('run','--rm','--no-deps','--entrypoint','promtool','prometheus','test','rules','/fixture/alert-test.json')
        docker('run','--rm','--no-deps','loki','-config.file=/etc/loki/local-config.yaml','-verify-config=true')
        docker('run','--rm','--no-deps','alloy','validate','/etc/alloy/config.alloy')
        docker('up','-d','--no-build')
        grafana=f"http://127.0.0.1:{c['grafana_port']}"
        usage=f"http://127.0.0.1:{c['usage_port']}"
        password=(p/'secrets/usage_admin_password').read_text().strip()
        wait_for(lambda:request(grafana,'/api/health').get('database')=='ok')
        wait_for(lambda:request(usage,'/api/diagnostics',password).get('inventory_available'))
        s.import_dashboard(p,c)
        saved=s.grafana_request(p,c,'/apis/dashboard.grafana.app/v2/namespaces/default/dashboards/forti-monitor')
        assert len(saved['spec']['variables'])==4
        provisioned=s.grafana_request(p,c,'/api/v1/provisioning/alert-rules')
        assert len(provisioned)==5 and all(rule['isPaused'] for rule in provisioned)
        # No SLA samples were fabricated: fixture API exposes metadata only.
        wan=wait_for(lambda:request(usage,'/api/wan?seconds=300',password))
        assert wan['interfaces'][0]['internet_state']=='NO DATA'
        assert wan['route']['interface']=='uplink-a'
        for path in ('/api/report','/api/inventory','/metrics'):
            try:request(usage,path)
            except urllib.error.HTTPError as exc:assert exc.code==401
            else:raise AssertionError('Private reporting endpoint did not require authentication')
        # Send two exact synthetic events with real RFC5424/TCP framing through
        # Alloy -> Loki -> worker. Source eventtime follows inventory collection.
        time.sleep(2)
        now=datetime.now(timezone.utc);stamp=int(now.timestamp()*1e9)
        def line(session,interface,mac,sent,received):
            fields=traffic_fields(stamp,session,interface,mac,sent,received)
            return '<134>1 '+now.isoformat().replace('+00:00','Z')+' fixture FortiGate - - - '+fields+'\n'
        first=line(10,'staff','02:00:00:00:00:01',1000000000,2000000000)
        second=line(11,'guests','02:00:00:00:00:02',300000000,700000000)
        def send(payload):
            with socket.create_connection(('127.0.0.1',c['syslog_port']),timeout=10) as sock:sock.sendall(payload.encode())
        wait_for(lambda:send(first+second) or True,30)
        def complete():
            result=request(usage,'/api/report?period=today',password)
            return result if result['totals']['upload_bytes']==1300000000 else None
        report=wait_for(complete,180)
        assert report['totals']['download_bytes']==2700000000
        assert len(report['devices'])==2 and len(report['vlans'])==2
        assert {x['vlan']['vlan_id'] for x in report['vlans']}=={100,200}
        for period in ('week','month'):
            other=request(usage,'/api/report?period='+period,password)
            assert other['totals']==report['totals']
        device=report['devices'][0]['device_id']
        history=request(usage,'/api/device?period=today&device='+device,password)
        assert history['daily_history'] and history['monthly_history']
        sent=time.time_ns();send(first) # A transport replay with unchanged FortiGate eventtime.
        replay_processed(usage,password,sent)
        assert request(usage,'/api/report?period=today',password)['totals']==report['totals']
        docker('restart','fortilens-usage')
        wait_for(lambda:request(usage,'/api/diagnostics',password).get('last_poll_ok'))
        assert request(usage,'/api/report?period=today',password)['totals']==report['totals']
        metrics=request(usage,'/metrics',password)
        assert 'fortilens_usage_bytes{' in metrics
        prom_port=int(compose['services']['prometheus']['ports'][0].split(':')[1])
        query='/api/v1/query?'+urllib.parse.urlencode({'query':'up{job="fortilens-accounting"}'})
        wait_for(lambda:any(row['value'][1]=='1' for row in request('http://127.0.0.1:'+str(prom_port),query)['data']['result']),100)
        for rule in provisioned:
            expr=rule['data'][0]['model']['expr']
            result=request('http://127.0.0.1:'+str(prom_port),'/api/v1/query?'+urllib.parse.urlencode({'query':expr}))
            assert result['status']=='success'
        # Validate candidates in the real Grafana parser, paused and without a
        # contact point. No external notification is possible in this lab.
        print(json.dumps({'synthetic_only':True,'services':len(compose['services']),'upload_bytes':1300000000,
            'download_bytes':2700000000,'vlans':[100,200],'replay_and_restart':'PASS','authenticated_prometheus_scrape':'PASS',
            'real_fortigate_numeric_verification':'PENDING'},sort_keys=True))
        compact_storage_pipeline(command,p,compose,usage,password,report,lambda:send(first))
        native_udp_pipeline(temporary,certificate)
    finally:
        if command:
            # Only sanitized service diagnostics and fixture-only logs; no
            # production configuration or credentials enter CI output.
            try:print(json.dumps(request(usage,'/api/diagnostics',password),sort_keys=True))
            except (OSError,ValueError,UnboundLocalError):pass
            # Own unique project only. Never use system prune or another folder.
            subprocess.run([*command,'logs','--tail','12','fortilens-usage','fixture-api'],cwd=p,check=False)
            subprocess.run([*command,'down','--volumes','--remove-orphans'],cwd=p,check=False)
        if temporary.parent==Path(tempfile.gettempdir()).resolve() and temporary.name.startswith('fortilens-integration-'):
            shutil.rmtree(temporary)


if __name__=='__main__':main()
