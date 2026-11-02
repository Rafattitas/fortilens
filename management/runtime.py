"""Host-only approved operations. No browser command, image, root or project input."""
import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import http.client
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import yaml
from .common import Problem, atomic, load, report_failure, setup
from .configuration import SERVICES
from .models import Apply, Change, Console, Job, Revision, Settings
import doctor
import interfaces
import portal


def fortigate(settings, token, path, params=None, ca=None):
    """Pinned-DNS, bounded, GET-only API connection; no proxies or redirects."""
    if path not in ('/api/v2/monitor/system/status', '/api/v2/cmdb/system/interface'):
        raise Problem('device_operation_rejected', 'Only supported read-only FortiGate operations are permitted.')
    try:
        addresses = list(dict.fromkeys(row[4][0] for row in socket.getaddrinfo(settings.fortigate_host, settings.api_port, type=socket.SOCK_STREAM)))
        if not addresses or any((ip := ipaddress.ip_address(a)).is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved or (ip.version == 6 and ip.ipv4_mapped and (ip.ipv4_mapped.is_loopback or ip.ipv4_mapped.is_link_local)) for a in addresses):
            raise Problem('device_address_rejected', 'Choose the actual FortiGate address; local, metadata and reserved service addresses are not supported.')
        context = ssl.create_default_context(cadata=ca or None)
        if not settings.api_tls_verify:
            context.check_hostname = False;context.verify_mode = ssl.CERT_NONE
        class Pinned(http.client.HTTPSConnection):
            def connect(self):
                sock = socket.create_connection((addresses[0], settings.api_port), timeout=10)
                self.sock = context.wrap_socket(sock, server_hostname=settings.fortigate_host)
        connection = Pinned(settings.fortigate_host, settings.api_port, timeout=15, context=context)
        try:
            connection.request('GET', path + ('?' + urllib.parse.urlencode(params) if params else ''), headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json'})
            response = connection.getresponse()
            data = response.read(8 * 1024 * 1024 + 1)
            if response.status in (401,403):
                raise Problem('api_authentication_failed' if response.status == 401 else 'api_permission_denied',
                              'Review the API token, read permissions, VDOM scope and Trusted Hosts restrictions.', 400)
            if response.status != 200:
                raise Problem('api_http_failed', 'The read-only API request failed; redirects are not followed.')
            if len(data) > 8 * 1024 * 1024:
                raise Problem('api_response_too_large', 'The interface response exceeded the safe size limit.')
            value = json.loads(data)
            if not isinstance(value, (dict,list)):
                raise ValueError()
            return value
        finally:
            connection.close()
    except Problem:
        raise
    except ssl.SSLError:
        raise Problem('api_tls_failed', 'The FortiGate certificate could not be verified. Review its hostname, validity and custom CA.') from None
    except (OSError, ValueError, http.client.HTTPException):
        raise Problem('api_connection_failed', 'Cannot reach the FortiGate HTTPS API. Review the address, port, routing and API access.') from None


class Docker:
    def __init__(self, configuration):
        self.c = configuration

    def run(self, args, timeout=180):
        # Compose/buildx need writable client state even though systemd makes
        # /root read-only. Keep it inside the helper's private registered root;
        # never broaden the web process or helper's filesystem permissions.
        client=self.c.control/'docker-client';client.mkdir(exist_ok=True,mode=0o700)
        env=os.environ.copy();env.update(DOCKER_CONFIG=str(client),BUILDX_CONFIG=str(client/'buildx'))
        env.pop('DOCKER_HOST',None);env.pop('DOCKER_CONTEXT',None)
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, shell=False,env=env)
        except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
            report_failure('docker_operation_unavailable',exc)
            raise Problem('docker_unavailable', 'Docker is unavailable or the approved operation timed out.') from None
        if result.returncode:
            if 'read-only file system' in result.stderr.lower():
                raise Problem('docker_state_read_only','Docker could not write its client/build state. Review the helper private state directory and systemd permissions.')
            raise Problem('docker_operation_failed', 'Docker rejected the operation. Review service readiness, host permissions, storage and port availability.')
        return result.stdout

    def command(self, file=None, directory=None):
        return ['docker','compose','-p',self.c.project,'--project-directory',str(directory or self.c.root),'-f',str(file or self.c.root/self.c.compose_name)]

    def validate(self, plan):
        folder,_ = self.c.plan(plan)
        # File binds are resolved against candidate files; volume identity still
        # uses the original fixed project. config --quiet never starts a service.
        self.run(self.command(folder/'candidate'/self.c.compose_name,folder/'candidate')+['config','--quiet'], 30)
        self.check_ports(yaml.safe_load((folder/'candidate'/self.c.compose_name).read_text()))

    def check_ports(self, candidate):
        own=set()
        for row in self.statuses():
            if not row.get('id') or row['status']!='running':continue
            info=json.loads(self.run(['docker','inspect',row['id']],15))[0]
            if info.get('Config',{}).get('Labels',{}).get('com.docker.compose.project')!=self.c.project:
                raise Problem('project_mismatch','Port ownership did not match the registered project.')
            for target,bindings in (info.get('NetworkSettings',{}).get('Ports') or {}).items():
                for binding in bindings or []:
                    own.add((row['service'],binding['HostIp'],int(binding['HostPort']),target.split('/')[-1]))
        settings=Settings.model_validate(self.c.state()['settings'])
        enabled,_=enabled_services(settings,candidate)
        checked=set()
        for name,service in candidate['services'].items():
            if name not in enabled:continue
            for binding in service.get('ports',[]):
                if not isinstance(binding,str):
                    raise Problem('custom_ports_review','Register custom published-port syntax on the host before managing it.')
                address,protocol=binding.rsplit('/',1) if '/' in binding else (binding,'tcp')
                parts=address.rsplit(':',2)
                if len(parts)==3:host,port=parts[0],int(parts[1])
                elif len(parts)==2:host,port='0.0.0.0',int(parts[0])
                else:raise Problem('custom_ports_review','Use explicit single-port bindings for managed services.')
                host=host.strip('[]');key=(host,port,protocol)
                if key in checked:raise Problem('port_conflict','Two enabled services request the same published port.')
                checked.add(key)
                if (name,host,port,protocol) in own:continue
                sock=socket.socket(socket.AF_INET6 if ':' in host else socket.AF_INET,socket.SOCK_DGRAM if protocol=='udp' else socket.SOCK_STREAM)
                try:sock.bind((host,port))
                except OSError:raise Problem('port_in_use','A requested monitoring port is already in use. Review service port settings before applying.') from None
                finally:sock.close()

    def statuses(self):
        if not (self.c.root/self.c.compose_name).exists():
            return []
        text = self.run(self.command()+['ps','--all','--format','json'],30).strip()
        try:
            rows = json.loads(text) if text.startswith('[') else [json.loads(x) for x in text.splitlines() if x]
            if not isinstance(rows,list) or any(not isinstance(row,dict) for row in rows):
                raise ValueError('invalid_container_status')
            for row in rows:
                if (not isinstance(row.get('Service'),str) or not isinstance(row.get('State'),str)
                        or not isinstance(row.get('Name',''),str)):
                    raise ValueError('invalid_container_status')
        except ValueError as exc:
            report_failure('docker_status_invalid',exc)
            raise Problem('docker_response_invalid','Docker returned an unreadable service status. Review the local engine.',503) from None
        return [{'service':r.get('Service'), 'status':r.get('State'), 'health':r.get('Health') or None,
                 'id':r.get('ID')} for r in rows if r.get('Service') in SERVICES and '-run-' not in r.get('Name','')]

    def address(self, service, port):
        ids = self.run(self.command()+['ps','--all','-q',service],15).strip().splitlines()
        if len(ids) != 1:
            raise Problem('service_unavailable', 'A required service is not running.')
        try:
            inspected = json.loads(self.run(['docker','inspect',ids[0]],15))
            if not isinstance(inspected,list) or len(inspected)!=1 or not isinstance(inspected[0],dict):
                raise ValueError('invalid_inspection')
            row=inspected[0]
            if not isinstance(row.get('Config'),dict) or not isinstance(row['Config'].get('Labels'),dict):
                raise ValueError('invalid_container_identity')
        except (ValueError,TypeError) as exc:
            report_failure('docker_inspect_invalid',exc)
            raise Problem('docker_response_invalid','Docker returned unreadable service identity. No operation was redirected.',503) from None
        if row.get('Config',{}).get('Labels',{}).get('com.docker.compose.project') != self.c.project:
            raise Problem('project_mismatch', 'Service identity did not match the registered project.')
        networks=row.get('NetworkSettings',{}).get('Networks') if isinstance(row.get('NetworkSettings'),dict) else None
        if not isinstance(networks,dict) or any(not isinstance(v,dict) for v in networks.values()):
            raise Problem('service_network_unavailable','This service uses an unsupported host/network layout.')
        addresses = [v.get('IPAddress') for v in networks.values() if v.get('IPAddress')]
        if any(not isinstance(address,str) for address in addresses):
            raise Problem('service_network_unavailable','This service uses an unsupported host/network layout.')
        if not addresses:
            raise Problem('service_network_unavailable', 'This service uses an unsupported host/network layout.')
        return addresses[0], port

    def http(self, service, port, path, *, method='GET', body=None, headers=None):
        host,port = self.address(service,port)
        conn = http.client.HTTPConnection(host,port,timeout=5)
        try:
            conn.request(method,path,body=body,headers=headers or {})
            r = conn.getresponse();data=r.read(16*1024*1024+1)
            if r.status != 200 or len(data)>16*1024*1024:
                raise Problem('service_api_unavailable', 'A monitoring service did not return a readable bounded response.')
            value=json.loads(data)
            if not isinstance(value,(dict,list)):
                raise ValueError('invalid_service_payload')
            return value
        except (OSError,http.client.HTTPException,ValueError) as exc:
            report_failure('service_request_failed',exc)
            raise Problem('service_api_unavailable','A monitoring service did not return a readable bounded response.',503) from None
        finally:
            conn.close()

    def start(self, enabled, disabled):
        services = self.c.compose()['services']
        # Stop disabled collectors; volumes, images, secrets and definitions stay.
        stopped = [x for x in disabled if x in services]
        if stopped:self.run(self.command()+['stop',*stopped])
        selected = [x for x in enabled if x in services]
        # Always recreate the sidecar when its Grafana namespace is replaced.
        if 'grafana' in selected and 'fortilens-portal' in services:
            self.run(self.command()+['stop','fortilens-portal'])
        self.run(self.command()+['up','-d','--no-deps','--build','--force-recreate',*selected],600)
        if 'grafana' in selected and 'fortilens-portal' in selected:
            self.run(self.command()+['up','-d','--no-deps','--force-recreate','fortilens-portal'],120)

    def ready(self, enabled):
        last = []
        for _ in range(45):
            last = self.statuses()
            byname = {r['service']:r for r in last}
            running = all(byname.get(n,{}).get('status') == 'running' and byname[n]['health'] not in ('unhealthy','starting') for n in enabled if n in self.c.compose()['services'])
            if running:
                try:
                    self.http('grafana',3000,'/grafana/api/health' if self.c.compose()['services']['grafana'].get('environment',{}).get('GF_SERVER_SERVE_FROM_SUB_PATH') == 'true' else '/api/health')
                    self.http('prometheus',9090,'/api/v1/query?query=up')
                    # Loki ready is text, so its query API confirms readiness here.
                    self.http('loki',3100,'/loki/api/v1/query?'+urllib.parse.urlencode({'query':'sum(count_over_time({job="fortigate-syslog"}[1m]))'}))
                    return last
                except (Problem,OSError,ValueError,http.client.HTTPException):pass
            time.sleep(2)
        raise Problem('readiness_failed', 'A required service did not become ready. Configuration rollback is available.')


def enabled_services(settings, compose):
    f=settings.monitoring
    active=['prometheus','grafana','loki']
    if f.wan or f.sla or f.interfaces:active.append('fortigate-exporter')
    if f.server:active.append('node-exporter')
    if f.syslog:active.append('alloy')
    if f.accounting:active.append('fortilens-usage')
    if 'fortilens-portal' in compose['services']:active.append('fortilens-portal')
    return active,[n for n in SERVICES if n not in active]


class Operations:
    def __init__(self, configuration, docker=None, device_get=fortigate, dashboard_uid='forti-monitor'):
        self.c=configuration;self.docker=docker or Docker(configuration);self.device_get=device_get
        self.jobs={};self.jobs_lock=threading.Lock();self.busy=threading.Lock();self.executor=ThreadPoolExecutor(max_workers=1)
        self.gateway_cookie=''
        self.gateway_lock=threading.Lock()
        if not interfaces.NAME.fullmatch(dashboard_uid):raise ValueError('Invalid registered dashboard UID')
        self.dashboard_uid=dashboard_uid
        for p in (self.c.control/'jobs').glob('*.json'):
            try:
                Job(job_id=p.stem)
                job=self.read_job(p.stem)
            except (Problem,ValueError) as exc:
                report_failure('job_recovery_unavailable',exc)
                continue
            if job.get('status')=='starting':
                self._safe_progress(job,'failed','The helper restarted during deployment. Review a controlled configuration rollback.',status='failed',rollback_available=True)

    def dispatch(self, operation, data):
        if operation in ('view','status','diagnostics') and data:
            raise Problem('unexpected_fields','This operation accepts no additional fields.')
        if operation=='view':return self.c.view()
        if operation=='status':return self.status()
        if operation=='diagnostics':return self.diagnostics()
        if operation=='save':
            if self.busy.locked():raise Problem('deployment_busy','A deployment is running. Wait before saving.',409)
            return self.c.save(data)
        if operation=='prepare':
            rev=Revision.model_validate(data)
            if self.busy.locked():raise Problem('deployment_busy','A deployment is already running.',409)
            plan=self.c.prepare(rev.revision);self.docker.validate(plan['plan_id']);return plan
        if operation in ('test','discover'):
            Revision.model_validate(data)
            try:
                result=self.device(operation,data['revision'])
                if operation=='test':atomic(self.c.control/'connection-test.json',{'at':time.time(),'scope':self.inventory_scope(Settings.model_validate(self.c.state()['settings'])),'result':result})
                return result
            except Problem as e:
                if operation=='test':atomic(self.c.control/'connection-test.json',{'at':time.time(),'revision':data['revision'],'result':{'status':'failed','message':e.message,'code':e.code}})
                raise
        if operation=='apply':return self.apply(Apply.model_validate(data))
        if operation=='rollback':return self.rollback(Apply.model_validate(data))
        if operation=='job':
            request=Job.model_validate(data)
            return self.read_job(request.job_id)
        if operation=='console':return self.console(Console.model_validate(data))
        raise Problem('operation_rejected','This management operation is not supported.',403)

    def credentials(self, settings):
        pending=load(self.c.secret_path,{})
        baseline=load(self.c.root/self.c.state()['site_file'],{})
        ca=pending.get('ca')
        if ca is None and baseline.get('api_ca_file'):
            from .common import guarded
            ca=guarded(self.c.root,baseline['api_ca_file']).read_text()
        return self.c.token(),ca

    def inventory_scope(self,settings):
        token,ca=self.credentials(settings)
        fields=[settings.fortigate_host,settings.api_port,settings.vdom,settings.api_tls_verify,hashlib.sha256(token.encode()).hexdigest(),hashlib.sha256((ca or '').encode()).hexdigest()]
        return hashlib.sha256(json.dumps(fields).encode()).hexdigest()

    def device(self, operation, revision):
        state=self.c.state()
        if revision!=state['revision']:raise Problem('revision_conflict','Save and use the latest settings revision.',409)
        s=Settings.model_validate(state['settings']);token,ca=self.credentials(s)
        if not token:raise Problem('token_required','Save the FortiGate API token first.')
        if operation=='test':
            data=self.device_get(s,token,'/api/v2/monitor/system/status',{'vdom':s.vdom},ca)
            if not isinstance(data,dict) or data.get('status')!='success':raise Problem('api_status_unconfirmed','The API did not confirm a successful read-only request.')
            # Never return arbitrary device payloads or echo a token in a name.
            info={}
            for key in ('version','serial','model'):
                details=data.get('results') if isinstance(data.get('results'),dict) else {}
                value=data.get(key,details.get(key))
                if isinstance(value,str) and len(value)<=120 and token not in value:info[key]=value
            if s.expected_devid and info.get('serial') and info['serial']!=s.expected_devid:
                raise Problem('device_identity_mismatch','The API device ID differs from the configured accounting source. Review the address and expected device ID.')
            return {'status':'success','tls_verified':s.api_tls_verify,'device':info,'message':'Read-only system/status succeeded. Interface and SLA permissions may differ.'}
        metadata={}
        def getter(start):
            try:
                data=self.device_get(s,token,'/api/v2/cmdb/system/interface',{'vdom':s.vdom,'format':'name|vdom|type|role|interface|vlanid|ip|status','count':500,'start':start},ca)
                for envelope in data if isinstance(data,list) else [data]:
                    for raw in envelope.get('results',[]):
                        if isinstance(raw,dict):metadata[(envelope.get('vdom',s.vdom),raw.get('name'))]=raw
                return data
            except Problem as e:raise interfaces.InventoryError(e.code,e.message) from None
        result=interfaces.collect(s.portable(),token,api_get=getter)
        for row in result['interfaces']:
            raw=metadata.get((row['vdom'],row['name']),{});address=None
            try:
                if isinstance(raw.get('ip'),str):address=str(ipaddress.ip_interface(raw['ip']))
            except ValueError:pass
            row.update(ip_address=address,status='Configured enabled' if raw.get('status')=='up' else 'Configured disabled' if raw.get('status')=='down' else 'Unknown',discovery_confidence=row.get('review_status','Review required'))
        result['interfaces']=[row for row in result['interfaces'] if token not in json.dumps(row)]
        atomic(self.c.control/'inventory.json',{'scope':self.inventory_scope(s),'report':result})
        return result

    def status(self):
        try:
            usage=shutil.disk_usage(self.c.control)
            disk={'total':usage.total,'used':usage.used,'available':usage.free}
        except OSError as exc:
            report_failure('storage_probe_failed',exc)
            disk={'total':None,'used':None,'available':None}
        try:
            self.docker.run(['docker','version','--format','{{.Server.Version}}'],5)
            services=self.docker.statuses();docker='Available'
        except (Problem,OSError,ValueError):services=[];docker='Unavailable'
        ram=None
        try:ram=os.sysconf('SC_PAGE_SIZE')*os.sysconf('SC_PHYS_PAGES')
        except (ValueError,AttributeError,OSError):pass
        if self.c.compose():
            _,disabled=enabled_services(Settings.model_validate(self.c.state()['settings']),self.c.compose())
            for row in services:
                if row['service'] in disabled:row.update(status='disabled',health=None)
        identities={p.stem for p in (self.c.control/'jobs').glob('*.json')}
        with self.jobs_lock:identities.update(self.jobs)
        jobs=[]
        for identity in identities:
            try:
                Job(job_id=identity)
                jobs.append(self.read_job(identity))
            except (Problem,ValueError) as exc:report_failure('job_status_unavailable',exc)
        def last_event(job):
            event=(job.get('events') or [{}])[-1]
            stamp=event.get('at',0) if isinstance(event,dict) else 0
            return stamp if isinstance(stamp,(int,float)) else 0
        latest=max(jobs,key=last_event) if jobs else None
        return {'hostname':socket.gethostname(),'os':platform.platform(),'architecture':platform.machine(),'cpu_count':os.cpu_count(),'ram_bytes':ram,
                'disk':disk,'docker':docker,'services':services,
                'installation':{'configured':self.c.state()['configured'],'project':self.c.project},
                'storage_projection':self.storage(),'latest_job':latest,
                'links':{'console':'fortilens-portal' in (self.c.compose() or {'services':{}})['services'],
                         'dashboard':'/grafana/d/'+self.dashboard_uid+'/?kiosk&theme=dark',
                         'grafana_port':load(self.c.root/self.c.state()['site_file'],{}).get('grafana_port',3001),
                         'grafana_prefix':'/grafana/' if 'fortilens-portal' in (self.c.compose() or {'services':{}})['services'] else '/'}}

    def storage(self):
        # Plan from observed service growth only. Counter resets/compaction or
        # absent data cannot become a made-up zero-byte storage forecast.
        result={'available':False,'message':'Insufficient data for storage estimation','components':{}}
        now=time.time()
        try:
            samples=load(self.c.control/'storage-samples.json',{})
            if not isinstance(samples,dict):raise ValueError('invalid_storage_history')
        except (OSError,ValueError) as exc:
            report_failure('storage_history_unavailable',exc)
            return result
        for service in ('loki','prometheus','fortilens-usage'):
            try:
                ids=self.docker.run(self.docker.command()+['ps','--all','-q',service],10).strip().splitlines()
                if len(ids)!=1:continue
                item=json.loads(self.docker.run(['docker','inspect',ids[0]],10))[0]
                if item['Config']['Labels'].get('com.docker.compose.project')!=self.c.project:continue
                targets=('/loki','/tmp/loki') if service=='loki' else ('/prometheus',) if service=='prometheus' else ('/data',)
                mounts=[m for m in item['Mounts'] if m['Destination'] in targets and m.get('Type')=='volume']
                if len(mounts)!=1:continue
                path=Path(mounts[0]['Source']);total=0;count=0;deadline=time.monotonic()+.25
                for base,dirs,files in os.walk(path,followlinks=False):
                    dirs[:]=[d for d in dirs if not (Path(base)/d).is_symlink()]
                    for name in files:
                        p=Path(base)/name
                        if not p.is_symlink():total+=p.stat().st_size
                        count+=1
                    if count>100000 or time.monotonic()>deadline:raise ValueError()
                result['components'][service]={'bytes':total,'complete_measurement':True}
                old=samples.get(service)
                if old and now-old['at']>=300 and total>old['bytes']:
                    days=self.c.state()['settings']['retention']['loki_days' if service=='loki' else 'prometheus_days' if service=='prometheus' else 'summary_days']
                    estimate=int((total-old['bytes'])/(now-old['at'])*86400*days)
                    result['components'][service]['projection_bytes']=estimate
                    result.update(available=True,message='Planning estimate from measured filesystem growth. Compaction, compression and future traffic may change actual use.')
                if not old or now-old['at']>=300:samples[service]={'at':now,'bytes':total}
            except (Problem,OSError,ValueError,KeyError,TypeError):continue
        if result['available']:
            try:
                if sum(x.get('projection_bytes',0) for x in result['components'].values())>shutil.disk_usage(self.c.control).free:
                    result['message']+=' Warning: projected growth exceeds currently available storage.'
            except OSError as exc:report_failure('storage_probe_failed',exc)
        try:atomic(self.c.control/'storage-samples.json',samples)
        except (OSError,ValueError) as exc:
            report_failure('storage_history_write_failed',exc)
            result['message']+=' The latest storage sample could not be saved; review host storage.'
        return result

    def diagnostics(self):
        state=self.c.state();settings=Settings.model_validate(state['settings']);c=settings.portable(load(self.c.root/state['site_file'],{}))
        def raw_request(p,config,path,method='GET',body=None,timeout=15):
            if path=='/api/health':return self.docker.http('grafana',3000,('/grafana' if 'fortilens-portal' in (self.c.compose() or {'services':{}})['services'] else '')+'/api/health')
            for uid,service,port in ((setup.PROM_UID,'prometheus',9090),(setup.LOKI_UID,'loki',3100)):
                if path==f'/api/datasources/uid/{uid}/health':
                    self.docker.http(service,port,'/api/v1/query?query=up' if service=='prometheus' else '/loki/api/v1/query?'+urllib.parse.urlencode({'query':'sum(count_over_time({job="fortigate-syslog"}[1m]))'}))
                    return {'status':'OK'}
            if '/api/v1/query?' in path:return self.docker.http('prometheus',9090,'/api/v1/query?'+path.split('?',1)[1])
            if path=='/api/ds/query':
                query=body['queries'][0];kind=query['datasource']['type']
                native=self.docker.http('prometheus' if kind=='prometheus' else 'loki',9090 if kind=='prometheus' else 3100,
                    ('/api/v1/query?' if kind=='prometheus' else '/loki/api/v1/query?')+urllib.parse.urlencode({'query':query['expr']}))
                values=[float(row['value'][1]) for row in native.get('data',{}).get('result',[])]
                return {'results':{'A':{'frames':[{'schema':{'fields':[{'type':'number'}]},'data':{'values':[values]}}]}}}
            raise ValueError('unsupported_diagnostic')
        def request(*args,**kwargs):
            try:return raw_request(*args,**kwargs)
            except Problem:raise ValueError('monitoring_service_unavailable') from None
        try:
            # doctor is the same callable used by setup.py doctor --json; its
            # subprocess-based status reader is replaced with approved helper data.
            rows=self.docker.statuses()
            checks=[doctor.check('service:'+r['service'],'PASS' if r['status']=='running' else 'WARN',r['service']+': '+str(r['status']),'service_state') for r in rows]
            report=doctor.run(self.c.root,c,request,lambda *a:[],setup.PROM_UID,setup.LOKI_UID,
                              service_reader=lambda *a,**k:checks,usage_request=lambda *a:self.accounting_diagnostics(c))
            previous=load(self.c.control/'connection-test.json',{})
            recent=previous and time.time()-previous['at']<300
            same=recent and previous.get('scope')==self.inventory_scope(settings)
            test=previous.get('result',{}) if same or recent and previous.get('revision')==state['revision'] else {}
            report['checks'].append(doctor.check('direct_api','PASS' if same and test.get('status')=='success' else 'FAIL' if test.get('status')=='failed' else 'UNKNOWN',
                test.get('message','A recent direct API test has not been established.'),'last_read_only_api_test',
                'Use Settings > FortiGate Connection > Test Connection. A system/status read does not verify all API permissions.'))
            report['status']=next((v for v in ('FAIL','WARN','UNKNOWN') if any(x['status']==v for x in report['checks'])),'PASS')
        except (Problem,OSError,ValueError,TypeError):
            report={'status':'UNKNOWN','checks':[{'id':'monitoring','status':'UNKNOWN','message':'Monitoring diagnostics are unavailable. The management portal is still running.','action':'Review service status, Docker permissions and disk capacity.'}]}
        collection={'latest_log_at':None,'latest_metric_at':None,'processing_lag_seconds':None,
                    'log_timestamp_kind':'receipt' if c.get('loki_receive_timestamps') else 'source or unconfirmed'}
        for check in report.get('checks',[]):
            age=check.get('evidence',{}).get('newest_sample_age_seconds')
            if isinstance(age,(int,float)):collection['latest_metric_at']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(time.time()-age))
        try:
            now=time.time()
            result=self.docker.http('loki',3100,'/loki/api/v1/query_range?'+urllib.parse.urlencode({'query':'{job="fortigate-syslog"}','start':str(int((now-86400)*1e9)),'end':str(int(now*1e9)),'direction':'backward','limit':1}))
            stamps=[int(v[0])/1e9 for row in result.get('data',{}).get('result',[]) for v in row.get('values',[])]
            if stamps:collection['latest_log_at']=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(max(stamps)))
        except (Problem,OSError,ValueError,KeyError,TypeError):pass
        if settings.monitoring.accounting:
            try:collection['processing_lag_seconds']=self.accounting_diagnostics(c).get('processing_lag_seconds')
            except (Problem,OSError,ValueError,TypeError):pass
        return {'monitoring':report,'system':self.status(),'collection':collection,'read_only':True}

    def accounting_diagnostics(self,c):
        compose=self.c.compose() or {}
        source=compose.get('secrets',{}).get('usage_admin_password',{}).get('file')
        if not source:raise ValueError('report_secret_unavailable')
        from .common import guarded
        path=Path(source);path=path if path.is_absolute() else self.c.root/path
        password=guarded(self.c.root,path.relative_to(self.c.root)).read_text().strip()
        auth='Basic '+base64.b64encode(('admin:'+password).encode()).decode()
        try:return self.docker.http('fortilens-usage',8080,'/api/diagnostics',headers={'Authorization':auth})
        except Problem:raise ValueError('accounting_service_unavailable') from None

    def read_job(self,identity):
        # The current process knows the newest state even when disk-full prevents
        # recording it. The bounded cache is not a durable recovery guarantee.
        with self.jobs_lock:
            cached=self.jobs.get(identity)
            if cached is not None:return deepcopy(cached)
        try:job=load(self.c.control/'jobs'/(identity+'.json'))
        except (OSError,ValueError) as exc:
            report_failure('job_read_failed',exc)
            raise Problem('job_state_unavailable','Deployment history is unavailable. Review host storage and service logs.',503) from None
        if not job:raise Problem('job_missing','Deployment job not found.',404)
        if not isinstance(job,dict) or job.get('job_id')!=identity or not isinstance(job.get('events',[]),list):
            raise Problem('job_state_invalid','Deployment history needs host-side review. Check Settings and the registered installation before retrying.',503)
        return job

    def _progress(self,job,phase,message,**values):
        job.update(phase=phase,message=message,**values)
        job.setdefault('events',[]).append({'at':time.time(),'phase':phase,'message':message})
        with self.jobs_lock:
            self.jobs[job['job_id']]=deepcopy(job)
            while len(self.jobs)>128:self.jobs.pop(next(iter(self.jobs)))
        atomic(self.c.control/'jobs'/(job['job_id']+'.json'),job)

    def _safe_progress(self,job,phase,message,**values):
        try:self._progress(job,phase,message,**values)
        except (OSError,ValueError,TypeError,Problem) as exc:report_failure('job_write_failed',exc)

    def _failed_job(self,job,message,**values):
        self._safe_progress(job,'failed',message,status='failed',**values)
        try:self.c.audit('deployment_failed',outcome='failed')
        except (OSError,ValueError,Problem) as exc:report_failure('deployment_audit_failed',exc)

    def apply(self,request):
        if not request.confirm:raise Problem('confirmation_required','Review the candidate before applying it.')
        if not self.busy.acquire(blocking=False):raise Problem('deployment_busy','A deployment is already running.',409)
        identity=os.urandom(16).hex();job={'job_id':identity,'plan_id':request.plan_id,'status':'starting','phase':'queued','events':[]}
        queued=False
        try:
            self._progress(job,'queued','Preparing the approved configuration.')
            self.executor.submit(self._deploy,request,job);queued=True
        except (OSError,ValueError,RuntimeError,Problem) as exc:
            report_failure('deployment_queue_failed',exc)
            self._failed_job(job,'Deployment could not be queued. No services were started.')
            raise Problem('deployment_queue_failed','Deployment could not be queued. Review host storage and the helper service.',503) from None
        finally:
            if not queued:self.busy.release()
        return job

    def _deploy(self,request,job):
        installed=False;install_attempted=False
        try:
            with self.c.lock:
                if request.revision!=self.c.state()['revision']:raise Problem('revision_conflict','The saved configuration revision changed.',409)
                self._progress(job,'validating','Validating Docker Compose and reviewed file fingerprints.')
                self.docker.validate(request.plan_id)
                folder,plan=self.c.plan(request.plan_id)
                previous=plan['previous'].get('applied') or {}
                changed_scope=any(previous.get(k)!=plan['settings'][k] for k in ('wan_interfaces','fortigate_host','api_port','vdom','api_tls_verify'))
                if plan['settings']['wan_interfaces'] and (plan['fresh'] or changed_scope):
                    inventory=load(self.c.control/'inventory.json')
                    if not inventory or inventory.get('scope')!=self.inventory_scope(Settings.model_validate(plan['settings'])):
                        raise Problem('discovery_required','Discover interfaces for this saved revision before deployment.')
                    interfaces.selected_names(inventory['report'],plan['settings']['wan_interfaces'],plan['settings']['vdom'])
                self._progress(job,'backup','Saving a private configuration backup; monitoring data is not copied or migrated.')
                install_attempted=True
                self.c.install_files(request.plan_id);installed=True
                settings=Settings.model_validate(plan['settings'])
                enabled,disabled=enabled_services(settings,self.c.compose())
                self._progress(job,'starting','Starting enabled services and retaining all data volumes.')
                self.docker.start(enabled,disabled)
                self._progress(job,'readiness','Waiting for monitoring service readiness.')
                services=self.docker.ready(enabled)
                if plan['fresh']:
                    c=setup.validate_config(load(self.c.root/self.c.state()['site_file']))
                    setup.import_dashboard(self.c.root,c)
                    if c.get('portal_enabled'):setup.connect_portal(self.c.root,c)
                self._progress(job,'diagnostics','Checking collection evidence. Missing FortiGate data is not a deployment failure.')
                diagnostic=self.diagnostics()
                self.c.complete(request.plan_id)
                self._safe_progress(job,'complete','Configuration applied. Review any collection warnings in Diagnostics.',status='complete',services=services,diagnostics=diagnostic)
        except Exception as e:
            report_failure('deployment_failed',e)
            restored=False
            # install_files can fail after replacing only some files. Its
            # durable APPLYING marker is written before the first target write.
            if install_attempted and not installed:
                try:
                    _,current=self.c.plan(request.plan_id)
                    installed=current.get('status') in ('APPLYING','APPLIED','FAILED')
                except (Problem,OSError,ValueError):pass
            if installed:
                try:self.c.restore(request.plan_id);restored=True
                except (Problem,OSError,ValueError) as exc:report_failure('configuration_restore_failed',exc)
            message=e.message if isinstance(e,Problem) else 'Deployment could not be completed. Review the recorded step, configuration recovery and service status before retrying. Monitoring data was not deleted.'
            self._failed_job(job,message,failed_step=job['phase'],configuration_restored=restored,rollback_available=installed)
        finally:self.busy.release()

    def rollback(self,request):
        if not request.confirm:raise Problem('confirmation_required','Confirm the configuration rollback.')
        if request.revision!=self.c.state()['revision']:raise Problem('revision_conflict','Reload the latest configuration before choosing rollback.',409)
        if not self.busy.acquire(blocking=False):raise Problem('deployment_busy','Wait for the running operation.',409)
        identity=os.urandom(16).hex();job={'job_id':identity,'plan_id':request.plan_id,'status':'starting','events':[]}
        def run():
            try:
                _,plan=self.c.plan(request.plan_id)
                if plan['status']!='ROLLED_BACK':self.c.restore(request.plan_id)
                if not plan['previous']['configured']:
                    # No original stack exists on a failed first install: stop
                    # only this registered project's services; retain all files/data.
                    self.docker.run(self.docker.command()+['stop',*[n for n in SERVICES if n in self.c.compose()['services']]])
                    self._safe_progress(job,'complete','Fresh-install services stopped; all data retained.',status='complete')
                    return
                s=Settings.model_validate(plan['previous']['settings']);enabled,disabled=enabled_services(s,self.c.compose())
                self.docker.start(enabled,disabled);self.docker.ready(enabled)
                self._safe_progress(job,'complete','Previous configuration restored; no application-data rollback was attempted.',status='complete')
            except Exception as e:
                report_failure('rollback_failed',e)
                self._failed_job(job,e.message if isinstance(e,Problem) else 'Rollback could not be completed. Retained data was not deleted; review diagnostics.')
            finally:self.busy.release()
        queued=False
        try:
            self._progress(job,'rollback','Restoring the previous configuration. Data volumes remain untouched.')
            self.executor.submit(run);queued=True
        except (OSError,ValueError,RuntimeError,Problem) as exc:
            report_failure('rollback_queue_failed',exc)
            self._failed_job(job,'Rollback could not be queued. No services were restarted.')
            raise Problem('rollback_queue_failed','Rollback could not be queued. Review host storage and the helper service.',503) from None
        finally:
            if not queued:self.busy.release()
        return job

    def console(self,request):
        # Existing unified console retains its own auth boundary. The helper
        # signs in server-side and never sends that credential/cookie to the UI.
        parsed=urllib.parse.urlsplit(request.path)
        decoded=urllib.parse.unquote(parsed.path)
        if parsed.scheme or parsed.netloc or '..' in decoded.split('/') or '\\' in decoded or any(ord(c)<32 for c in decoded):
            raise Problem('console_path_rejected','Invalid console resource.')
        allowed=parsed.path in ('/','/api/portal','/api/session','/api/monitoring-status',*portal.REPORT_ROUTES) or parsed.path.startswith('/grafana/')
        if not allowed or request.method=='POST' and not portal.query_post(parsed.path):
            raise Problem('console_operation_rejected','Only existing read-only console operations are proxied.',403)
        c=load(self.c.root/self.c.state()['site_file'],{})
        if 'fortilens-portal' not in (self.c.compose() or {'services':{}})['services']:
            raise Problem('console_not_integrated','This deployment retains its native Grafana/report login. Use the existing service links.',503)
        source=self.c.compose()['secrets']['usage_admin_password']['file'];path=Path(source)
        from .common import guarded
        password=guarded(self.c.root,path.relative_to(self.c.root) if path.is_absolute() else path).read_text().strip()
        def connect():return http.client.HTTPConnection('127.0.0.1',c.get('usage_port',3002),timeout=30)
        try:
            for attempt in range(2):
                with self.gateway_lock:
                    if not self.gateway_cookie:
                        conn=connect()
                        try:
                            conn.request('POST','/api/session',json.dumps({'username':'admin','password':password}),{'X-FortiLens-Request':'1','Content-Type':'application/json'})
                            response=conn.getresponse();response.read(65536)
                            if response.status!=200:raise Problem('console_signin_failed','The protected monitoring console is not ready.',503)
                            self.gateway_cookie=response.getheader('Set-Cookie','').split(';')[0]
                        finally:conn.close()
                    cookie=self.gateway_cookie
                conn=connect()
                try:
                    headers={'Cookie':cookie,'Content-Type':request.content_type,'Accept-Encoding':'identity','Origin':f"http://127.0.0.1:{c.get('usage_port',3002)}"}
                    conn.request(request.method,request.path,base64.b64decode(request.body) if request.body else None,headers)
                    response=conn.getresponse();data=response.read(32*1024*1024+1)
                    if response.status==401 and not attempt:
                        with self.gateway_lock:
                            if self.gateway_cookie==cookie:self.gateway_cookie=''
                        continue
                    if len(data)>32*1024*1024:raise Problem('console_too_large','This console resource exceeded the proxy limit.')
                    return {'status':response.status,'content_type':response.getheader('Content-Type','application/octet-stream'),
                            'location':response.getheader('Location'),'body':base64.b64encode(data).decode()}
                finally:conn.close()
        except (OSError,http.client.HTTPException):raise Problem('console_unavailable','Monitoring is unavailable; Settings and Diagnostics remain accessible.',503) from None
